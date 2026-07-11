Read the following documents before writing any code:
- docs/reqs/REQ-008_Report_Assembler.md
- docs/reqs/REQ-004_Risk_Radar_Node.md (skill package
  section — for structural consistency reference only)

You are implementing **REQ-008 — Slice 1 (Skill Package) only**.

This slice produces NO executable LangGraph/LangChain wiring code.
Pure prompt/schema content file — same pattern as:
- app/agents/skills/risk_clause_extraction.py (REQ-004)
- app/agents/skills/feasibility_scoring.py (REQ-005)
- app/agents/skills/financial_extraction.py (REQ-006)

---

## Your scope (do not touch anything outside this list)
- app/agents/skills/report_synthesis.py (create)

---

## What to implement

### 1. Pydantic schemas — exactly as defined in REQ-008

  from pydantic import BaseModel, Field
  from enum import Enum

  class GoNoGo(str, Enum):
      GO      = "GO"
      REVIEW  = "REVIEW"
      DECLINE = "DECLINE"

  class RiskSummaryItem(BaseModel):
      category:    str
      severity:    str
      description: str = Field(
          description="Plain-English, 1 sentence max. "
          "Never quote clause_text verbatim — summarise it."
      )

  class ReportOutput(BaseModel):
      go_no_go:              GoNoGo
      effective_score:       float
      is_analyst_override:   bool
      executive_summary:     str = Field(
          description="3-5 sentences summarising the tender "
          "and the overall recommendation. Never longer."
      )
      recommendation:        str = Field(
          description="Exactly 1 sentence. Clear and direct."
      )
      risk_summary:          list[RiskSummaryItem] = Field(
          max_length=5,
          description="Top 5 risks by severity only. "
          "Critical first, then high, medium, low."
      )
      feasibility_highlights: list[str] = Field(
          description="3-5 bullet points summarising the "
          "feasibility dimension scores. Each bullet references "
          "a specific dimension and its score."
      )
      financial_highlights:  list[str] = Field(
          description="3-5 bullet points on key financial "
          "commitments: contract value, bonds, LD, retention."
      )
      analyst_note:          str | None = Field(
          default=None,
          description="Required when is_analyst_override=True. "
          "States: Feasibility score adjusted from {ai_score} "
          "to {override_score} by analyst review."
      )

### 2. Go/No-Go thresholds constant

  GO_NO_GO_THRESHOLDS = {
      "GO":      (70.0, 100.0),  # score >= 70
      "REVIEW":  (40.0, 69.9),   # score 40-69
      "DECLINE": (0.0,  39.9),   # score < 40
  }

  def compute_go_no_go(effective_score: float) -> GoNoGo:
      """
      Deterministic Python function — never ask the LLM.
      Called in the node, not in the prompt.
      """
      if effective_score >= 70.0:
          return GoNoGo.GO
      elif effective_score >= 40.0:
          return GoNoGo.REVIEW
      else:
          return GoNoGo.DECLINE

### 3. System prompt constant

  REPORT_SYNTHESIS_PROMPT = """..."""

  Must instruct the model to:
  - Your role is SYNTHESIS, not analysis. All findings,
    scores, and financial data are provided as structured
    input. Do not add new analysis, inferences, or opinions
    not supported by the input data.
  - The Go/No-Go recommendation is provided to you as input
    (already computed) — do not override it with your own
    judgment. Use it exactly as given.
  - executive_summary must be 3-5 sentences maximum.
    First sentence: what the tender is about.
    Second sentence: the Go/No-Go recommendation and score.
    Third sentence: the most critical risk (if any).
    Fourth/fifth (optional): financial exposure summary.
  - recommendation must be exactly 1 sentence, starting
    with "We recommend" or "We advise against" or
    "We recommend careful review of".
  - risk_summary: include only the top 5 risks by severity.
    Summarise each in plain English — never quote verbatim
    clause text. Each description must be 1 sentence.
  - feasibility_highlights: 3-5 bullets, each referencing
    a specific dimension by name and its score out of 20.
    Example: "Technical Fit: 18/20 — strong alignment with
    company's civil engineering specialisation."
  - financial_highlights: 3-5 bullets covering: contract
    value, performance bond requirement, liquidated damages
    rate and cap (if present), retention rate.
  - analyst_note: if is_analyst_override is True, you MUST
    include this field with the exact text:
    "Feasibility score adjusted from {ai_score:.0f} to
    {override_score:.0f} by analyst review."
    If is_analyst_override is False, set to null.
  - Output language is always English regardless of the
    tender's source language.
  - Never fabricate data not present in the input.

### 4. Fallback report constant

  FALLBACK_REPORT = {
      "go_no_go":              "REVIEW",
      "effective_score":       0.0,
      "is_analyst_override":   False,
      "executive_summary":     "Automated report synthesis "
                               "encountered an error. Please "
                               "review the findings manually "
                               "using the sections above.",
      "recommendation":        "We recommend manual review of "
                               "all findings before making a "
                               "bid decision.",
      "risk_summary":          [],
      "feasibility_highlights": ["Feasibility data available "
                                 "in the analysis above."],
      "financial_highlights":  ["Financial data available in "
                                "the analysis above."],
      "analyst_note":          None,
  }

### 5. Few-shot examples — 3 examples

  REPORT_FEW_SHOT_EXAMPLES = [...]

  Example 1 — GO report (score=82, no override):
    A civil engineering company (strong technical fit,
    good financial capacity) bidding on a road construction
    tender in Egypt. Score 82. 2 medium risks, 1 low risk.
    Show full ReportOutput with GO recommendation,
    clear executive_summary, relevant highlights.

  Example 2 — DECLINE report (score=28, no override):
    An MEP company bidding on a large dam project in Saudi
    Arabia. Score 28. 2 critical risks (uncapped LD,
    performance bond exceeds bonding capacity), financial
    commitments far exceed company capacity.
    Show full ReportOutput with DECLINE recommendation.

  Example 3 — REVIEW report with analyst override:
    AI score=35 (would be DECLINE), analyst overrode to 65
    (REVIEW). Show ReportOutput where:
    - go_no_go = "REVIEW" (based on effective_score=65)
    - is_analyst_override = True
    - analyst_note = "Feasibility score adjusted from 35
      to 65 by analyst review."
    - executive_summary acknowledges the override

  Format:
  REPORT_FEW_SHOT_EXAMPLES = [
      {
          "context": {
              "effective_score": 82.0,
              "go_no_go": "GO",
              "is_analyst_override": False,
              "risk_findings": [...],
              "feasibility_breakdown": {...},
              "financial_summary": {...},
          },
          "expected_output": ReportOutput(...).model_dump(),
          "note": "GO report — strong fit, minor risks"
      },
      ...
  ]

---

## Rules
- ZERO LangChain or LangGraph imports.
- ZERO async functions — pure data/config only.
- compute_go_no_go() IS a function in this file — it is
  pure Python with no I/O, so it belongs here alongside
  the schema definitions.
- Do NOT write report_assembler_node — that is Slice 2.
- Do NOT write tests — that is Slice 5.
- FALLBACK_REPORT must be a plain dict (not a ReportOutput
  instance) so it can be returned directly without
  schema validation when LLM fails.
- risk_summary in few-shot examples must have max 5 items
  and descriptions must be 1 sentence each — model the
  quality you expect from the LLM.
- The REVIEW + override example (Example 3) is the most
  important — it demonstrates the analyst override flow
  that REQ-007 enables. Make it realistic.

---

## When you finish
Show me:
1. Full contents of app/agents/skills/report_synthesis.py
2. Run compute_go_no_go() for boundary values:
   python -c "
   from app.agents.skills.report_synthesis import (
       compute_go_no_go, GoNoGo
   )
   tests = [0.0, 39.9, 40.0, 69.9, 70.0, 100.0]
   for score in tests:
       result = compute_go_no_go(score)
       print(f'{score} → {result.value}')
   "
   Expected:
     0.0   → DECLINE
     39.9  → DECLINE
     40.0  → REVIEW
     69.9  → REVIEW
     70.0  → GO
     100.0 → GO
3. Confirm zero LangChain/LangGraph imports:
   grep -n "from langchain\|from langgraph" \
   app/agents/skills/report_synthesis.py
   (must return nothing)
4. Confirm FALLBACK_REPORT is a plain dict not a Pydantic
   instance — show me its type:
   python -c "
   from app.agents.skills.report_synthesis import FALLBACK_REPORT
   print(type(FALLBACK_REPORT))
   # Expected: <class 'dict'>
   "

Do not move to Slice 2 until I explicitly tell you to.