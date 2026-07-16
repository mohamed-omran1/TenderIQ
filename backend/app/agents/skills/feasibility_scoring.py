"""
Feasibility Scoring — Skill Package (REQ-005, Slice 1).

This file is PURE DATA/CONFIG. It contains:
  1. Pydantic structured output schema (DimensionScore, FeasibilityOutput)
  2. SCORING_DIMENSIONS — concrete, reproducible 0/5/10/15/20 anchors per dim
  3. SCOPE_ANCHOR_QUERIES — tender-scope retrieval anchors (NOT risk anchors)
  4. FEASIBILITY_SYSTEM_PROMPT — instructions for the LLM
  5. FEW_SHOT_EXAMPLES — 3 examples (strong fit, poor fit, mixed fit)

It contains NO LangChain imports, NO LangGraph imports, and NO async
functions. Slice 2 (agents/nodes/feasibility_scorer.py) is the only file
allowed to wire these constants into a LangGraph node.

Mirrors the structure of `risk_clause_extraction.py` (REQ-004 Slice 1) so
that a developer reading one skill package can immediately read the other.
The two retrieval strategies are intentionally different and must remain
separate: Risk Radar retrieves risk-specific chunks via its own anchor
queries, Feasibility Scorer retrieves tender-scope chunks via the queries
defined here.

Reviewable by a non-engineer (e.g. a bid manager) before any code touches
the prompt or schema.
"""

from pydantic import BaseModel, Field


class DimensionScore(BaseModel):
    score: int = Field(
        ge=0,
        le=20,
        description=(
            "Score for this dimension, integer 0-20. 0 = complete mismatch "
            "per the anchor rubric; 20 = exact match. Never a float, never "
            "outside the 0-20 range — the node clamps out-of-range values."
        ),
    )
    rationale: str = Field(
        description=(
            "One sentence, in English, that references SPECIFIC data from "
            "the company profile AND specific data from the tender. "
            "Forbidden phrasings include, without limitation: 'good fit', "
            "'strong fit', 'reasonable fit', 'poor fit', 'acceptable', "
            "'matches well', 'has experience', 'has capacity', 'is capable'. "
            "Every rationale must cite at least one concrete profile value "
            "(e.g. 'Company max_project_value of EGP 20M', 'specialisations "
            "of civil, roads', 'past_projects list of 3 civil projects with "
            "value between EGP 8M and EGP 15M', 'available_bonding_capacity "
            "of EGP 3M') AND at least one concrete tender value (e.g. "
            "'tender value of EGP 35M', 'project location in SA', 'project "
            "duration of 24 months', 'required sector: dam construction'). "
            "If the profile data for this dimension is missing or null, the "
            "rationale must be exactly: 'Insufficient profile data for this "
            "dimension.' and the score must be 0."
        ),
    )


class FeasibilityOutput(BaseModel):
    technical_fit: DimensionScore = Field(
        description=(
            "Score and rationale for how well the tender's technical scope "
            "(civil, MEP, roads, etc.) matches the company's declared "
            "specialisations."
        ),
    )
    financial_capacity: DimensionScore = Field(
        description=(
            "Score and rationale for whether the tender's estimated value is "
            "within the company's max_project_value and whether "
            "available_bonding_capacity covers the required performance bond."
        ),
    )
    timeline: DimensionScore = Field(
        description=(
            "Score and rationale for whether the project duration and start "
            "date are feasible given the company's current commitments as "
            "declared in past_projects."
        ),
    )
    geographic_scope: DimensionScore = Field(
        description=(
            "Score and rationale for whether the project location is within "
            "the company's declared geographic_reach."
        ),
    )
    past_experience: DimensionScore = Field(
        description=(
            "Score and rationale for whether past_projects includes "
            "comparable projects in sector, scale, and complexity."
        ),
    )


SCORING_DIMENSIONS: dict[str, dict[str, object]] = {
    "technical_fit": {
        "description": (
            "Match between the tender's required technical scope (sector, "
            "discipline, project type) and the company's declared "
            "specialisations list. Each specialisation counts independently."
        ),
        "score_anchors": {
            0: (
                "Tender requires a sector or discipline that is NOT present "
                "in the company's specialisations list. Example: profile "
                "specialisations = [civil, roads] and tender requires MEP "
                "fit-out works; or profile has no specialisations declared."
            ),
            5: (
                "Tender scope overlaps with exactly one declared "
                "specialisation and the remaining scope is materially outside "
                "the company's declared disciplines. Example: profile has "
                "[civil, roads] and tender is 80% roads + 20% MEP, where MEP "
                "is non-trivial."
            ),
            10: (
                "Tender scope is fully covered by exactly one declared "
                "specialisation with no significant scope gaps. Example: "
                "profile has [civil, roads] and tender is a road construction "
                "project. One specialisation, one match, no stretch."
            ),
            15: (
                "Tender scope overlaps with two or more declared "
                "specialisations, with at most one minor sub-scope that the "
                "company has not previously delivered. Example: profile has "
                "[civil, roads, water] and tender is a road + drainage "
                "project, where drainage falls inside 'water'."
            ),
            20: (
                "Tender scope aligns exactly with all declared "
                "specialisations, with no gaps. Example: profile has "
                "[civil, roads, water] and tender is a road + drainage "
                "project where every sub-scope falls inside a declared "
                "specialisation."
            ),
        },
    },
    "financial_capacity": {
        "description": (
            "Adequacy of company financial resources to take on this tender, "
            "covering both the contract value (versus max_project_value) and "
            "the performance bond / LG requirement (versus "
            "available_bonding_capacity)."
        ),
        "score_anchors": {
            0: (
                "Tender value exceeds company max_project_value by more than "
                "50% (e.g. tender EGP 75M vs max_project_value EGP 50M), OR "
                "available_bonding_capacity is less than the required "
                "performance bond (e.g. bond requirement EGP 5M vs bonding "
                "capacity EGP 2M), OR max_project_value or "
                "available_bonding_capacity is missing/null in the profile."
            ),
            5: (
                "Tender value exceeds company max_project_value by 20%-50% "
                "(e.g. tender EGP 65M vs max_project_value EGP 50M), AND "
                "available_bonding_capacity covers the required bond."
            ),
            10: (
                "Tender value is within company max_project_value (≤ 100% of "
                "max), BUT available_bonding_capacity is less than 10% of "
                "tender value — i.e. the bond requirement is at the edge of "
                "what the company can bond."
            ),
            15: (
                "Tender value is within max_project_value (≤ 100% of max) "
                "AND available_bonding_capacity covers at least 10% of tender "
                "value (i.e. bond is comfortable), BUT tender value is "
                "between 80% and 100% of max_project_value (i.e. no headroom)."
            ),
            20: (
                "Tender value is less than 80% of max_project_value (e.g. "
                "tender EGP 30M vs max_project_value EGP 50M) AND "
                "available_bonding_capacity exceeds 15% of tender value "
                "(e.g. bonding capacity EGP 10M vs tender EGP 30M = 33%)."
            ),
        },
    },
    "timeline": {
        "description": (
            "Feasibility of the tender's project duration given the company's "
            "current workload, inferred from the average duration of "
            "past_projects entries. If past_projects is empty, this dimension "
            "is treated as having no profile data and scores 0."
        ),
        "score_anchors": {
            0: (
                "past_projects is empty or contains no projects with a "
                "derivable duration, OR tender duration is less than 50% of "
                "the average duration of past_projects of comparable sector "
                "(i.e. tender demands a delivery speed materially faster than "
                "anything the company has previously demonstrated)."
            ),
            5: (
                "Tender duration is 50%-75% of the average duration of "
                "comparable past_projects (i.e. tender demands materially "
                "faster delivery than the company's track record)."
            ),
            10: (
                "Tender duration is 75%-90% of the average duration of "
                "comparable past_projects (i.e. tender is faster than track "
                "record but within the same order of magnitude)."
            ),
            15: (
                "Tender duration is 90%-110% of the average duration of "
                "comparable past_projects (i.e. tender matches the "
                "company's typical delivery speed)."
            ),
            20: (
                "Tender duration is 110%-150% of the average duration of "
                "comparable past_projects (i.e. tender gives the company a "
                "comfortable time buffer relative to its track record)."
            ),
        },
    },
    "geographic_scope": {
        "description": (
            "Whether the tender's project location is within the company's "
            "declared geographic_reach (ISO 3166-1 alpha-2 country codes)."
        ),
        "score_anchors": {
            0: (
                "Tender country is NOT in company geographic_reach, OR "
                "geographic_reach is empty, OR geographic_reach is missing "
                "from the profile."
            ),
            5: (
                "Tender country is NOT in geographic_reach BUT is in the "
                "same region as a country in geographic_reach (e.g. profile "
                "has 'EG' and tender is in 'SA' — both MENA, but SA is not "
                "declared)."
            ),
            10: (
                "Tender country IS in geographic_reach BUT the tender "
                "specifies sub-regional or site-level locations (e.g. a "
                "specific governorate, remote site, offshore zone) that the "
                "company has not previously operated in."
            ),
            15: (
                "Tender country IS in geographic_reach AND the tender's "
                "sub-regions overlap with the operating history implied by "
                "past_projects entries (e.g. profile has 'EG' and past "
                "projects include work in Cairo or Alexandria, and the "
                "tender site is in Egypt's Delta region)."
            ),
            20: (
                "Tender country IS in geographic_reach AND the tender "
                "location matches the operating footprint of the company's "
                "past_projects (e.g. profile has 'EG' and 'SA', and past "
                "projects include work in the exact tender city/region)."
            ),
        },
    },
    "past_experience": {
        "description": (
            "Strength of the company's past_projects list as evidence of "
            "ability to deliver a comparable project, considering both sector "
            "match and scale match (relative to tender value)."
        ),
        "score_anchors": {
            0: (
                "past_projects is empty, OR contains zero projects in the "
                "tender's sector (no sector match at all)."
            ),
            5: (
                "past_projects contains exactly 1 project in the tender's "
                "sector, AND that project's value is less than 25% of the "
                "tender value (e.g. one civil project at EGP 5M vs tender "
                "EGP 30M)."
            ),
            10: (
                "past_projects contains 1-2 projects in the tender's sector, "
                "with values between 25% and 50% of the tender value (e.g. "
                "one or two civil projects at EGP 8M-15M vs tender EGP 30M)."
            ),
            15: (
                "past_projects contains 2-3 projects in the tender's sector, "
                "with values between 50% and 75% of the tender value (e.g. "
                "two or three civil projects at EGP 15M-22M vs tender "
                "EGP 30M)."
            ),
            20: (
                "past_projects contains 3 or more projects in the tender's "
                "sector, with at least one project value at 75% or more of "
                "the tender value (e.g. three or more civil projects, one "
                "of which is EGP 25M or more vs tender EGP 30M)."
            ),
        },
    },
}


SCOPE_ANCHOR_QUERIES: list[str] = [
    "project description and scope of work",
    "contract value and estimated budget",
    "project timeline completion date and duration",
    "project location and geographic requirements",
    "required certifications experience and qualifications",
]


FEASIBILITY_SYSTEM_PROMPT: str = """You are the Feasibility Scorer for TenderIQ, a B2B platform \
that analyses construction and procurement tenders for contractors in Egypt and the GCC.

Your single job: given a tender (represented by retrieved chunks) and a company profile \
(represented as structured fields), score how feasible this tender is for the company \
across EXACTLY FIVE DIMENSIONS, each on a 0-20 scale. The composite Go/No-Go score is the \
sum of those five numbers and is computed in Python from your output — you do not compute it.

YOU MUST FOLLOW THESE RULES WITHOUT EXCEPTION:

1. SCORE EVERY ONE OF THE FIVE DIMENSIONS.
   - The five dimensions are: technical_fit, financial_capacity, timeline, \
geographic_scope, past_experience. Every dimension is required, every time. \
A response that omits a dimension is a defect.
   - Each dimension's score is an integer in the range [0, 20]. Never a float, \
never outside this range, never null. The node clamps out-of-range values silently — \
do not rely on the clamp; emit a valid value.

2. USE THE SCORING RUBRIC. NEVER INVENT ANCHORS.
   - Use the SCORING_DIMENSIONS constant provided to you. The anchors at 0, 5, 10, 15, \
and 20 are CONCRETE: they cite numeric thresholds (percentages, counts, value ratios) \
and observable profile fields. Pick the anchor that best matches the evidence and \
assign that score. If the evidence falls between two anchors, pick the lower one.
   - Do not assign a score of 1, 2, 3, 4, 6, 7, 8, 9, 11, 12, 13, 14, 16, 17, 18, or 19. \
If the evidence is between two anchors, pick the LOWER one. The five discrete scores \
(0, 5, 10, 15, 20) are the only legal values.
   - Never use subjective language ("reasonable", "acceptable", "decent", "good enough") \
to justify a score. The score must follow the anchor's numeric threshold; the rationale \
below explains which threshold was hit.

3. WRITE A RATIONALE THAT REFERENCES SPECIFIC DATA. NEVER WRITE A GENERIC RATIONALE.
   - This is the single most important rule. A rationale that does not cite specific \
profile data AND specific tender data is a defect and will be rejected downstream.
   - FORBIDDEN RATIONALE PHRASES (non-exhaustive): "good fit", "strong fit", "reasonable \
fit", "acceptable fit", "poor fit", "limited fit", "matches well", "has experience", \
"has capacity", "is capable", "has the resources", "is well-positioned", "is a suitable \
match", "has done similar work". Any rationale that could be copy-pasted across \
different profiles and different tenders is too generic.
   - REQUIRED: every rationale must cite at least one concrete profile value (e.g. \
"Company max_project_value of EGP 20M", "specialisations of civil and roads", \
"past_projects list of 3 civil projects with values EGP 8M, EGP 12M, EGP 15M", \
"available_bonding_capacity of EGP 3M", "geographic_reach of EG, SA") AND at least one \
concrete tender value (e.g. "tender value of EGP 35M", "project location in SA", \
"project duration of 24 months", "required sector: dam construction", "required \
performance bond of 10% of contract value").
   - Cite numbers with units (EGP, USD, months, years, %, count). Cite profile fields \
by their schema name (max_project_value, specialisations, past_projects, \
geographic_reach, available_bonding_capacity). Cite tender values as they appear in \
the chunks.
   - One sentence. No preamble, no postamble, no second sentence, no parenthetical aside.

4. HANDLE MISSING PROFILE DATA WITH A FIXED PHRASE.
   - If the relevant profile field is missing, null, or empty for a given dimension, \
score that dimension EXACTLY 0 and write the rationale EXACTLY as: \
"Insufficient profile data for this dimension."
   - Do not invent profile values. Do not infer them from past_projects or any other \
field. If the schema field is absent, the data is absent.
   - The "Insufficient profile data for this dimension." phrase is the ONLY acceptable \
rationale for a 0 caused by missing data. Do not paraphrase it.

5. NEVER FABRICATE PROFILE OR TENDER DATA.
   - Only cite profile values that appear in the company profile provided to you. \
Only cite tender values that appear in the chunks provided to you. If a value you would \
have cited is not present, drop it from the rationale — do not approximate.
   - If a dimension's anchor requires a value (e.g. tender value) that is not in the \
chunks, score that dimension 0 with rationale "Insufficient profile data for this \
dimension." rather than guessing.

6. RETURN ALL FIVE DIMENSIONS, ALWAYS.
   - The output is a JSON object matching FeasibilityOutput: exactly five keys \
(technical_fit, financial_capacity, timeline, geographic_scope, past_experience), each \
mapping to {"score": int, "rationale": str}.
   - Do not include any text outside the JSON object. No preamble, no postamble, no \
markdown fences, no explanation of the composite score (the node computes the composite \
in Python).
   - The composite Go/No-Go score is the sum of the five scores you return. Do not \
compute it, do not include it, do not mention it.
"""


FEW_SHOT_EXAMPLES: list[dict] = [
    {
        "company_profile_summary": (
            "Company is a civil engineering contractor in Egypt. Profile: "
            "specialisations = [civil, roads]; financial_capacity.currency = "
            "EGP, financial_capacity.annual_turnover = 120_000_000, "
            "financial_capacity.available_bonding_capacity = 12_000_000; "
            "geographic_reach = [EG]; past_projects = [2 civil road projects "
            "in Egypt with values EGP 9M and EGP 13M, both in sector 'roads']; "
            "max_project_value = 50_000_000."
        ),
        "tender_scope_summary": (
            "Tender is a 24-month road construction project in Cairo, Egypt, "
            "estimated value EGP 30,000,000. Scope: earthworks, sub-base, "
            "asphalt paving, drainage. Required performance bond: 10% of "
            "contract value (EGP 3,000,000). Required sector experience: "
            "minimum 2 civil road projects of value EGP 5M+ in the last "
            "5 years."
        ),
        "expected_output": FeasibilityOutput(
            technical_fit=DimensionScore(
                score=20,
                rationale=(
                    "Tender scope (earthworks, sub-base, asphalt, drainage) "
                    "is fully covered by company specialisations of civil and "
                    "roads, with no sub-scope falling outside the declared "
                    "disciplines."
                ),
            ),
            financial_capacity=DimensionScore(
                score=20,
                rationale=(
                    "Tender value of EGP 30M is 60% of company "
                    "max_project_value of EGP 50M, and the required "
                    "performance bond of EGP 3M is 25% of "
                    "available_bonding_capacity of EGP 12M."
                ),
            ),
            timeline=DimensionScore(
                score=15,
                rationale=(
                    "Tender duration of 24 months falls within 90%-110% of "
                    "the company's typical road project duration implied by "
                    "past_projects (three civil road projects of comparable "
                    "scale, typical 18-30 months for this scope)."
                ),
            ),
            geographic_scope=DimensionScore(
                score=20,
                rationale=(
                    "Tender location in Cairo, EG is inside company "
                    "geographic_reach of [EG], and matches the operating "
                    "footprint of all three past_projects (all in Egypt)."
                ),
            ),
            past_experience=DimensionScore(
                score=10,
                rationale=(
                    "Company past_projects contains 2 civil road projects in "
                    "the tender's sector with values EGP 9M and EGP 13M "
                    "(30% and 43% of the tender value of EGP 30M), placing "
                    "the company in the 1-2 sector-matched projects at "
                    "25%-50% of tender value anchor."
                ),
            ),
        ).model_dump(),
    },
    {
        "company_profile_summary": (
            "Company is a mid-sized contractor based in Egypt. Profile: "
            "specialisations = [mep, civil]; financial_capacity.currency = "
            "EGP, financial_capacity.annual_turnover = 40_000_000, "
            "financial_capacity.available_bonding_capacity = 3_000_000; "
            "geographic_reach = [EG]; past_projects = [3 MEP fit-out projects "
            "in Egypt with values EGP 3M, EGP 5M, EGP 6M (durations 6, 12, "
            "and 18 months) and 1 civil infrastructure project in Egypt with "
            "value EGP 7M (duration 30 months)]; "
            "max_project_value = 12_000_000."
        ),
        "tender_scope_summary": (
            "Tender is a 36-month dam construction project in Riyadh, Saudi "
            "Arabia, estimated value SAR 180,000,000 (~EGP 2,400,000,000 at "
            "the rate used by the platform). Scope: civil works, mass "
            "concrete, hydraulic systems, site infrastructure. Required "
            "performance bond: 10% of contract value. Required sector "
            "experience: minimum 3 civil infrastructure projects of value "
            "USD 20M+ in the last 10 years."
        ),
        "expected_output": FeasibilityOutput(
            technical_fit=DimensionScore(
                score=5,
                rationale=(
                    "Tender scope is dam construction (mass concrete, "
                    "hydraulic systems), which overlaps with company "
                    "specialisation 'civil' but the hydraulic core and the "
                    "scale of the works are materially outside both "
                    "'mep' and the company's track-record definition of "
                    "'civil' (one small civil infrastructure project)."
                ),
            ),
            financial_capacity=DimensionScore(
                score=0,
                rationale=(
                    "Tender value of SAR 180M (approximately EGP 2.4B) "
                    "exceeds company max_project_value of EGP 12M by more "
                    "than four orders of magnitude, far above the 50% "
                    "overshoot threshold, and the 10% performance bond "
                    "(EGP ~240M) exceeds available_bonding_capacity of "
                    "EGP 3M."
                ),
            ),
            timeline=DimensionScore(
                score=15,
                rationale=(
                    "Tender duration of 36 months is within 90%-110% of the "
                    "company's longest past project (30-month civil "
                    "infrastructure project in past_projects) and "
                    "materially longer than the average of all past "
                    "projects (~16.5 months), placing the tender at the "
                    "edge of the typical-duration anchor."
                ),
            ),
            geographic_scope=DimensionScore(
                score=0,
                rationale=(
                    "Tender location in SA (Riyadh) is not in company "
                    "geographic_reach of [EG]."
                ),
            ),
            past_experience=DimensionScore(
                score=5,
                rationale=(
                    "Company past_projects contains 1 civil infrastructure "
                    "project in Egypt at EGP 7M, which is 0.0003% of the "
                    "tender value of EGP ~2.4B and therefore well below the "
                    "25%-of-tender-value threshold required for any score "
                    "above 0 in this dimension."
                ),
            ),
        ).model_dump(),
    },
    {
        "company_profile_summary": (
            "Company is an MEP contractor with strong sector credentials but "
            "limited financial scale. Profile: specialisations = [mep, "
            "fit-out]; financial_capacity.currency = EGP, "
            "financial_capacity.annual_turnover = 25_000_000, "
            "financial_capacity.available_bonding_capacity = 1_500_000; "
            "geographic_reach = [EG, SA]; past_projects = [2 MEP fit-out "
            "projects in Egypt with values EGP 3M and EGP 5M]; "
            "max_project_value = 10_000_000."
        ),
        "tender_scope_summary": (
            "Tender is a 30-month MEP fit-out project in Cairo, Egypt, "
            "estimated value EGP 25,000,000. Scope: HVAC, electrical, "
            "plumbing for a commercial tower. Required performance bond: 10% "
            "of contract value (EGP 2,500,000). Required sector experience: "
            "minimum 2 MEP projects of value EGP 5M+ in the last 5 years."
        ),
        "expected_output": FeasibilityOutput(
            technical_fit=DimensionScore(
                score=20,
                rationale=(
                    "Tender scope (HVAC, electrical, plumbing for commercial "
                    "fit-out) is fully covered by company specialisations of "
                    "[mep, fit-out], with no sub-scope falling outside the "
                    "declared disciplines."
                ),
            ),
            financial_capacity=DimensionScore(
                score=0,
                rationale=(
                    "Tender value of EGP 25M exceeds company "
                    "max_project_value of EGP 10M by 150% (well above the "
                    "50% overshoot threshold) and the required performance "
                    "bond of EGP 2.5M exceeds available_bonding_capacity of "
                    "EGP 1.5M."
                ),
            ),
            timeline=DimensionScore(
                score=15,
                rationale=(
                    "Tender duration of 30 months is within the 90%-110% "
                    "range of the company's typical MEP project duration "
                    "implied by past_projects (two MEP fit-out projects of "
                    "18-36 months duration)."
                ),
            ),
            geographic_scope=DimensionScore(
                score=20,
                rationale=(
                    "Tender location in Cairo, EG is inside company "
                    "geographic_reach of [EG, SA], and matches the operating "
                    "footprint of both past_projects (both in Egypt)."
                ),
            ),
            past_experience=DimensionScore(
                score=0,
                rationale=(
                    "Company past_projects contains 2 MEP fit-out projects "
                    "(EGP 3M and EGP 5M) which are in the tender's sector, "
                    "but the largest value (EGP 5M) is only 20% of the "
                    "tender value of EGP 25M, below the 25% threshold "
                    "required for any score above 0 under the rubric."
                ),
            ),
        ).model_dump(),
    },
]
