"""
Risk Clause Extraction — Skill Package (REQ-004, Slice 1).

This file is PURE DATA/CONFIG. It contains:
  1. Pydantic structured output schema (RiskFinding, RiskRadarOutput)
  2. SEVERITY_RUBRIC — concrete, reproducible thresholds
  3. FIDIC_TAXONOMY — FIDIC 1999 Red Book clause references
  4. RISK_RADAR_SYSTEM_PROMPT — instructions for the LLM
  5. FEW_SHOT_EXAMPLES — 5 examples (2 EN, 2 AR source, 1 no-risk)

It contains NO LangChain imports, NO LangGraph imports, and NO async
functions. Slice 2 (agents/nodes/risk_radar.py) is the only file
allowed to wire these constants into a LangGraph node.

Reviewable by a non-engineer (e.g. a contracts professional) before
any code touches the prompt or schema.

NOTE: This file is a byte-identical copy of the Slice 1 deliverable at
the repository root (`app/agents/skills/risk_clause_extraction.py`).
The backend's Python package is rooted at `backend/app/` (per
`backend/pyproject.toml`'s `setuptools.packages.find` config), so
`from app.agents.skills.risk_clause_extraction import ...` resolves
under `backend/app/agents/skills/`. The Slice 1 file at the repository
root remains the source of truth and must not be edited in Slice 2.
"""

from typing import Literal

from pydantic import BaseModel, Field


class RiskFinding(BaseModel):
    category: Literal["fidic", "penalty", "lg_bond", "termination", "other"]
    severity: Literal["critical", "high", "medium", "low"]
    clause_text: str = Field(
        description=(
            "Verbatim quote from the source chunk — never paraphrased. "
            "If the source chunk is Arabic, quote the Arabic text exactly."
        )
    )
    explanation: str = Field(
        description=(
            "Plain-English explanation, regardless of source language. "
            "The English analyst must be able to act on this without reading "
            "the original Arabic clause."
        )
    )
    source_chunk_index: int = Field(
        description=(
            "The 0-based index of the source chunk in the input chunks list. "
            "Used by the node to verify clause_text is a substring of that "
            "chunk's text before persisting the finding."
        )
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Model self-reported certainty on a 0.0–1.0 scale. If genuinely "
            "uncertain between two categories, lower the score rather than "
            "guessing."
        ),
    )


class RiskRadarOutput(BaseModel):
    findings: list[RiskFinding]


SEVERITY_RUBRIC: dict[str, str] = {
    "critical": (
        "Clauses with uncapped or asymmetric financial liability. "
        "Examples: unlimited liquidated damages (no cap, or cap exceeds 10% "
        "of contract value); one-sided termination rights with no cure period; "
        "performance bond or LG demands that exceed 10% of contract value; "
        "indemnity clauses that are unlimited in time or scope. Critical = "
        "a mis-bid on this clause can directly cause a contract loss larger "
        "than the contract margin."
    ),
    "high": (
        "Capped but materially significant penalties — typically exceeding 5% "
        "of contract value, or LG / bond conditions that are unusually "
        "onerous relative to market norms (e.g. > 5% bond when market norm "
        "is 5%). High = the clause is negotiable but its current form would "
        "materially weaken bid economics if accepted as-is."
    ),
    "medium": (
        "Standard penalty clauses within typical market range (1%–5% of "
        "contract value), or standard FIDIC conditions with the normal 28-day "
        "notice / 42-day claim windows. Medium = in line with market; "
        "analyst should review but no special escalation needed."
    ),
    "low": (
        "Administrative or procedural risk with minimal direct financial "
        "exposure. Examples: notice period requirements, routine reporting "
        "obligations, insurance certificate obligations, standard warranty "
        "documentation. Low = flag for awareness only."
    ),
}


FIDIC_TAXONOMY: dict[str, dict[str, str]] = {
    "sub_clause_1_9": {
        "title": "Delayed Drawings or Instructions",
        "applies_to": "fidic",
        "risk_note": (
            "If the Employer fails to deliver drawings/instructions on time "
            "and the Contractor suffers cost or delay, this is a contractor "
            "entitlement. Asymmetric drafting (e.g. removing the entitlement) "
            "should be flagged as a FIDIC deviation."
        ),
    },
    "sub_clause_2_1": {
        "title": "Right of Access to the Site",
        "applies_to": "fidic",
        "risk_note": (
            "Defines the Employer's obligation to give the Contractor timely "
            "access. Look for clauses that delay or condition this right, as "
            "they can cascade into EOT and cost claims."
        ),
    },
    "sub_clause_4_2": {
        "title": "Performance Security",
        "applies_to": "lg_bond",
        "risk_note": (
            "Standard FIDIC form requires the Contractor to obtain a "
            "performance security (usually a bank guarantee or surety bond) "
            "of an amount stated in the Appendix (commonly 5%–10% of the "
            "Accepted Contract Amount). Anything materially above 10%, or "
            "with wording that expands the bank/bonder's call rights beyond "
            "the FIDIC standard, should be flagged — that is a lg_bond risk, "
            "not a pure fidic reference."
        ),
    },
    "sub_clause_8_4": {
        "title": "Extension of Time for Completion",
        "applies_to": "fidic",
        "risk_note": (
            "Sets out the contractual mechanism for EOT. Asymmetric "
            "drafting that narrows the Employer's Risk Events list (e.g. "
            "removing 'unforeseeable physical conditions' or 'delay by "
            "authorities') is a FIDIC deviation worth flagging."
        ),
    },
    "sub_clause_8_7": {
        "title": "Delay Damages",
        "applies_to": "penalty",
        "risk_note": (
            "This is FIDIC's liquidated damages for delay clause. The daily "
            "rate and cap are stated in the Appendix. A standard cap is up "
            "to 10% of the Accepted Contract Amount; anything above 10% or "
            "uncapped should be flagged as penalty category with severity "
            "critical. Anything at the standard 10% cap is severity high. "
            "Below 5% and clearly time-limited is severity medium."
        ),
    },
    "sub_clause_11_4": {
        "title": "Failure to Remedy Defects",
        "applies_to": "termination",
        "risk_note": (
            "Gives the Employer rights when the Contractor fails to remedy "
            "defects. Asymmetric drafting that removes the Contractor's cure "
            "window or that converts minor defects into termination grounds "
            "should be flagged as a termination risk."
        ),
    },
    "sub_clause_15_1": {
        "title": "Notice of Default",
        "applies_to": "termination",
        "risk_note": (
            "The formal precursor to termination under Sub-Clause 15.2. "
            "Standard form gives the Contractor a 14-day cure period. "
            "Shortening or removing this period is a FIDIC deviation that "
            "should be flagged."
        ),
    },
    "sub_clause_15_2": {
        "title": "Termination by the Employer",
        "applies_to": "termination",
        "risk_note": (
            "Lists the grounds on which the Employer may terminate the "
            "Contractor's employment. Standard grounds include failure to "
            "proceed, abandonment, persistent failure to comply, "
            "insolvency, and corruption. Watch for additions (e.g. "
            "'material breach of any obligation') that broaden the "
            "Employer's termination right and shift risk asymmetrically."
        ),
    },
    "sub_clause_15_5": {
        "title": "Termination by the Contractor",
        "applies_to": "termination",
        "risk_note": (
            "The Contractor's symmetric termination right (e.g. for "
            "non-payment). If the Particular Conditions remove or "
            "narrow this clause, the Contractor loses an important exit "
            "right — flag as a FIDIC deviation."
        ),
    },
    "sub_clause_17_4": {
        "title": "Consequences of Risks",
        "applies_to": "fidic",
        "risk_note": (
            "Defines who bears the cost of Employer's Risks (e.g. war, "
            "ionising radiation, force majeure of Employer origin). "
            "Asymmetric drafting that reallocates these to the Contractor "
            "should be flagged."
        ),
    },
    "sub_clause_20_1": {
        "title": "Contractor's Claims",
        "applies_to": "fidic",
        "risk_note": (
            "Sets the 28-day notice / 42-day detailed-claim windows. "
            "Shortening these windows or converting them into 'no "
            "compensation if missed' (rather than the older 'limited to "
            "contemporary records') wording is common and should be "
            "flagged as a FIDIC deviation."
        ),
    },
}


RISK_RADAR_SYSTEM_PROMPT: str = """You are the Risk Radar for TenderIQ, a B2B platform that \
analyses construction and procurement tenders for contractors in Egypt and the GCC.

Your single job: given a set of tender document chunks, identify and classify every \
clause that creates legal or financial risk for the contractor bidding on the tender.

YOU MUST FOLLOW THESE RULES WITHOUT EXCEPTION:

1. EXTRACT ONLY WHAT IS PRESENT.
   - Only return a finding if a risk-bearing clause is ACTUALLY present in the \
provided chunks. Never infer, fabricate, or 'fill in' a clause because it is \
typical for a FIDIC contract or a GCC tender.
   - If a chunk contains no risk-bearing clause, do NOT produce a finding from it.
   - If NO risk-bearing clauses are found across ALL chunks, return an empty \
findings list: {"findings": []}. An empty list is a valid output, not a failure.

2. QUOTE VERBATIM.
   - The `clause_text` field MUST be a verbatim substring of the source chunk. \
Never paraphrase, summarise, translate, or correct the original.
   - If the source chunk is in Arabic, `clause_text` MUST be the Arabic text \
exactly as it appears, including any diacritics, punctuation, and original \
spelling. Do NOT translate `clause_text` into English.
   - Do NOT add quotation marks, ellipses, or commentary inside `clause_text` \
itself. The verifier will check that `clause_text` is a substring of the chunk \
text at the recorded `source_chunk_index`.

3. EXPLAIN IN ENGLISH, ALWAYS.
   - The `explanation` field MUST be written in plain English, regardless of \
whether `clause_text` is Arabic or English. The TenderIQ UI is English-only and \
the analyst is reading in English. Write as if explaining to a non-Arabic-reader \
bid manager.
   - The explanation should make the risk operational: what it is, why it \
matters, and what the contractor would need to negotiate or price for.

4. ASSIGN EXACTLY ONE CATEGORY.
   - Each finding gets exactly one category from this fixed enum: \
"fidic", "penalty", "lg_bond", "termination", "other". No multi-tagging.
   - Use "fidic" for clauses that reference, vary, or contradict a named FIDIC \
sub-clause (e.g. Sub-Clause 8.7 Delay Damages, Sub-Clause 15.2 Termination by \
the Employer, Sub-Clause 4.2 Performance Security). Cite the specific sub-clause \
number in the explanation.
   - Use "penalty" for any clause that imposes a financial penalty on the \
contractor (delay damages, defect liability deductions, performance penalties) \
regardless of whether it is FIDIC-rooted or bespoke.
   - Use "lg_bond" for any clause that requires a bank guarantee, surety bond, \
parent-company guarantee, retention guarantee, or advance-payment guarantee.
   - Use "termination" for clauses that create termination, suspension, or \
notice-of-default rights — either for the Employer or the Contractor.
   - Use "other" only if a clause is genuinely risk-bearing but fits none of \
the above. Do not use "other" as a default for ambiguity.

5. APPLY THE SEVERITY RUBRIC CONSISTENTLY.
   - Use the SEVERITY_RUBRIC constant provided to you. The thresholds are:
       * critical: uncapped or asymmetric financial liability (e.g. unlimited \
delay damages, or one-sided termination with no cure period).
       * high: capped but materially significant penalty (exceeding 5% of \
contract value), or unusually onerous LG/bond conditions relative to market norm.
       * medium: standard penalty clause in typical market range (1%–5% of \
contract value), or standard FIDIC conditions with normal cure periods.
       * low: administrative / procedural risk with minimal direct financial \
exposure (notice periods, reporting obligations).
   - In the `explanation` field, cite the specific numeric or structural reason \
for the severity (e.g. "exceeds 5% of contract value", "removes the standard \
14-day cure period in Sub-Clause 15.1"). Never use vague language like \
"significant" or "unusual" without the underlying threshold.

6. BE HONEST ABOUT CONFIDENCE.
   - `confidence` is your self-reported certainty on a 0.0–1.0 scale.
   - If a clause is ambiguous between two categories (e.g. could be "penalty" \
or "termination"), pick the single most specific category AND set `confidence` \
below 0.7. You may also note the ambiguity in the `explanation`.
   - If you are not confident the verbatim text appears in the chunk, do not \
emit the finding at all. A 0.4-confidence finding is acceptable when the clause \
IS present; a fabricated clause at 0.95 confidence is a defect.

7. NEVER FABRICATE TO SEEM THOROUGH.
   - Do not invent clauses to fill out a list. Returning 2 high-quality \
findings is better than 8 findings where 3 are fabricated.
   - Do not duplicate the same clause under multiple categories.

OUTPUT FORMAT:
   - You MUST return your answer as a JSON object matching the RiskRadarOutput \
schema: {"findings": [RiskFinding, ...]}.
   - `source_chunk_index` is the 0-based index of the chunk in the input list \
where the verbatim `clause_text` was found.
   - Do not include any text outside the JSON object. No preamble, no \
postamble, no markdown fences.
"""


FEW_SHOT_EXAMPLES: list[dict] = [
    {
        "input_chunk": (
            "Section 8.7 — Liquidated Damages for Delay. If the Contractor fails "
            "to comply with Sub-Clause 8.5 [Time for Completion], the Contractor "
            "shall pay liquidated damages to the Employer at the rate stated in "
            "the Appendix to the Contract. The cap on liquidated damages shall "
            "be 15% of the Accepted Contract Amount."
        ),
        "expected_output": RiskFinding(
            category="fidic",
            severity="critical",
            clause_text=(
                "If the Contractor fails to comply with Sub-Clause 8.5 [Time for "
                "Completion], the Contractor shall pay liquidated damages to the "
                "Employer at the rate stated in the Appendix to the Contract. "
                "The cap on liquidated damages shall be 15% of the Accepted "
                "Contract Amount."
            ),
            explanation=(
                "This is FIDIC Sub-Clause 8.7 (Delay Damages). The cap is set at "
                "15% of the Accepted Contract Amount, which is materially above "
                "the typical FIDIC market norm of up to 10%. Severity is "
                "critical because the cap exceeds 10% of contract value and "
                "exposes the Contractor to a contract loss larger than typical "
                "contract margin."
            ),
            source_chunk_index=0,
            confidence=0.95,
        ).model_dump(),
    },
    {
        "input_chunk": (
            "Clause 12.3 — Termination for Default. The Employer may, by giving "
            "14 days' written notice to the Contractor, terminate the Contract "
            "immediately if the Contractor fails to remedy a material breach "
            "within seven (7) days of written notice from the Employer. The "
            "Employer's right to terminate under this clause is in addition to, "
            "and not in substitution for, any other right or remedy of the "
            "Employer at law."
        ),
        "expected_output": RiskFinding(
            category="termination",
            severity="high",
            clause_text=(
                "The Employer may, by giving 14 days' written notice to the "
                "Contractor, terminate the Contract immediately if the "
                "Contractor fails to remedy a material breach within seven (7) "
                "days of written notice from the Employer. The Employer's right "
                "to terminate under this clause is in addition to, and not in "
                "substitution for, any other right or remedy of the Employer at "
                "law."
            ),
            explanation=(
                "Termination for default clause. The 7-day cure period is short "
                "relative to the FIDIC standard 14-day cure under Sub-Clause "
                "15.1, and the phrase 'material breach of any obligation' is "
                "broad. The cumulative-remedies wording is standard. Severity "
                "is high because the asymmetric cure period and broad trigger "
                "materially shift termination risk to the Contractor, though a "
                "written notice requirement and a cure window (however short) "
                "keep it below 'critical'."
            ),
            source_chunk_index=1,
            confidence=0.88,
        ).model_dump(),
    },
    {
        "input_chunk": (
            "\u0627\u0644\u0628\u0646\u062f 8/7 \u2013 \u062a\u0639\u0648\u064a\u0636 "
            "\u0627\u0644\u0623\u0636\u0631\u0627\u0631 \u0628\u0633\u0628\u0628 "
            "\u0627\u0644\u062a\u0623\u062e\u064a\u0631: \u0641\u064a \u062d\u0627\u0644\u0629 "
            "\u0639\u062f\u0645 \u0627\u0644\u062a\u0632\u0627\u0645 \u0627\u0644\u0645\u0642\u0627\u0648\u0644 "
            "\u0628\u0627\u0644\u0645\u0648\u0627\u0639\u064a\u062f \u0627\u0644\u0645\u062d\u062f\u062f\u0629 "
            "\u0644\u0627\u062a\u0645\u0627\u0645 \u0627\u0644\u0623\u0639\u0645\u0627\u0644 \u0648\u0641\u0642\u0627\u064b "
            "\u0644\u0644\u0628\u0646\u062f 8/5\u060c \u064a\u0644\u062a\u0632\u0645 "
            "\u0627\u0644\u0645\u0642\u0627\u0648\u0644 \u0628\u062f\u0641\u0639 \u062a\u0639\u0648\u064a\u0636 "
            "\u0623\u0636\u0631\u0627\u0631 \u0644\u0644\u063a\u064a\u0631 \u0628\u0646\u0633\u0628\u0629 \u0623\u0633\u0628\u0648\u0639\u064a\u0629 "
            "\u062a\u0633\u0627\u0648\u064a 0.2% \u0645\u0646 \u0642\u064a\u0645\u0629 \u0627\u0644\u0639\u0642\u062f "
            "\u0627\u0644\u0645\u0648\u0627\u0641\u0642 \u0639\u0644\u064a\u0647\u060c \u0639\u0644\u0649 \u0623\u0646 "
            "\u0644\u0627 \u062a\u062a\u062c\u0627\u0648\u0632 \u0625\u062c\u0645\u0627\u0644\u064a \u062a\u0644\u0643 "
            "\u0627\u0644\u062a\u0639\u0648\u064a\u0636\u0627\u062a 10% \u0645\u0646 \u0642\u064a\u0645\u0629 "
            "\u0627\u0644\u0639\u0642\u062f. \u062a\u062f\u0641\u0639 \u062a\u0644\u0643 "
            "\u0627\u0644\u062a\u0639\u0648\u064a\u0636\u0627\u062a \u063a\u064a\u0631 \u062a\u0639\u0628\u064a\u0631\u064a\u0629 "
            "\u0639\u0646 \u0623\u064a \u062d\u0642 \u0644\u0644\u063a\u064a\u0631 \u0641\u064a \u0627\u0644\u0637\u0644\u0628 "
            "\u062a\u0639\u0648\u064a\u0636\u0627\u062a \u062a\u0627\u0645\u0629 \u0639\u0646 \u0627\u0644\u0623\u0636\u0631\u0627\u0631."
        ),
        "expected_output": RiskFinding(
            category="fidic",
            severity="high",
            clause_text=(
                "\u0641\u064a \u062d\u0627\u0644\u0629 \u0639\u062f\u0645 \u0627\u0644\u062a\u0632\u0627\u0645 "
                "\u0627\u0644\u0645\u0642\u0627\u0648\u0644 \u0628\u0627\u0644\u0645\u0648\u0627\u0639\u064a\u062f "
                "\u0627\u0644\u0645\u062d\u062f\u062f\u0629 \u0644\u0627\u062a\u0645\u0627\u0645 \u0627\u0644\u0623\u0639\u0645\u0627\u0644 "
                "\u0648\u0641\u0642\u0627\u064b \u0644\u0644\u0628\u0646\u062f 8/5\u060c \u064a\u0644\u062a\u0632\u0645 "
                "\u0627\u0644\u0645\u0642\u0627\u0648\u0644 \u0628\u062f\u0641\u0639 \u062a\u0639\u0648\u064a\u0636 \u0623\u0636\u0631\u0627\u0631 "
                "\u0644\u0644\u063a\u064a\u0631 \u0628\u0646\u0633\u0628\u0629 \u0623\u0633\u0628\u0648\u0639\u064a\u0629 \u062a\u0633\u0627\u0648\u064a "
                "0.2% \u0645\u0646 \u0642\u064a\u0645\u0629 \u0627\u0644\u0639\u0642\u062f \u0627\u0644\u0645\u0648\u0627\u0641\u0642 "
                "\u0639\u0644\u064a\u0647\u060c \u0639\u0644\u0649 \u0623\u0646 \u0644\u0627 \u062a\u062a\u062c\u0627\u0648\u0632 "
                "\u0625\u062c\u0645\u0627\u0644\u064a \u062a\u0644\u0643 \u0627\u0644\u062a\u0639\u0648\u064a\u0636\u0627\u062a 10% \u0645\u0646 "
                "\u0642\u064a\u0645\u0629 \u0627\u0644\u0639\u0642\u062f. \u062a\u062f\u0641\u0639 \u062a\u0644\u0643 "
                "\u0627\u0644\u062a\u0639\u0648\u064a\u0636\u0627\u062a \u063a\u064a\u0631 \u062a\u0639\u0628\u064a\u0631\u064a\u0629 "
                "\u0639\u0646 \u0623\u064a \u062d\u0642 \u0644\u0644\u063a\u064a\u0631 \u0641\u064a \u0627\u0644\u0637\u0644\u0628 "
                "\u062a\u0639\u0648\u064a\u0636\u0627\u062a \u062a\u0627\u0645\u0629 \u0639\u0646 \u0627\u0644\u0623\u0636\u0631\u0627\u0631."
            ),
            explanation=(
                "This is the Arabic equivalent of FIDIC Sub-Clause 8.7 (Delay "
                "Damages). The cap is set at 10% of the Accepted Contract "
                "Amount, with a weekly rate of 0.2% of contract value. The cap "
                "is at the high end of the FIDIC market norm but not above it, "
                "so severity is 'high' (capped but at the upper end of market). "
                "The final sentence clarifies that these damages are the "
                "Employer's sole and exclusive remedy for delay, which is "
                "standard FIDIC."
            ),
            source_chunk_index=2,
            confidence=0.92,
        ).model_dump(),
    },
    {
        "input_chunk": (
            "\u0627\u0644\u0645\u0627\u062f\u0629 4/2 \u2013 \u0636\u0645\u0627\u0646 "
            "\u0627\u0644\u0623\u062f\u0627\u0621: \u064a\u0644\u062a\u0632\u0645 "
            "\u0627\u0644\u0645\u0642\u0627\u0648\u0644 \u0628\u062a\u0642\u062f\u064a\u0645 "
            "\u0636\u0645\u0627\u0646 \u0623\u062f\u0627\u0621 \u0639\u0628\u0631 \u0635\u0643 "
            "\u0635\u0627\u062f\u0631 \u0639\u0646 \u0628\u0646\u0643 \u0645\u0639\u062a\u0645\u062f "
            "\u0628\u0645\u0628\u0644\u063a \u064a\u0633\u0627\u0648\u064a 10% \u0645\u0646 "
            "\u0642\u064a\u0645\u0629 \u0627\u0644\u0639\u0642\u062f \u0627\u0644\u0645\u0648\u0627\u0641\u0642 "
            "\u0639\u0644\u064a\u0647\u060c \u0636\u0645\u0627\u0646\u0627\u064b \u0644\u062a\u0646\u0641\u064a\u0630 "
            "\u0627\u0644\u0645\u0642\u0627\u0648\u0644 \u0648\u0641\u0642\u0627\u064b \u0644\u0645\u0648\u0627\u0635\u0641\u0627\u062a "
            "\u0627\u0644\u0639\u0642\u062f. \u064a\u062c\u0628 \u0623\u0646 \u064a\u0643\u0648\u0646 "
            "\u0627\u0644\u0636\u0645\u0627\u0646 \u0635\u0627\u0644\u062d\u0627\u064b \u0644\u0645\u062f\u0629 "
            "\u0627\u0644\u0639\u0642\u062f \u0628\u0623\u0643\u0645\u0644\u0647\u060c \u0648\u062d\u062a\u0649 "
            "\u062a\u0633\u0644\u064a\u0645 \u0627\u0644\u0645\u0628\u0646\u0649 \u0648\u0625\u0635\u062f\u0627\u0631 "
            "\u0634\u0647\u0627\u062f\u0629 \u0627\u0644\u0627\u0633\u062a\u0648\u0641\u0627\u0621 \u0645\u0646 "
            "\u0627\u0644\u0645\u0647\u0646\u062f\u0633. \u0644\u0644\u063a\u064a\u0631 \u0627\u0644\u062d\u0642 "
            "\u0641\u064a \u0627\u0644\u0637\u0644\u0628 \u062a\u0633\u0644\u064a\u0645 \u0627\u0644\u0636\u0645\u0627\u0646 "
            "\u0628\u0645\u062c\u0631\u062f \u0625\u062e\u0637\u0627\u0631 \u0627\u0644\u0645\u0642\u0627\u0648\u0644 \u0644\u0648\u0627\u0626\u062d "
            "\u0627\u0644\u0636\u0645\u0627\u0646 \u062f\u0648\u0646 \u0623\u064a \u0634\u0631\u0637."
        ),
        "expected_output": RiskFinding(
            category="lg_bond",
            severity="high",
            clause_text=(
                "\u064a\u0644\u062a\u0632\u0645 \u0627\u0644\u0645\u0642\u0627\u0648\u0644 \u0628\u062a\u0642\u062f\u064a\u0645 "
                "\u0636\u0645\u0627\u0646 \u0623\u062f\u0627\u0621 \u0639\u0628\u0631 \u0635\u0643 \u0635\u0627\u062f\u0631 "
                "\u0639\u0646 \u0628\u0646\u0643 \u0645\u0639\u062a\u0645\u062f \u0628\u0645\u0628\u0644\u063a \u064a\u0633\u0627\u0648\u064a "
                "10% \u0645\u0646 \u0642\u064a\u0645\u0629 \u0627\u0644\u0639\u0642\u062f \u0627\u0644\u0645\u0648\u0627\u0641\u0642 "
                "\u0639\u0644\u064a\u0647\u060c \u0636\u0645\u0627\u0646\u0627\u064b \u0644\u062a\u0646\u0641\u064a\u0630 \u0627\u0644\u0645\u0642\u0627\u0648\u0644 "
                "\u0648\u0641\u0642\u0627\u064b \u0644\u0645\u0648\u0627\u0635\u0641\u0627\u062a \u0627\u0644\u0639\u0642\u062f. \u064a\u062c\u0628 "
                "\u0623\u0646 \u064a\u0643\u0648\u0646 \u0627\u0644\u0636\u0645\u0627\u0646 \u0635\u0627\u0644\u062d\u0627\u064b \u0644\u0645\u062f\u0629 "
                "\u0627\u0644\u0639\u0642\u062f \u0628\u0623\u0643\u0645\u0644\u0647\u060c \u0648\u062d\u062a\u0649 \u062a\u0633\u0644\u064a\u0645 "
                "\u0627\u0644\u0645\u0628\u0646\u0649 \u0648\u0625\u0635\u062f\u0627\u0631 \u0634\u0647\u0627\u062f\u0629 \u0627\u0644\u0627\u0633\u062a\u0648\u0641\u0627\u0621 "
                "\u0645\u0646 \u0627\u0644\u0645\u0647\u0646\u062f\u0633. \u0644\u0644\u063a\u064a\u0631 \u0627\u0644\u062d\u0642 \u0641\u064a "
                "\u0627\u0644\u0637\u0644\u0628 \u062a\u0633\u0644\u064a\u0645 \u0627\u0644\u0636\u0645\u0627\u0646 \u0628\u0645\u062c\u0631\u062f "
                "\u0625\u062e\u0637\u0627\u0631 \u0627\u0644\u0645\u0642\u0627\u0648\u0644 \u0644\u0648\u0627\u062d\u062d \u0627\u0644\u0636\u0645\u0627\u0646 "
                "\u062f\u0648\u0646 \u0623\u064a \u0634\u0631\u0637."
            ),
            explanation=(
                "Arabic equivalent of FIDIC Sub-Clause 4.2 (Performance "
                "Security). The Contractor must provide a performance bond in "
                "the form of a bank guarantee equal to 10% of the Accepted "
                "Contract Amount, valid until Taking Over and issuance of the "
                "Performance Certificate. The final sentence gives the Employer "
                "an unconditional right to call the bond on simple notice "
                "without any condition, which goes beyond the standard FIDIC "
                "wording. Severity is 'high' because the 10% amount is at the "
                "upper end of the FIDIC market norm (typically 5%–10%) and the "
                "unconditional-call wording is asymmetric."
            ),
            source_chunk_index=3,
            confidence=0.86,
        ).model_dump(),
    },
    {
        "input_chunk": (
            "Section 1.1 — Definitions. In this Contract, the following terms "
            "have the meanings set out below: 'Commencement Date' means the "
            "date notified by the Engineer under Sub-Clause 8.1; 'Time for "
            "Completion' means the period stated in the Appendix to the "
            "Contract, commencing on the Commencement Date. The Employer and "
            "Contractor shall keep records of all correspondence for a minimum "
            "of seven (7) years following the date of the Performance "
            "Certificate."
        ),
        "expected_output": RiskRadarOutput(findings=[]).model_dump(),
    },
]
