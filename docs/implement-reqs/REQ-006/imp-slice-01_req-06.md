Read the following documents before writing any code:
- docs/reqs/REQ-006_Financial_Analyst_Node.md
- docs/reqs/REQ-004_Risk_Radar_Node.md (skill package section
  only — for structural consistency reference)
- docs/reqs/REQ-005_Feasibility_Scorer_Node.md (skill package
  section only — for structural consistency reference)

You are implementing **REQ-006 — Slice 1 (Skill Package) only**.

This slice produces NO executable LangGraph/LangChain wiring code.
Pure prompt/schema content file — same pattern as:
- app/agents/skills/risk_clause_extraction.py (REQ-004)
- app/agents/skills/feasibility_scoring.py (REQ-005)

---

## Your scope (do not touch anything outside this list)
- app/agents/skills/financial_extraction.py (create)

---

## What to implement

### 1. Pydantic schemas — exactly as defined in REQ-006

  from pydantic import BaseModel, Field
  from typing import Literal

  class MonetaryValue(BaseModel):
      value:        float
      currency:     str = Field(
                        description="ISO 4217 code (e.g. SAR, AED, "
                        "EGP, USD) or the literal string 'UNKNOWN' "
                        "if currency cannot be determined")
      needs_review: bool = False

  class BondRequirement(BaseModel):
      bond_type:          Literal["performance", "advance_payment",
                                  "retention", "other"]
      amount:             MonetaryValue
      percentage:         float | None = Field(
                              default=None,
                              description="As % of contract value "
                              "if explicitly stated")
      conditions:         str = Field(
                              description="Plain-English summary of "
                              "bond conditions and when it can be "
                              "called")
      source_chunk_index: int

  class PaymentMilestone(BaseModel):
      description: str
      percentage:  float | None = None
      amount:      MonetaryValue | None = None
      trigger:     str = Field(
                       description="The event that triggers this "
                       "payment, e.g. 'on signing', 'on completion "
                       "of foundations', 'on taking-over certificate'")

  class LiquidatedDamages(BaseModel):
      rate:              MonetaryValue
      period:            str  # "per day", "per week", "per month"
      cap:               MonetaryValue | None = None
      cap_percentage:    float | None = None
      source_chunk_index: int

  class FinancialOutput(BaseModel):
      contract_value:      MonetaryValue | None = None
      bonds:               list[BondRequirement] = []
      liquidated_damages:  LiquidatedDamages | None = None
      payment_schedule:    list[PaymentMilestone] = []
      retention_rate:      float | None = None
      advance_payment:     MonetaryValue | None = None

### 2. Financial anchor queries constant

  FINANCIAL_ANCHOR_QUERIES = [
      "performance bond and bank guarantee requirements",
      "advance payment and mobilisation amount",
      "liquidated damages penalty per day or week",
      "retention money and defects liability period",
      "payment terms milestones and schedule",
      "contract value total price sum",
  ]

### 3. ISO 4217 currency normalisation map — as a constant

  CURRENCY_NORMALISATION = {
      # Arabic/common variants → ISO code
      "Riyals":         "SAR",
      "Saudi Riyals":   "SAR",
      "ريال":           "SAR",
      "ريال سعودي":     "SAR",
      "Dirhams":        "AED",
      "UAE Dirhams":    "AED",
      "درهم":           "AED",
      "Egyptian Pounds":"EGP",
      "جنيه":           "EGP",
      "جنيه مصري":      "EGP",
      "Dollars":        "USD",
      "US Dollars":     "USD",
      "Qatari Riyals":  "QAR",
      "ريال قطري":      "QAR",
      "Kuwaiti Dinars": "KWD",
      "دينار كويتي":    "KWD",
      "Bahraini Dinars":"BHD",
      "دينار بحريني":   "BHD",
      "Omani Riyals":   "OMR",
      "ريال عماني":     "OMR",
  }

  Add any other common GCC tender currency variants you are
  confident about. Flag any Arabic currency names you are
  not certain about rather than guessing.

### 4. System prompt — as a string constant

  FINANCIAL_SYSTEM_PROMPT = """..."""

Must instruct the model to:
  - Extract ONLY financial commitments explicitly stated in the
    provided chunks — never infer or calculate values not present
  - Store currency as stated — if the tender says "SAR 500,000"
    store currency="SAR", if it says "500,000 Riyals" the
    post-processing will normalise it via CURRENCY_NORMALISATION
  - If currency is genuinely ambiguous or not stated, set
    currency="UNKNOWN" and needs_review=true — never guess
  - For bonds expressed as a percentage of contract value:
    store both the percentage AND the computed absolute amount
    if contract_value is known — otherwise store percentage only
    and leave amount.value as 0.0 with needs_review=true
  - Payment schedule milestones must include the TRIGGER EVENT,
    not just the amount — "20% on completion of foundations"
    not just "20%"
  - If the same clause appears in Arabic and English,
    extract ONCE — prefer the version with more complete data
  - If a field is not mentioned in the tender, leave it as
    null or empty list — never fabricate financial data
  - contract_value must be the total contract price only —
    not a sum you compute from milestones

### 5. Few-shot examples — 3 examples as list of dicts

  FEW_SHOT_EXAMPLES = [...]

  Example 1 — English-only tender (construction, Saudi Arabia):
    A chunk containing: performance bond 10% of contract value,
    advance payment 15%, liquidated damages SAR 5,000/day capped
    at 10% of contract value, 5% retention.
    Expected FinancialOutput with all fields populated.

  Example 2 — Arabic-source chunk:
    A chunk in Arabic containing bond and payment terms.
    Expected FinancialOutput where currency is correctly
    identified from Arabic text using CURRENCY_NORMALISATION.
    For the Arabic chunk text: if you are not fully confident
    in the Arabic legal phrasing accuracy, write your best
    attempt and flag it explicitly in your summary —
    do not present uncertain Arabic as verified.

  Example 3 — Bilingual deduplication case:
    Two chunks — one in Arabic and one in English — describing
    the same liquidated damages clause. Expected output has
    exactly ONE LiquidatedDamages entry, not two. The English
    version is preferred when both are equally complete.

  Format:
  FEW_SHOT_EXAMPLES = [
      {
          "input_chunks": ["chunk text..."],
          "expected_output": FinancialOutput(...).model_dump(),
          "note": "..."  # explains what this example demonstrates
      },
      ...
  ]

---

## Rules
- ZERO LangChain or LangGraph imports in this file.
- ZERO async functions — pure data/config only.
- Do NOT write financial_analyst_node — that is Slice 2.
- Do NOT write tests — that is Slice 5.
- CURRENCY_NORMALISATION map must cover all 6 GCC currencies
  (SAR, AED, QAR, KWD, BHD, OMR) plus EGP and USD at minimum.
- For any Arabic text you include in few-shot examples,
  flag your confidence level explicitly in your summary —
  do not present uncertain Arabic legal phrasing as accurate.
- The system prompt must explicitly state the deduplication
  rule for bilingual tenders — this is the most common source
  of duplicate extraction errors in practice.
- Maintain structural consistency with REQ-004 and REQ-005
  skill packages — same file layout, same comment style,
  same separation of schemas / constants / prompt / examples.

---

## When you finish
Show me:
1. Full contents of app/agents/skills/financial_extraction.py
2. Show me the CURRENCY_NORMALISATION map — confirm it covers
   all 6 GCC currencies in both English and Arabic variants
3. For the Arabic few-shot example, explicitly state your
   confidence level in the Arabic text used:
   "HIGH — verified legal phrasing" or
   "MEDIUM — plausible but unverified" or
   "LOW — please review before using"
4. Confirm zero LangChain/LangGraph imports:
   grep -n "from langchain\|from langgraph" \
   app/agents/skills/financial_extraction.py
   (must return nothing)
5. Confirm the system prompt contains an explicit sentence
   about bilingual deduplication — show me that exact sentence

Do not move to Slice 2 until I explicitly tell you to.