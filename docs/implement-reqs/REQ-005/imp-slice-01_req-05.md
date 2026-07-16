Read the following documents before writing any code:
- docs/reqs/REQ-005_Feasibility_Scorer_Node.md
- docs/reqs/REQ-004_Risk_Radar_Node.md (section: Skill Package —
  for consistency reference only, do not copy its content)

You are implementing **REQ-005 — Slice 1 (Skill Package) only**.

This slice produces NO executable LangGraph/LangChain wiring code.
It produces a pure prompt/schema content file — same principle as
REQ-004's skill package in app/agents/skills/risk_clause_extraction.py.

---

## Your scope (do not touch anything outside this list)
- app/agents/skills/feasibility_scoring.py (create)

---

## What to implement

### 1. Pydantic schemas

  from pydantic import BaseModel, Field
  from typing import Literal

  class DimensionScore(BaseModel):
      score:     int = Field(ge=0, le=20,
                   description="Score for this dimension, 0-20")
      rationale: str = Field(
                   description="One sentence referencing specific "
                   "profile data. Never generic language.")

  class FeasibilityOutput(BaseModel):
      technical_fit:      DimensionScore
      financial_capacity: DimensionScore
      timeline:           DimensionScore
      geographic_scope:   DimensionScore
      past_experience:    DimensionScore

### 2. Scoring dimension rubric — as a documented constant

  SCORING_DIMENSIONS = {
      "technical_fit": {
          "description": "...",
          "score_anchors": {
              0:  "...",   # complete mismatch
              5:  "...",   # partial overlap
              10: "...",   # moderate fit
              15: "...",   # strong fit with minor gaps
              20: "...",   # exact match
          }
      },
      # ... same structure for all 5 dimensions
  }

Define score_anchors for all 5 dimensions:
  technical_fit, financial_capacity, timeline,
  geographic_scope, past_experience

Each score anchor (0/5/10/15/20) must have a concrete,
unambiguous descriptor — not vague language like "good" or
"poor". Example for financial_capacity at score 0:
  "Tender value exceeds company max_project_value by more
   than 50% or available_bonding_capacity is insufficient
   to cover the required performance bond."

### 3. Tender-scope anchor queries — as a constant

  SCOPE_ANCHOR_QUERIES = [
      "project description and scope of work",
      "contract value and estimated budget",
      "project timeline completion date and duration",
      "project location and geographic requirements",
      "required certifications experience and qualifications",
  ]

### 4. System prompt — as a string constant

  FEASIBILITY_SYSTEM_PROMPT = """..."""

Must instruct the model to:
  - Score each of the 5 dimensions from 0 to 20 using the
    rubric anchors provided
  - Write a rationale for each dimension that references
    SPECIFIC data from the company profile and the tender —
    never write generic rationales like "company is a good fit"
  - Reference exact profile values in the rationale, e.g.:
    "Company max_project_value of EGP 20M is below the tender's
    estimated EGP 35M, indicating financial capacity gap."
  - Never invent profile data not present in the provided context
  - If profile data for a dimension is missing or null, score
    that dimension 0 with rationale:
    "Insufficient profile data for this dimension."
  - Return all 5 dimensions — never omit a dimension
  - Scores must be integers 0-20, never floats, never outside range

### 5. Few-shot examples — as a list of dicts
Create 3 few-shot examples:

  Example 1 — Strong fit (composite ~85):
    A civil engineering company with EGP 50M capacity bidding
    on a road construction tender in Egypt for EGP 30M.
    Show high scores across most dimensions with specific rationales.

  Example 2 — Poor fit (composite ~25):
    An MEP company bidding on a large dam construction project
    in Saudi Arabia where they have no geographic presence
    and the value far exceeds their capacity.
    Show low scores with specific rationales citing exact mismatches.

  Example 3 — Mixed fit (composite ~55):
    A company with strong technical fit but limited financial
    capacity and no past projects of comparable scale.
    Show varied scores per dimension.

  Format:
  FEW_SHOT_EXAMPLES = [
      {
          "company_profile_summary": "...",
          "tender_scope_summary": "...",
          "expected_output": FeasibilityOutput(...).model_dump(),
      },
      ...
  ]

---

## Rules
- ZERO LangChain or LangGraph imports in this file.
- ZERO async functions — pure data/config only.
- Do NOT write the feasibility_scorer_node function — Slice 2.
- Do NOT write any tests — Slice 5.
- score_anchors must use concrete language with specific
  thresholds (percentages, ranges) — never subjective adjectives
  like "good", "poor", "reasonable" without a measurable criterion.
- The rationale field description must explicitly forbid generic
  language — this is the most important prompt engineering
  decision in this file.
- Verify consistency with REQ-004's skill package structure —
  both files should follow the same pattern so a developer
  reading one can immediately understand the other.

---

## When you finish
Show me:
1. Full contents of app/agents/skills/feasibility_scoring.py
2. For each of the 5 dimensions, show me the score_anchors
   at 0 and 20 — I want to verify they are concrete and
   measurable, not vague
3. Confirm zero LangChain/LangGraph imports:
   grep -n "from langchain\|from langgraph" \
   app/agents/skills/feasibility_scoring.py
   (must return nothing)
4. Confirm the system prompt explicitly forbids generic
   rationales — show me the exact sentence in the prompt
   that enforces this

Do not move to Slice 2 until I explicitly tell you to.