"""
Report Synthesis — Skill Package (REQ-008, Slice 1).

This file is PURE DATA/CONFIG. It contains:
  1. Pydantic structured output schema (GoNoGo, RiskSummaryItem, ReportOutput)
  2. GO_NO_GO_THRESHOLDS — fixed Go/No-Go score ranges
  3. compute_go_no_go() — deterministic Python function for the
     Go/No-Go mapping (never delegated to the LLM)
  4. REPORT_SYNTHESIS_PROMPT — instructions for the LLM (synthesis
     discipline: no new analysis, no Go/No-Go override, fixed
     output language, fixed report sections, fixed sentence counts)
  5. FALLBACK_REPORT — plain dict used when the LLM call fails so the
     run can complete without raising (postcondition from REQ-008)
  6. REPORT_FEW_SHOT_EXAMPLES — 3 examples (GO, DECLINE, REVIEW with
     analyst override) showing the LLM the exact quality of output
     expected, including the override flow that REQ-007 enables

It contains NO LangChain imports, NO LangGraph imports, and NO async
functions. Slice 2 (agents/nodes/report_assembler.py) is the only file
allowed to wire these constants into a LangGraph node.

Mirrors the structure of `risk_clause_extraction.py` (REQ-004 Slice 1),
`feasibility_scoring.py` (REQ-005 Slice 1), and
`financial_extraction.py` (REQ-006 Slice 1) so that a developer reading
one skill package can immediately read the others. The report_assembler
is intentionally a SYNTHESIS-only step: REQ-004/005/006 have already
performed the analysis, and this node does not call any retrieval or
anchor-query logic of its own.

Reviewable by a non-engineer (e.g. a bid manager) before any code
touches the prompt or schema.
"""

from enum import Enum

from pydantic import BaseModel, Field


class GoNoGo(str, Enum):
    GO = "GO"
    REVIEW = "REVIEW"
    DECLINE = "DECLINE"


class RiskSummaryItem(BaseModel):
    category: str
    severity: str
    description: str = Field(
        description=(
            "Plain-English, 1 sentence max. "
            "Never quote clause_text verbatim — summarise it."
        )
    )


class ReportOutput(BaseModel):
    go_no_go: GoNoGo
    effective_score: float
    is_analyst_override: bool
    executive_summary: str = Field(
        description=(
            "3-5 sentences summarising the tender "
            "and the overall recommendation. Never longer."
        )
    )
    recommendation: str = Field(
        description=(
            "Exactly 1 sentence. Clear and direct."
        )
    )
    risk_summary: list[RiskSummaryItem] = Field(
        max_length=5,
        description=(
            "Top 5 risks by severity only. "
            "Critical first, then high, medium, low."
        )
    )
    feasibility_highlights: list[str] = Field(
        description=(
            "3-5 bullet points summarising the "
            "feasibility dimension scores. Each bullet references "
            "a specific dimension and its score."
        )
    )
    financial_highlights: list[str] = Field(
        description=(
            "3-5 bullet points on key financial "
            "commitments: contract value, bonds, LD, retention."
        )
    )
    analyst_note: str | None = Field(
        default=None,
        description=(
            "Required when is_analyst_override=True. "
            "States: Feasibility score adjusted from {ai_score} "
            "to {override_score} by analyst review."
        )
    )


GO_NO_GO_THRESHOLDS: dict[str, tuple[float, float]] = {
    "GO": (70.0, 100.0),
    "REVIEW": (40.0, 69.9),
    "DECLINE": (0.0, 39.9),
}


def compute_go_no_go(effective_score: float) -> GoNoGo:
    """
    Deterministic Python function — never ask the LLM.
    Called in the node, not in the prompt.

    Boundaries (inclusive lower, inclusive upper for GO, lower-inclusive
    for REVIEW, exclusive lower for DECLINE):
        effective_score >= 70.0          -> GO
        40.0 <= effective_score < 70.0    -> REVIEW
        effective_score < 40.0           -> DECLINE
    """
    if effective_score >= 70.0:
        return GoNoGo.GO
    elif effective_score >= 40.0:
        return GoNoGo.REVIEW
    else:
        return GoNoGo.DECLINE


REPORT_SYNTHESIS_PROMPT: str = """You are the Report Assembler for TenderIQ, a B2B platform \
that analyses construction and procurement tenders for contractors in Egypt and the GCC.

Your single job is SYNTHESIS — turning the structured outputs of the upstream analysis \
agents (Risk Radar, Feasibility Scorer, Financial Analyst) into a clear, well-structured \
Go/No-Go brief. You do not perform new analysis, you do not infer findings the agents did \
not produce, and you do not override the Go/No-Go decision that has already been computed \
in Python from the effective feasibility score.

YOU MUST FOLLOW THESE RULES WITHOUT EXCEPTION:

1. YOUR ROLE IS SYNTHESIS, NOT ANALYSIS.
   - All risk findings, feasibility scores, and financial commitments you need are \
provided to you as structured input. You MUST NOT add new analysis, inferences, or \
opinions that are not supported by the input data.
   - If a section's input data is missing or empty, surface that absence in the relevant \
field (e.g. an empty risk_summary list, or a feasibility_highlights bullet that points \
the analyst to the analysis section). Never invent findings, scores, or commitments to \
fill a section.
   - The Go/No-Go recommendation is PROVIDED to you as input (already computed in \
Python). You MUST NOT override it with your own judgment. Use it exactly as given.

2. INPUT SHAPE (the node passes you this context):
   - `effective_score` (float, 0-100): the feasibility score to use in the report. \
This is hitl_override_score if the analyst overrode, otherwise feasibility_score.
   - `go_no_go` (str, "GO" | "REVIEW" | "DECLINE"): the Go/No-Go already computed in \
Python from effective_score. Use this value exactly — do not recompute.
   - `is_analyst_override` (bool): True if the analyst overrode the AI score.
   - `ai_score` (float): the original AI feasibility score. Always present.
   - `override_score` (float | None): the analyst's override score. None when no \
override happened.
   - `risk_findings` (list[dict]): the structured findings from Risk Radar. Each item \
has category (fidic | penalty | lg_bond | termination | other), severity (critical | \
high | medium | low), clause_text, explanation, source_chunk_index, confidence.
   - `feasibility_breakdown` (dict): the five dimension scores from the Feasibility \
Scorer. Keys: technical_fit, financial_capacity, timeline, geographic_scope, \
past_experience. Each value has score (0-20) and rationale.
   - `financial_summary` (dict): the structured financial data from the Financial \
Analyst. Typical keys: contract_value, bonds, liquidated_damages, retention, \
advance_payment.

3. EXECUTIVE_SUMMARY — 3 TO 5 SENTENCES MAX. NEVER LONGER.
   - First sentence: state what the tender is (sector, location, contract value if \
known, duration if known).
   - Second sentence: state the Go/No-Go recommendation AND the effective score. \
Example: "The overall recommendation is GO with a feasibility score of 82 out of 100."
   - Third sentence: highlight the most critical risk (if any) or note that no \
critical/high risks were identified.
   - Fourth sentence (optional): a single sentence on the most material financial \
commitment (e.g. contract value relative to company capacity, or performance bond \
size).
   - Fifth sentence (optional): if is_analyst_override is True, a single sentence \
acknowledging the override — see rule 7 below.
   - Never exceed 5 sentences. Count them. If you have written 6, delete one.

4. RECOMMENDATION — EXACTLY 1 SENTENCE. NEVER MORE.
   - The sentence MUST start with one of these three phrases:
       * "We recommend" — for a GO recommendation.
       * "We recommend careful review of" — for a REVIEW recommendation.
       * "We advise against" — for a DECLINE recommendation.
   - The sentence must be clear and direct. State the decision and (briefly) why. \
One verb, one outcome, one reason. No bullet points, no semicolons, no second clause \
after a colon.

5. RISK_SUMMARY — TOP 5 RISKS BY SEVERITY. NEVER MORE.
   - Include at most 5 items. If the input has fewer than 5 risk findings, return \
fewer items. If the input has more than 5, select the 5 with the highest severity \
(critical > high > medium > low). Within the same severity, use the order from the \
input.
   - Order the list with critical first, then high, then medium, then low.
   - Each item's `description` MUST be exactly 1 sentence in plain English, summing up \
the risk for the analyst. You MUST NOT quote `clause_text` verbatim — the brief's job \
is to summarise, and the full clause text already lives in the risk findings table on \
the report page.
   - Each item's `category` and `severity` MUST match the corresponding input finding \
exactly. Do not re-classify.
   - If `risk_findings` is empty, return an empty list.

6. FEASIBILITY_HIGHLIGHTS — 3 TO 5 BULLET POINTS.
   - Each bullet is a single short sentence (no list-of-lists, no nested structure).
   - Each bullet MUST reference a specific dimension by name and its score out of 20. \
Example shape: "Technical Fit: 18/20 — strong alignment with the company's civil \
engineering specialisation."
   - Reference the rationale from the input only briefly — one short clause — never \
copy the full rationale verbatim. The report page already shows the full breakdown.
   - Pick the 3-5 most material dimensions. If a dimension scored 0 due to missing \
profile data, surface that explicitly (e.g. "Past Experience: 0/20 — insufficient \
profile data").
   - If `feasibility_breakdown` is empty or missing, return a single bullet pointing \
the analyst to the analysis section (e.g. "Feasibility data available in the analysis \
above.").

7. FINANCIAL_HIGHLIGHTS — 3 TO 5 BULLET POINTS.
   - Each bullet is a single short sentence.
   - Cover, where present in the input, these key commitments: contract value, \
performance bond requirement, liquidated damages rate and cap, retention rate, \
advance payment.
   - Use the exact numeric values from the input — do not round, do not approximate.
   - If a key commitment is not present in the input, omit that bullet rather than \
guessing. Do not invent financial numbers.
   - If `financial_summary` is empty or missing, return a single bullet pointing the \
analyst to the analysis section (e.g. "Financial data available in the analysis \
above.").

8. ANALYST_NOTE — REQUIRED WHEN is_analyst_override IS True.
   - If `is_analyst_override` is True, you MUST populate `analyst_note` with EXACTLY \
this text, substituting the numbers from the context:
       "Feasibility score adjusted from {ai_score:.0f} to {override_score:.0f} by \
analyst review."
   - Use the integer-rounded values (no decimals, no percentage sign).
   - The string is mandatory and exact — do not paraphrase it, do not add a period, do \
not add extra text. The format is part of the contract with the frontend.
   - If `is_analyst_override` is False, set `analyst_note` to null.

9. OUTPUT LANGUAGE IS ALWAYS ENGLISH.
   - The brief is read by an English-speaking analyst in an English UI. Regardless of \
the tender's source language (Arabic, English, bilingual), every field you produce \
MUST be in English.
   - Risk finding categories and severities keep their English enum values. \
`executive_summary`, `recommendation`, every `description`, every feasibility bullet, \
and every financial bullet are written in English.

10. NEVER FABRICATE DATA.
    - Never invent a numeric value (contract value, bond percentage, LD rate, \
feasibility score) that is not in the input.
    - Never invent a risk that is not in the input. If the input has zero risk \
findings, the brief shows zero risks.
    - Never upgrade a severity. If a risk is "medium" in the input, it stays "medium" \
in risk_summary.
    - Never downgrade a severity either. The brief preserves the upstream classification.
    - If a number in the input is unclear or missing, omit the bullet rather than \
guessing. The analyst can read the full data in the analysis section.

OUTPUT FORMAT:
    - Return a JSON object matching the ReportOutput schema exactly. The node passes \
this object through Pydantic validation.
    - Do not include any text outside the JSON object. No preamble, no postamble, no \
markdown fences, no commentary about the analysis you did not perform.
"""


FALLBACK_REPORT: dict = {
    "go_no_go": "REVIEW",
    "effective_score": 0.0,
    "is_analyst_override": False,
    "executive_summary": (
        "Automated report synthesis encountered an error. Please review the findings "
        "manually using the sections above."
    ),
    "recommendation": (
        "We recommend manual review of all findings before making a bid decision."
    ),
    "risk_summary": [],
    "feasibility_highlights": [
        "Feasibility data available in the analysis above.",
    ],
    "financial_highlights": [
        "Financial data available in the analysis above.",
    ],
    "analyst_note": None,
}


REPORT_FEW_SHOT_EXAMPLES: list[dict] = [
    {
        "note": "GO report — strong fit, minor risks",
        "context": {
            "effective_score": 82.0,
            "go_no_go": "GO",
            "is_analyst_override": False,
            "ai_score": 82.0,
            "override_score": None,
            "risk_findings": [
                {
                    "category": "fidic",
                    "severity": "medium",
                    "clause_text": (
                        "If the Contractor fails to comply with Sub-Clause 8.5 [Time "
                        "for Completion], the Contractor shall pay liquidated damages "
                        "to the Employer at the rate stated in the Appendix to the "
                        "Contract. The cap on liquidated damages shall be 10% of the "
                        "Accepted Contract Amount."
                    ),
                    "explanation": (
                        "Standard FIDIC Sub-Clause 8.7 delay damages clause with a "
                        "10% cap, which is at the upper end of the typical market "
                        "norm but not above it. Severity is medium because the cap is "
                        "in the standard 1%-5% market range relative to the bid margin "
                        "or, in absolute terms, at the FIDIC ceiling."
                    ),
                    "source_chunk_index": 0,
                    "confidence": 0.93,
                },
                {
                    "category": "lg_bond",
                    "severity": "medium",
                    "clause_text": (
                        "The Contractor shall provide a performance security in the "
                        "form of an on-demand bank guarantee for an amount equal to "
                        "5% of the Accepted Contract Amount."
                    ),
                    "explanation": (
                        "Standard FIDIC Sub-Clause 4.2 performance security at 5% of "
                        "contract value, which is the typical market norm and well "
                        "within the company's available bonding capacity."
                    ),
                    "source_chunk_index": 1,
                    "confidence": 0.96,
                },
                {
                    "category": "other",
                    "severity": "low",
                    "clause_text": (
                        "The Contractor shall maintain insurance covering contractor's "
                        "plant, materials, and third-party liability for the duration "
                        "of the Contract."
                    ),
                    "explanation": (
                        "Standard insurance documentation obligation with no material "
                        "deviation from market practice. Administrative only."
                    ),
                    "source_chunk_index": 2,
                    "confidence": 0.91,
                },
            ],
            "feasibility_breakdown": {
                "technical_fit": {
                    "score": 20,
                    "rationale": (
                        "Tender scope (earthworks, sub-base, asphalt, drainage) is "
                        "fully covered by company specialisations of civil and roads."
                    ),
                },
                "financial_capacity": {
                    "score": 20,
                    "rationale": (
                        "Tender value of EGP 30M is 60% of company max_project_value "
                        "of EGP 50M, and the required bond of EGP 1.5M is well within "
                        "available_bonding_capacity of EGP 12M."
                    ),
                },
                "timeline": {
                    "score": 15,
                    "rationale": (
                        "Tender duration of 24 months matches the typical 18-30 month "
                        "road project duration in past_projects."
                    ),
                },
                "geographic_scope": {
                    "score": 20,
                    "rationale": (
                        "Tender location in Cairo, EG is inside company "
                        "geographic_reach of [EG] and matches past_projects footprint."
                    ),
                },
                "past_experience": {
                    "score": 15,
                    "rationale": (
                        "Company past_projects contains 3 civil road projects with "
                        "values EGP 9M, EGP 13M, and EGP 22M, the largest at 73% of "
                        "the tender value of EGP 30M."
                    ),
                },
            },
            "financial_summary": {
                "contract_value": {
                    "value": 30_000_000.0,
                    "currency": "EGP",
                    "needs_review": False,
                },
                "bonds": [
                    {
                        "bond_type": "performance",
                        "amount": {
                            "value": 1_500_000.0,
                            "currency": "EGP",
                            "needs_review": False,
                        },
                        "percentage_of_contract": 5.0,
                    }
                ],
                "liquidated_damages": {
                    "rate": "0.05% of contract value per day",
                    "cap": {
                        "value": 3_000_000.0,
                        "currency": "EGP",
                        "needs_review": False,
                    },
                    "cap_percentage_of_contract": 10.0,
                },
                "retention": {
                    "rate_percentage": 10.0,
                    "release_condition": (
                        "Released at the end of the Defects Notification Period, "
                        "subject to engineer certification."
                    ),
                },
                "advance_payment": None,
            },
        },
        "expected_output": ReportOutput(
            go_no_go=GoNoGo.GO,
            effective_score=82.0,
            is_analyst_override=False,
            executive_summary=(
                "This tender is a 24-month road construction project in Cairo, Egypt, "
                "with an estimated contract value of EGP 30,000,000 covering "
                "earthworks, sub-base, asphalt paving, and drainage. The overall "
                "recommendation is GO with a feasibility score of 82 out of 100. "
                "Three risk findings were identified, all in the medium-or-lower "
                "range, with the standard FIDIC 10% delay damages cap and a 5% "
                "performance bond sitting at typical market levels. The financial "
                "profile is comfortable: the contract value is 60% of the company's "
                "declared maximum, and the required performance bond is well within "
                "available bonding capacity."
            ),
            recommendation=(
                "We recommend submitting a competitive bid, accepting the standard "
                "FIDIC terms as drafted and confirming the 5% performance bond with "
                "the company's bank before bid submission."
            ),
            risk_summary=[
                RiskSummaryItem(
                    category="fidic",
                    severity="medium",
                    description=(
                        "Standard FIDIC delay damages clause with a 10% cap, sitting "
                        "at the upper end of the typical market range but within "
                        "acceptable norms."
                    ),
                ),
                RiskSummaryItem(
                    category="lg_bond",
                    severity="medium",
                    description=(
                        "5% performance security in the form of an on-demand bank "
                        "guarantee, which matches the FIDIC market norm and is well "
                        "within the company's bonding capacity."
                    ),
                ),
                RiskSummaryItem(
                    category="other",
                    severity="low",
                    description=(
                        "Routine insurance documentation obligation with no material "
                        "deviation from market practice."
                    ),
                ),
            ],
            feasibility_highlights=[
                "Technical Fit: 20/20 — tender scope (earthworks, asphalt, drainage) "
                "is fully covered by the company's civil and roads specialisations.",
                "Financial Capacity: 20/20 — tender value of EGP 30M is 60% of "
                "max_project_value of EGP 50M and the bond is comfortably bonded.",
                "Timeline: 15/20 — tender duration of 24 months matches the typical "
                "delivery speed in past_projects.",
                "Geographic Scope: 20/20 — tender location in Cairo matches the "
                "company's Egypt-only footprint and past operating history.",
                "Past Experience: 15/20 — three civil road projects in past_projects, "
                "the largest at 73% of tender value.",
            ],
            financial_highlights=[
                "Contract value: EGP 30,000,000 over 24 months, 60% of company "
                "max_project_value.",
                "Performance bond: EGP 1,500,000 (5% of contract value), within "
                "available bonding capacity.",
                "Liquidated damages: 0.05% of contract value per day, capped at "
                "EGP 3,000,000 (10% of contract value).",
                "Retention: 10% of contract value, released at the end of the "
                "Defects Notification Period.",
            ],
            analyst_note=None,
        ).model_dump(),
    },
    {
        "note": (
            "DECLINE report — financial commitments exceed company capacity, "
            "multiple critical risks"
        ),
        "context": {
            "effective_score": 28.0,
            "go_no_go": "DECLINE",
            "is_analyst_override": False,
            "ai_score": 28.0,
            "override_score": None,
            "risk_findings": [
                {
                    "category": "penalty",
                    "severity": "critical",
                    "clause_text": (
                        "In case of delay in the completion of the Works, the "
                        "Contractor shall pay liquidated damages at a rate of 0.2% of "
                        "the Accepted Contract Amount per day, without any cap on the "
                        "total amount."
                    ),
                    "explanation": (
                        "Uncapped liquidated damages with a daily rate of 0.2% of "
                        "contract value, which can compound to many times the contract "
                        "value over a multi-month delay. Severity is critical because "
                        "the absence of a cap exposes the contractor to a contract "
                        "loss larger than the contract margin."
                    ),
                    "source_chunk_index": 0,
                    "confidence": 0.97,
                },
                {
                    "category": "lg_bond",
                    "severity": "critical",
                    "clause_text": (
                        "The Contractor shall provide a performance bond in the form "
                        "of an unconditional bank guarantee for an amount equal to "
                        "20% of the Accepted Contract Amount, valid until the end of "
                        "the Defects Notification Period."
                    ),
                    "explanation": (
                        "Performance bond of 20% of contract value is materially above "
                        "the FIDIC market norm of 5%-10% and, given the contract value, "
                        "exceeds the company's available bonding capacity. Severity is "
                        "critical because the bond is uncapped beyond its face value "
                        "and the bond amount alone is unwriteable for the company."
                    ),
                    "source_chunk_index": 1,
                    "confidence": 0.95,
                },
                {
                    "category": "fidic",
                    "severity": "high",
                    "clause_text": (
                        "The Employer may, by giving 7 days' written notice, "
                        "terminate the Contract immediately if the Contractor fails to "
                        "remedy a material breach within 3 days of written notice."
                    ),
                    "explanation": (
                        "Termination clause with a 3-day cure period, materially below "
                        "the FIDIC standard 14-day cure under Sub-Clause 15.1, and a "
                        "broad 'material breach of any obligation' trigger. Severity "
                        "is high because the asymmetric cure window shifts termination "
                        "risk to the contractor."
                    ),
                    "source_chunk_index": 2,
                    "confidence": 0.90,
                },
            ],
            "feasibility_breakdown": {
                "technical_fit": {
                    "score": 5,
                    "rationale": (
                        "Tender scope is dam construction (mass concrete, hydraulic "
                        "systems) which overlaps with the company's 'civil' "
                        "specialisation but the hydraulic core is materially outside "
                        "the company's MEP focus."
                    ),
                },
                "financial_capacity": {
                    "score": 0,
                    "rationale": (
                        "Tender value of SAR 180M exceeds company max_project_value of "
                        "EGP 12M by many orders of magnitude and the 20% performance "
                        "bond exceeds available_bonding_capacity of EGP 3M."
                    ),
                },
                "timeline": {
                    "score": 15,
                    "rationale": (
                        "Tender duration of 36 months is within 90%-110% of the "
                        "company's longest past project (30-month civil "
                        "infrastructure project)."
                    ),
                },
                "geographic_scope": {
                    "score": 0,
                    "rationale": (
                        "Tender location in SA (Riyadh) is not in company "
                        "geographic_reach of [EG]."
                    ),
                },
                "past_experience": {
                    "score": 5,
                    "rationale": (
                        "Company past_projects contains 1 civil infrastructure project "
                        "in Egypt at EGP 7M, well below 25% of the tender value."
                    ),
                },
            },
            "financial_summary": {
                "contract_value": {
                    "value": 180_000_000.0,
                    "currency": "SAR",
                    "needs_review": False,
                },
                "bonds": [
                    {
                        "bond_type": "performance",
                        "amount": {
                            "value": 36_000_000.0,
                            "currency": "SAR",
                            "needs_review": False,
                        },
                        "percentage_of_contract": 20.0,
                    }
                ],
                "liquidated_damages": {
                    "rate": "0.2% of contract value per day",
                    "cap": None,
                    "cap_percentage_of_contract": None,
                },
                "retention": {
                    "rate_percentage": 10.0,
                    "release_condition": (
                        "Released at the end of the Defects Notification Period."
                    ),
                },
                "advance_payment": {
                    "amount": {
                        "value": 18_000_000.0,
                        "currency": "SAR",
                        "needs_review": False,
                    },
                    "percentage_of_contract": 10.0,
                },
            },
        },
        "expected_output": ReportOutput(
            go_no_go=GoNoGo.DECLINE,
            effective_score=28.0,
            is_analyst_override=False,
            executive_summary=(
                "This tender is a 36-month dam construction project in Riyadh, Saudi "
                "Arabia, with an estimated contract value of SAR 180,000,000 covering "
                "mass concrete and hydraulic works. The overall recommendation is "
                "DECLINE with a feasibility score of 28 out of 100. Two critical risks "
                "were identified: uncapped liquidated damages and a 20% performance "
                "bond that exceeds the company's bonding capacity. Financial "
                "commitments far exceed company capacity: the contract value is several "
                "orders of magnitude above max_project_value and the required bond is "
                "unwriteable."
            ),
            recommendation=(
                "We advise against bidding on this tender in its current form, as the "
                "uncapped liquidated damages and 20% performance bond make the bid "
                "unwriteable for the company."
            ),
            risk_summary=[
                RiskSummaryItem(
                    category="penalty",
                    severity="critical",
                    description=(
                        "Uncapped liquidated damages at 0.2% of contract value per "
                        "day, exposing the contractor to losses larger than the "
                        "contract margin on any multi-month delay."
                    ),
                ),
                RiskSummaryItem(
                    category="lg_bond",
                    severity="critical",
                    description=(
                        "20% unconditional performance bond, which is materially above "
                        "the FIDIC market norm of 5%-10% and exceeds the company's "
                        "available bonding capacity."
                    ),
                ),
                RiskSummaryItem(
                    category="fidic",
                    severity="high",
                    description=(
                        "Termination clause with a 3-day cure period, materially below "
                        "the FIDIC standard 14-day cure, shifting termination risk "
                        "asymmetrically to the contractor."
                    ),
                ),
            ],
            feasibility_highlights=[
                "Technical Fit: 5/20 — dam scope overlaps with 'civil' but the "
                "hydraulic core is materially outside the company's MEP focus.",
                "Financial Capacity: 0/20 — tender value is several orders of "
                "magnitude above max_project_value and the bond is unwriteable.",
                "Timeline: 15/20 — 36-month duration matches the company's longest "
                "past project.",
                "Geographic Scope: 0/20 — tender location in SA is outside the "
                "company's Egypt-only geographic_reach.",
                "Past Experience: 5/20 — only one civil infrastructure project, well "
                "below 25% of tender value.",
            ],
            financial_highlights=[
                "Contract value: SAR 180,000,000 over 36 months, several orders of "
                "magnitude above company max_project_value.",
                "Performance bond: SAR 36,000,000 (20% of contract value), "
                "unwriteable within available bonding capacity.",
                "Liquidated damages: 0.2% of contract value per day, uncapped — "
                "exposure grows linearly with delay.",
                "Retention: 10% of contract value, released at the end of the "
                "Defects Notification Period.",
                "Advance payment: SAR 18,000,000 (10% of contract value), secured by "
                "a separate advance-payment guarantee.",
            ],
            analyst_note=None,
        ).model_dump(),
    },
    {
        "note": (
            "REVIEW report with analyst override — AI scored 35 (DECLINE), "
            "analyst overrode to 65 (REVIEW); demonstrates the override flow "
            "that REQ-007 enables"
        ),
        "context": {
            "effective_score": 65.0,
            "go_no_go": "REVIEW",
            "is_analyst_override": True,
            "ai_score": 35.0,
            "override_score": 65.0,
            "risk_findings": [
                {
                    "category": "fidic",
                    "severity": "high",
                    "clause_text": (
                        "The cap on liquidated damages shall be 12% of the Accepted "
                        "Contract Amount, applied on a per-day basis at the rate "
                        "stated in the Appendix."
                    ),
                    "explanation": (
                        "Delay damages cap of 12% is materially above the typical "
                        "FIDIC norm of up to 10% and would expose the contractor to a "
                        "penalty close to the contract margin. Severity is high "
                        "because the cap exceeds 5% of contract value and approaches "
                        "the critical threshold."
                    ),
                    "source_chunk_index": 0,
                    "confidence": 0.92,
                },
                {
                    "category": "lg_bond",
                    "severity": "medium",
                    "clause_text": (
                        "The Contractor shall provide a performance bond equal to 8% "
                        "of the Accepted Contract Amount, in the form of a bank "
                        "guarantee from a bank acceptable to the Employer."
                    ),
                    "explanation": (
                        "Performance bond of 8% is within the typical FIDIC market "
                        "norm (5%-10%) and is bondable for the company. Severity is "
                        "medium because the bond condition is standard even though "
                        "the absolute amount is at the upper end of the market range."
                    ),
                    "source_chunk_index": 1,
                    "confidence": 0.94,
                },
                {
                    "category": "termination",
                    "severity": "medium",
                    "clause_text": (
                        "The Employer may suspend the Works if the Contractor fails "
                        "to comply with a written notice within 14 days, with the "
                        "cost of suspension borne by the Contractor."
                    ),
                    "explanation": (
                        "Suspension right with a 14-day cure window (FIDIC standard) "
                        "and a cost-allocation clause that places suspension cost on "
                        "the contractor. Severity is medium because the cure window is "
                        "standard, but the cost-allocation wording is contractor-"
                        "favourable to the Employer."
                    ),
                    "source_chunk_index": 2,
                    "confidence": 0.88,
                },
            ],
            "feasibility_breakdown": {
                "technical_fit": {
                    "score": 15,
                    "rationale": (
                        "Tender scope is buildings fit-out (MEP-heavy) which aligns "
                        "with the company's two declared specialisations of mep and "
                        "fit-out, with one minor sub-scope (low-voltage systems) that "
                        "the company has not previously delivered."
                    ),
                },
                "financial_capacity": {
                    "score": 10,
                    "rationale": (
                        "Tender value of EGP 18M is 90% of company max_project_value "
                        "of EGP 20M (no headroom), and the 8% performance bond of "
                        "EGP 1.44M is at the edge of available_bonding_capacity of "
                        "EGP 1.5M."
                    ),
                },
                "timeline": {
                    "score": 15,
                    "rationale": (
                        "Tender duration of 18 months is within 90%-110% of the "
                        "company's typical MEP project duration from past_projects."
                    ),
                },
                "geographic_scope": {
                    "score": 20,
                    "rationale": (
                        "Tender location in Alexandria, EG is inside company "
                        "geographic_reach of [EG, SA] and matches the operating "
                        "footprint of past_projects."
                    ),
                },
                "past_experience": {
                    "score": 5,
                    "rationale": (
                        "Company past_projects contains 1 MEP fit-out project in "
                        "Egypt at EGP 6M, which is 33% of the tender value."
                    ),
                },
            },
            "financial_summary": {
                "contract_value": {
                    "value": 18_000_000.0,
                    "currency": "EGP",
                    "needs_review": False,
                },
                "bonds": [
                    {
                        "bond_type": "performance",
                        "amount": {
                            "value": 1_440_000.0,
                            "currency": "EGP",
                            "needs_review": False,
                        },
                        "percentage_of_contract": 8.0,
                    }
                ],
                "liquidated_damages": {
                    "rate": "0.1% of contract value per day",
                    "cap": {
                        "value": 2_160_000.0,
                        "currency": "EGP",
                        "needs_review": False,
                    },
                    "cap_percentage_of_contract": 12.0,
                },
                "retention": {
                    "rate_percentage": 10.0,
                    "release_condition": (
                        "Released at the end of the Defects Notification Period, "
                        "split half at Taking Over and half at Performance "
                        "Certificate."
                    ),
                },
                "advance_payment": {
                    "amount": {
                        "value": 1_800_000.0,
                        "currency": "EGP",
                        "needs_review": False,
                    },
                    "percentage_of_contract": 10.0,
                },
            },
        },
        "expected_output": ReportOutput(
            go_no_go=GoNoGo.REVIEW,
            effective_score=65.0,
            is_analyst_override=True,
            executive_summary=(
                "This tender is an 18-month MEP-heavy building fit-out project in "
                "Alexandria, Egypt, with an estimated contract value of EGP "
                "18,000,000 covering HVAC, electrical, plumbing, and low-voltage "
                "systems. The overall recommendation is REVIEW with a feasibility "
                "score of 65 out of 100 after an analyst override. The AI's original "
                "score of 35 (DECLINE) was driven by an aggressive 12% liquidated "
                "damages cap and tight bonding capacity; the analyst overrode the "
                "score upward on the basis that the 12% LD cap is a known point of "
                "negotiation with the Employer. The most material risk is the 12% "
                "delay damages cap, which is materially above the FIDIC market norm "
                "of up to 10%. Financial exposure is high relative to the company's "
                "capacity, with the contract value at 90% of max_project_value and "
                "the 8% bond at the edge of available bonding capacity."
            ),
            recommendation=(
                "We recommend careful review of the 12% liquidated damages cap and "
                "the bonding capacity headroom before submitting a bid, and "
                "preparation of a written negotiation plan targeting the LD cap and "
                "the bond percentage as bid conditions."
            ),
            risk_summary=[
                RiskSummaryItem(
                    category="fidic",
                    severity="high",
                    description=(
                        "12% delay damages cap is materially above the typical FIDIC "
                        "norm of up to 10% and approaches the contract margin on a "
                        "materially delayed project."
                    ),
                ),
                RiskSummaryItem(
                    category="lg_bond",
                    severity="medium",
                    description=(
                        "8% performance bond is within the market norm but the "
                        "absolute amount leaves no headroom within the company's "
                        "available bonding capacity."
                    ),
                ),
                RiskSummaryItem(
                    category="termination",
                    severity="medium",
                    description=(
                        "Suspension right with a 14-day cure window is FIDIC standard, "
                        "but cost allocation places suspension cost on the contractor."
                    ),
                ),
            ],
            feasibility_highlights=[
                "Technical Fit: 15/20 — MEP-heavy fit-out aligns with the company's "
                "mep and fit-out specialisations, with one minor sub-scope gap.",
                "Financial Capacity: 10/20 — tender value is 90% of "
                "max_project_value (no headroom) and the bond is at the edge of "
                "available capacity.",
                "Timeline: 15/20 — 18-month duration matches the company's typical "
                "MEP project duration.",
                "Geographic Scope: 20/20 — tender location in Alexandria matches the "
                "company's Egypt footprint and past_projects.",
                "Past Experience: 5/20 — only one comparable MEP project at 33% of "
                "tender value.",
            ],
            financial_highlights=[
                "Contract value: EGP 18,000,000 over 18 months, 90% of company "
                "max_project_value with no headroom.",
                "Performance bond: EGP 1,440,000 (8% of contract value), at the edge "
                "of available bonding capacity.",
                "Liquidated damages: 0.1% of contract value per day, capped at EGP "
                "2,160,000 (12% of contract value) — above market norm.",
                "Retention: 10% of contract value, split at Taking Over and at the "
                "Performance Certificate.",
                "Advance payment: EGP 1,800,000 (10% of contract value).",
            ],
            analyst_note=(
                "Feasibility score adjusted from 35 to 65 by analyst review."
            ),
        ).model_dump(),
    },
]
