Read the following documents before writing any code:
- docs/reqs/REQ-004_Risk_Radar_Node.md
- docs/01_PRD.md (section 2.1 — Current Pain Points, for FIDIC context)

You are implementing **REQ-004 — Slice 1 (Skill Package) only**.

This slice produces NO executable LangGraph/LangChain wiring code.
It produces a pure prompt/schema content file that a non-engineer
(e.g. a contracts professional) could review and approve before
any code touches it.

---

## Your scope (do not touch anything outside this list)
- app/agents/skills/risk_clause_extraction.py (create)

---

## What to implement

Create a single Python file containing ONLY constants and Pydantic
schema definitions — no LangChain imports, no LangGraph imports,
no async functions, no node logic.

### 1. The Pydantic structured output schema

  from pydantic import BaseModel, Field
  from typing import Literal

  class RiskFinding(BaseModel):
      category: Literal["fidic", "penalty", "lg_bond", "termination", "other"]
      severity: Literal["critical", "high", "medium", "low"]
      clause_text: str = Field(description="Verbatim quote from the source chunk — never paraphrased")
      explanation: str = Field(description="Plain-English explanation, regardless of source language")
      source_chunk_index: int
      confidence: float = Field(ge=0.0, le=1.0)

  class RiskRadarOutput(BaseModel):
      findings: list[RiskFinding]

### 2. The severity rubric — as a documented constant

  SEVERITY_RUBRIC = {
      "critical": "Clauses with uncapped or asymmetric financial liability "
                  "(e.g. unlimited liquidated damages, one-sided termination "
                  "rights with no cure period).",
      "high": "Capped but materially significant penalties — typically "
              "exceeding 5% of contract value, or LG/bond conditions that "
              "are unusually onerous relative to market norms.",
      "medium": "Standard penalty clauses within typical market range "
                "(1-5% of contract value), or standard FIDIC conditions "
                "with normal cure periods.",
      "low": "Administrative or procedural risk with minimal direct "
             "financial exposure (e.g. notice period requirements, "
             "reporting obligations).",
  }

### 3. The FIDIC taxonomy reference — as a documented constant
A dict or list explaining the specific FIDIC clause numbers/concepts
the model should recognise, e.g. Sub-Clause 8.7 (Delay Damages),
Sub-Clause 15.2 (Termination by Employer), Sub-Clause 4.2 (Performance
Security). Research the standard FIDIC Red Book / Yellow Book clause
numbering — use Context7 or web search to confirm accuracy rather
than relying on memory, since getting clause numbers wrong here
would undermine the entire feature's credibility with a contracts
professional reviewer.

### 4. The system prompt — as a string constant

  RISK_RADAR_SYSTEM_PROMPT = """..."""

Must instruct the model to:
  - Only extract clauses that are ACTUALLY present in the provided
    chunks — never infer or fabricate clause text
  - Quote clause_text verbatim, including in the original language
    if the source is Arabic
  - Always write explanation in English regardless of source language
  - Assign exactly one category per finding (no multi-tagging)
  - Use the severity rubric above, citing the specific threshold
    that applies (e.g. "exceeds 5% of contract value")
  - Set confidence honestly — if uncertain between two categories,
    reflect that with a lower confidence score rather than guessing
  - Return an empty findings list if no risk clauses are found —
    never fabricate findings to seem thorough

### 5. Few-shot examples — as a list of constant dicts
Create 5 few-shot examples, each as a dict with input chunk text
and expected RiskFinding output:
  - 2 examples in English (one penalty clause, one termination clause)
  - 2 examples with Arabic source clause_text and English explanation
    (one FIDIC-style delay damages clause, one LG/bond clause)
  - 1 example demonstrating the "no risk found" case — input chunk
    is purely administrative/descriptive text, expected output is
    an empty findings list

Format:
  FEW_SHOT_EXAMPLES = [
      {
          "input_chunk": "...",
          "expected_output": RiskFinding(...).model_dump(),
      },
      ...
  ]

For the Arabic examples, use realistic Arabic contract/tender
language — if you are not confident in producing accurate Arabic
legal phrasing, say so explicitly in your summary rather than
guessing, so I can review and correct it myself.

---

## Rules
- This file must have ZERO LangChain or LangGraph imports.
- This file must have ZERO async functions — it is pure data/config.
- Do NOT write the risk_radar_node function — that is Slice 2.
- Do NOT write any tests — that is Slice 5.
- Every FIDIC clause reference must be verifiably accurate —
  flag any clause number you are not fully confident about rather
  than presenting uncertain information as fact.
- The severity rubric thresholds must be concrete numbers
  (e.g. "5% of contract value"), never vague language like
  "significant" or "reasonable" without a number attached.

---

## When you finish
Show me:
1. The full contents of app/agents/skills/risk_clause_extraction.py
2. Explicitly flag any FIDIC clause number or Arabic legal phrasing
   you are not fully confident about, so I can have it reviewed
3. Confirm zero LangChain/LangGraph imports exist in this file
   (grep for "from langchain" and "from langgraph" — should return nothing)

Do not move to Slice 2 until I explicitly tell you to.