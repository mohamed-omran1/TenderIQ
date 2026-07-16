"""
Financial Extraction — Skill Package (REQ-006, Slice 1).

This file is PURE DATA/CONFIG. It contains:
  1. Pydantic structured output schemas
     (MonetaryValue, BondRequirement, PaymentMilestone,
     LiquidatedDamages, FinancialOutput)
  2. FINANCIAL_ANCHOR_QUERIES — financial-relevant retrieval anchors
     (different from REQ-004's risk anchors and REQ-005's scope anchors)
  3. CURRENCY_NORMALISATION — Arabic and common English variants of GCC
     currencies mapped to ISO 4217 codes
  4. FINANCIAL_SYSTEM_PROMPT — instructions for the LLM (English-only
     analyst-facing language; bilingual source handling)
  5. FEW_SHOT_EXAMPLES — 3 examples (EN-only Saudi, AR source, bilingual
     dedup)

It contains NO LangChain imports, NO LangGraph imports, and NO async
functions. Slice 2 (agents/nodes/financial_analyst.py) is the only file
allowed to wire these constants into a LangGraph node.

Mirrors the structure of `risk_clause_extraction.py` (REQ-004 Slice 1)
and `feasibility_scoring.py` (REQ-005 Slice 1) so that a developer
reading one skill package can immediately read the other. The three
retrieval strategies are intentionally different and must remain
separate:
    - Risk Radar → risk-specific chunks (REQ-004 anchors)
    - Feasibility Scorer → tender-scope chunks (REQ-005 anchors)
    - Financial Analyst → financial-specific chunks (this file's anchors)

Reviewable by a non-engineer (e.g. a CFO, finance lead, or contracts
professional) before any code touches the prompt or schema.
"""

from typing import Literal

from pydantic import BaseModel, Field


class MonetaryValue(BaseModel):
    value: float = Field(
        description=(
            "The raw numeric amount as stated in the tender, with no unit "
            "conversion applied. If the tender says '500,000', store 500000.0. "
            "If the tender says '5M' or '5 million', store 5000000.0. Do NOT "
            "subtract, sum, or derive this value from other fields — only the "
            "explicitly stated value goes here."
        )
    )
    currency: str = Field(
        description=(
            "ISO 4217 currency code (e.g. 'SAR', 'AED', 'EGP', 'USD'), or the "
            "literal string 'UNKNOWN' if the currency genuinely cannot be "
            "determined from the source chunk. CURRENCY_NORMALISATION maps "
            "Arabic and common English variants to their ISO code at "
            "post-processing time."
        )
    )
    needs_review: bool = Field(
        default=False,
        description=(
            "Set to True if currency is 'UNKNOWN', if the value is ambiguous, "
            "or if a percentage bond was stated without an absolute amount and "
            "contract_value was also unknown. The analyst must manually verify "
            "any item with needs_review=True before bidding."
        ),
    )


class BondRequirement(BaseModel):
    bond_type: Literal["performance", "advance_payment", "retention", "other"] = (
        Field(
            description=(
                "Category of bond/guarantee. 'performance' = performance bond "
                "or performance security (bank guarantee or surety). "
                "'advance_payment' = guarantee securing an advance or "
                "mobilisation payment. 'retention' = retention money guarantee "
                "or retention bond. 'other' = any other guarantee (parent "
                "company guarantee, esg bond, etc.) that does not fit the above."
            ),
        )
    )
    amount: MonetaryValue = Field(
        description=(
            "Absolute monetary value of the bond as stated in the tender. For "
            "a bond stated as a percentage of contract value, compute the "
            "absolute amount using contract_value if it is known in the same "
            "extraction; otherwise store amount.value=0.0 with needs_review=True "
            "and rely on the percentage field below."
        ),
    )
    percentage: float | None = Field(
        default=None,
        description=(
            "The bond amount as a percentage of contract value, if explicitly "
            "stated in the tender (e.g. 10.0 for '10% of contract value'). "
            "Store both the percentage AND the absolute amount when "
            "contract_value is known; store only the percentage when "
            "contract_value is unknown."
        ),
    )
    conditions: str = Field(
        description=(
            "Plain-English summary of the bond conditions: when it is "
            "released, when it can be called by the Employer, validity period, "
            "and any unusual call rights. Written in English regardless of "
            "source language."
        ),
    )
    source_chunk_index: int = Field(
        description=(
            "The 0-based index of the source chunk in the input chunks list. "
            "Used by the node to verify the bond's conditions are traceable "
            "back to a specific tender chunk before persistence."
        ),
    )


class PaymentMilestone(BaseModel):
    description: str = Field(
        description=(
            "Plain-English description of what is being paid for (e.g. "
            "'Mobilisation advance', 'Foundations completion', 'Taking-over "
            "certificate', 'Final account')."
        ),
    )
    percentage: float | None = Field(
        default=None,
        description=(
            "The milestone amount as a percentage of contract value, if "
            "explicitly stated (e.g. 10.0 for '10% on signing')."
        ),
    )
    amount: MonetaryValue | None = Field(
        default=None,
        description=(
            "The milestone amount as an absolute monetary value, if explicitly "
            "stated. Often only one of percentage or amount is stated — that "
            "is normal, not an error."
        ),
    )
    trigger: str = Field(
        description=(
            "The event that triggers this payment, e.g. 'on signing', "
            "'on completion of foundations', 'on taking-over certificate', "
            "'on submission of performance bond', 'monthly on IPC approval'. "
            "The trigger event is mandatory and must be a specific, "
            "verifiable event — never just 'on milestone' or 'TBD'."
        ),
    )


class LiquidatedDamages(BaseModel):
    rate: MonetaryValue = Field(
        description=(
            "The liquidated-damages rate per period (e.g. SAR 5,000 per day). "
            "This is the per-period penalty amount, NOT the total cap."
        ),
    )
    period: str = Field(
        description=(
            "The period over which the rate applies — exactly one of: "
            "'per day', 'per week', 'per month'. If the tender uses another "
            "period (e.g. 'per fortnight'), normalise to the closest of these "
            "three and preserve the original wording in the explanation if "
            "needed."
        ),
    )
    cap: MonetaryValue | None = Field(
        default=None,
        description=(
            "The maximum total liquidated damages payable, if stated. If the "
            "tender says 'uncapped' or omits any cap, leave this null — do not "
            "default to a percentage of contract value."
        ),
    )
    cap_percentage: float | None = Field(
        default=None,
        description=(
            "The cap expressed as a percentage of contract value (e.g. 10.0 "
            "for 'capped at 10% of contract value'). Store both cap and "
            "cap_percentage when the tender provides both, the same way as "
            "bonds."
        ),
    )
    source_chunk_index: int = Field(
        description=(
            "The 0-based index of the source chunk in the input chunks list."
        ),
    )


class FinancialOutput(BaseModel):
    contract_value: MonetaryValue | None = Field(
        default=None,
        description=(
            "The total contract value / Accepted Contract Amount as a single "
            "MonetaryValue. This is the OVERALL contract price, not a sum "
            "computed from payment milestones. Leave null if the tender does "
            "not state a total contract value."
        ),
    )
    bonds: list[BondRequirement] = Field(
        default_factory=list,
        description=(
            "List of all bonds and guarantees stated in the tender — "
            "performance, advance payment, retention, and any 'other'. Empty "
            "list if no bonds are stated."
        ),
    )
    liquidated_damages: LiquidatedDamages | None = Field(
        default=None,
        description=(
            "The single liquidated-damages clause for delay. Null if the "
            "tender does not include a liquidated-damages clause."
        ),
    )
    payment_schedule: list[PaymentMilestone] = Field(
        default_factory=list,
        description=(
            "List of payment milestones in the order they fall in the tender. "
            "Merge milestones from across multiple chunks into a single "
            "cohesive schedule — do not truncate at chunk boundaries. Empty "
            "list if no schedule is stated."
        ),
    )
    retention_rate: float | None = Field(
        default=None,
        description=(
            "Retention money as a percentage of each interim payment (e.g. "
            "5.0 for '5% retention'). Stored separately from the bonds list "
            "because retention is a deduction mechanism, not a separate "
            "guarantee instrument. Null if not stated."
        ),
    )
    advance_payment: MonetaryValue | None = Field(
        default=None,
        description=(
            "The mobilisation/advance payment as a single MonetaryValue. "
            "Stored separately from the bonds list because it is an inflow "
            "to the contractor (typically backed by an advance_payment bond "
            "which lives in the bonds list). Null if not stated."
        ),
    )


FINANCIAL_ANCHOR_QUERIES: list[str] = [
    "performance bond and bank guarantee requirements",
    "advance payment and mobilisation amount",
    "liquidated damages penalty per day or week",
    "retention money and defects liability period",
    "payment terms milestones and schedule",
    "contract value total price sum",
]


# ---------------------------------------------------------------------------
# Currency normalisation map.
#
# Coverage target (per REQ-006 Slice 1 spec): all six GCC currencies
# (SAR, AED, QAR, KWD, BHD, OMR) plus EGP and USD, in BOTH English and
# Arabic variants.
#
# Arabic-variant confidence notes (honest self-assessment):
#   - HIGH confidence: ريال سعودي (SAR), درهم إماراتي (AED), ريال قطري
#     (QAR), دينار كويتي (KWD), دينار بحريني (BHD), ريال عماني (OMR),
#     جنيه مصري (EGP), دولار أمريكي (USD). These are the standard
#     formal Arabic names of these currencies and are unambiguous.
#   - MEDIUM confidence: bare "درهم" (dirham) is mapped to AED because
#     GCC tenders use it in that sense, but the same Arabic word is
#     also used for MAD (Moroccan Dirham) and DZD (Algerian Dinar) in
#     North-African Arabic. The post-processor that consumes this map
#     should consider the tender's country/region before applying the
#     bare "درهم" entry; if the tender is clearly Moroccan or
#     Algerian, this mapping is wrong. Bare "ريال" (riyal) is similarly
#     ambiguous across SAR, QAR, OMR, and YER — treat as a hint, not a
#     definitive mapping.
#   - HIGH confidence on "Omani Rial" being the formal English name for
#     OMR (the imp-slice spec uses "Omani Riyals" which is a common
#     colloquialism; both forms are included).
# ---------------------------------------------------------------------------
CURRENCY_NORMALISATION: dict[str, str] = {
    # ---- Saudi Riyal (SAR) ------------------------------------------------
    "Riyals":          "SAR",
    "Saudi Riyals":    "SAR",
    "Saudi Riyal":     "SAR",
    "SR":              "SAR",
    "SAR":             "SAR",
    "ريال":            "SAR",   # MEDIUM: ambiguous with QAR/OMR/YER
    "ريال سعودي":      "SAR",   # HIGH
    "ريالات":           "SAR",   # HIGH (plural of ريال سعودي)
    "ريالات سعودية":    "SAR",   # HIGH

    # ---- UAE Dirham (AED) -------------------------------------------------
    "Dirhams":         "AED",
    "UAE Dirhams":     "AED",
    "UAE Dirham":      "AED",
    "AED":             "AED",
    "درهم":            "AED",   # MEDIUM: ambiguous with MAD/DZD
    "درهم إماراتي":     "AED",   # HIGH
    "دراهم":            "AED",   # MEDIUM: ambiguous
    "دراهم إماراتية":    "AED",   # HIGH

    # ---- Qatari Riyal (QAR) -----------------------------------------------
    "Qatari Riyals":   "QAR",
    "Qatari Riyal":    "QAR",
    "QAR":             "QAR",
    "ريال قطري":       "QAR",   # HIGH
    "ريالات قطرية":     "QAR",   # HIGH

    # ---- Kuwaiti Dinar (KWD) ----------------------------------------------
    "Kuwaiti Dinars":  "KWD",
    "Kuwaiti Dinar":   "KWD",
    "KD":              "KWD",
    "KWD":             "KWD",
    "دينار كويتي":      "KWD",   # HIGH
    "دنانير كويتية":    "KWD",   # HIGH

    # ---- Bahraini Dinar (BHD) ---------------------------------------------
    "Bahraini Dinars": "BHD",
    "Bahraini Dinar":  "BHD",
    "BHD":             "BHD",
    "دينار بحريني":     "BHD",   # HIGH
    "دنانير بحرينية":    "BHD",   # HIGH

    # ---- Omani Rial (OMR) -------------------------------------------------
    "Omani Riyals":    "OMR",   # common colloquial form
    "Omani Rial":      "OMR",   # formal ISO name
    "OMR":             "OMR",
    "ريال عماني":       "OMR",   # HIGH
    "ريالات عمانية":     "OMR",   # HIGH

    # ---- Egyptian Pound (EGP) ---------------------------------------------
    "Egyptian Pounds": "EGP",
    "Egyptian Pound":  "EGP",
    "EGP":             "EGP",
    "LE":              "EGP",   # common abbreviation in Egypt (Livre Égyptienne)
    "جنيه":            "EGP",   # HIGH in Egyptian context
    "جنيه مصري":       "EGP",   # HIGH
    "جنيهات":           "EGP",   # HIGH (plural)
    "جنيهات مصرية":      "EGP",   # HIGH

    # ---- US Dollar (USD) --------------------------------------------------
    "Dollars":         "USD",
    "US Dollars":      "USD",
    "US Dollar":       "USD",
    "USD":             "USD",
    "دولار":            "USD",   # MEDIUM: also used loosely for any hard currency
    "دولار أمريكي":     "USD",   # HIGH
    "دولارات":          "USD",   # MEDIUM
    "دولارات أمريكية":   "USD",   # HIGH
}


FINANCIAL_SYSTEM_PROMPT: str = """You are the Financial Analyst for TenderIQ, a B2B platform \
that analyses construction and procurement tenders for contractors in Egypt and the GCC.

Your single job: given a set of tender document chunks, extract every financial commitment \
the contractor will be required to make — contract value, bonds, advance payment, retention, \
liquidated damages, and the payment schedule — into a structured JSON object. You are the CFO's \
preparation step before bid pricing: a missed performance bond is a missed working-capital cost.

YOU MUST FOLLOW THESE RULES WITHOUT EXCEPTION:

1. EXTRACT ONLY WHAT IS EXPLICITLY STATED.
   - Only return a value if the financial commitment is ACTUALLY present in the provided \
chunks. Never infer, calculate, or 'fill in' a value that the tender did not state.
   - Never compute the contract value by summing the payment milestones. The contract_value \
field must be the total contract price as stated by the tender (e.g. 'Accepted Contract Amount', \
'Contract Price', 'Total Tender Value'). If the tender does not state a total contract value, \
leave contract_value=null. Do not derive it.
   - If a financial commitment is not mentioned in the tender, leave that field null or as an \
empty list. Never fabricate financial data. A null field is a valid output, not a failure.

2. STORE THE CURRENCY AS STATED. NEVER CONVERT.
   - Store the currency exactly as the tender states it. If the tender says 'SAR 500,000', \
store currency='SAR' and value=500000.0. If the tender says '500,000 Riyals' or \
'500,000 ريال سعودي', store currency='Riyals' or 'ريال سعودي' as-written; the post-processing \
step applies CURRENCY_NORMALISATION to map that to 'SAR' before persistence. The model does not \
do the conversion itself.
   - If the currency is genuinely ambiguous or not stated in the chunk, set currency='UNKNOWN' \
and needs_review=true. Never guess the currency. Never default to USD or to the local GCC \
currency of the issuer.

3. DEDUPLICATE BILINGUAL CLAUSES. EXTRACT ONCE, NOT TWICE.
   - If the same financial clause appears in BOTH Arabic and English (parallel-text tender), \
extract it ONCE — never produce two entries for the same underlying commitment. When both \
language versions are equally complete, prefer the ENGLISH version of any free-text field \
(conditions, trigger, description) so the English analyst can read it; otherwise prefer the \
version with the most complete metadata (e.g. the version that states an explicit currency code \
or a numeric percentage takes priority over one that does not). This is the most common source \
of duplicate-extraction errors in practice, and a double-counted bond or doubled liquidated-\
damages entry materially distorts the CFO's exposure view.

4. COMPUTE BOND AMOUNTS ONLY WHEN CONTRACT VALUE IS KNOWN.
   - For a bond stated as a percentage of contract value (e.g. '10% of contract value'), \
store BOTH the percentage (percentage=10.0) AND the computed absolute amount (amount.value = \
0.10 × contract_value.value) if contract_value is known in the same extraction. If \
contract_value is unknown, store the percentage only and set amount.value=0.0 with \
needs_review=true so the analyst can compute the absolute amount later.
   - The same rule applies to the cap on liquidated damages and to any retention or advance \
amount that the tender expresses as a percentage.

5. PAYMENT MILESTONES MUST INCLUDE THE TRIGGER EVENT.
   - Every payment milestone must have a non-empty trigger field that names the SPECIFIC event \
that causes the payment. '20% on completion of foundations' is correct. '20%' alone is a defect. \
'On milestone' or 'TBD' is a defect. Good trigger examples: 'on signing of Contract Agreement', \
'on submission of performance bond', 'monthly on certification of IPC by the Engineer', \
'on completion of substructure', 'on taking-over certificate', 'on submission of retention \
guarantee'.

6. NEVER DOUBLE-COUNT THE SAME BOND.
   - If the same bond type (performance, advance_payment, retention) appears in multiple \
chunks, keep ONE entry per bond type unless the tender explicitly lists multiple distinct \
instruments of the same type (which is rare — most tenders have one performance bond, one \
advance-payment guarantee, and one retention mechanism). When in doubt, prefer the entry with \
the most complete metadata (explicit percentage, explicit currency, explicit conditions).

7. MERGE MILESTONES ACROSS CHUNKS.
   - The payment schedule in a tender is usually split across several chunks (one chunk for \
signing, one for foundations, one for taking-over, etc.). Merge all of them into a SINGLE \
payment_schedule list in the order they appear in the tender. Do not truncate at chunk \
boundaries. Do not emit one FinancialOutput per chunk — emit ONE FinancialOutput that covers \
all chunks.

8. LIQUIDATED DAMAGES IS A SINGLE OBJECT, NOT A LIST.
   - The tender has one delay-damages clause. Return it as a single LiquidatedDamages object \
(not a list). If the tender genuinely has separate per-day and per-week schedules, return the \
one with the explicit period and the explicit cap — not both.

9. NEVER FABRICATE TO SEEM THOROUGH.
   - A short output is better than a padded one. If the tender states only a performance bond \
and a contract value, return exactly that — not invented retention, advance payment, or \
liquidated damages entries. Returning 3 accurate fields is better than 7 fields where 4 are \
guessed.

10. WRITE PLAIN-ENGLISH ANALYST-FACING TEXT.
    - All free-text fields (conditions, description, trigger) are written in PLAIN ENGLISH \
regardless of source chunk language. The TenderIQ UI is English-only and the analyst is reading \
in English. Quote the source language in the explanation if useful context, but the field \
itself must be English.

OUTPUT FORMAT:
    - You MUST return your answer as a JSON object matching the FinancialOutput schema: \
{"contract_value": MonetaryValue | null, "bonds": [BondRequirement, ...], "liquidated_damages": \
LiquidatedDamages | null, "payment_schedule": [PaymentMilestone, ...], "retention_rate": float \
| null, "advance_payment": MonetaryValue | null}.
    - All list fields default to empty lists, not null, when no items are present.
    - source_chunk_index is the 0-based index of the chunk in the input list where the value \
was stated (or the FIRST chunk in which a value appears, for items merged across chunks).
    - Do not include any text outside the JSON object. No preamble, no postamble, no markdown \
fences, no explanation of your reasoning.
"""


# ---------------------------------------------------------------------------
# Few-shot examples.
#
# Three examples are required by REQ-006 Slice 1:
#   1. English-only Saudi construction tender (full FinancialOutput).
#   2. Arabic-source chunk (currency identified via CURRENCY_NORMALISATION).
#   3. Bilingual deduplication case (Arabic + English of the same clause).
#
# Arabic-text confidence for Example 2 and Example 3 (chunks in Arabic):
#   MEDIUM — plausible but unverified. The vocabulary (ضمان, كفالة,
#   تعويضات, دفعة مقدمة, دفعة مقدمة, غرامات) and FIDIC-style clause
#   numbering (4/2, 8/7, 14/2) are consistent with standard
#   construction-tender Arabic, and the terms are high-confidence
#   translations of FIDIC's English clause titles. The exact phrasing
#   used here is the model's best effort and should be reviewed by a
#   bilingual contracts professional before being treated as verified
#   legal text.
# ---------------------------------------------------------------------------
FEW_SHOT_EXAMPLES: list[dict] = [
    # ---- Example 1: English-only Saudi construction tender -----------------
    {
        "input_chunks": [
            (
                "Section 4.2 — Performance Security. The Contractor shall provide "
                "a Performance Security in the form of an unconditional bank "
                "guarantee from a bank approved by the Employer, in the amount of "
                "10% of the Accepted Contract Amount. The guarantee shall remain "
                "valid until the Employer has issued the Performance Certificate."
            ),
            (
                "Section 14.2 — Advance Payment. The Employer shall make an advance "
                "payment of 15% of the Accepted Contract Amount to the Contractor "
                "upon submission of the Performance Security and the Advance "
                "Payment Guarantee. The advance shall be repaid by deductions from "
                "each Interim Payment Certificate at a rate of 25% of the amount of "
                "each IPC."
            ),
            (
                "Section 8.7 — Delay Damages. If the Contractor fails to comply "
                "with Sub-Clause 8.5 [Time for Completion], the Contractor shall "
                "pay Delay Damages to the Employer at the rate of SAR 5,000 per "
                "day. The total amount of Delay Damages shall not exceed 10% of "
                "the Accepted Contract Amount. The Accepted Contract Amount is "
                "SAR 35,000,000."
            ),
            (
                "Section 14.3 — Application for Interim Payments. The Contractor "
                "shall submit an IPC monthly. The Employer shall retain 5% of each "
                "IPC until the Defects Notification Period ends, at which point "
                "the retained amount is released in two halves: half on the "
                "Taking-Over Certificate and half on the Performance Certificate."
            ),
        ],
        "expected_output": FinancialOutput(
            contract_value=MonetaryValue(
                value=35_000_000.0,
                currency="SAR",
                needs_review=False,
            ),
            bonds=[
                BondRequirement(
                    bond_type="performance",
                    amount=MonetaryValue(
                        value=3_500_000.0,
                        currency="SAR",
                        needs_review=False,
                    ),
                    percentage=10.0,
                    conditions=(
                        "Unconditional bank guarantee from an Employer-approved "
                        "bank, valid until issuance of the Performance "
                        "Certificate."
                    ),
                    source_chunk_index=0,
                ),
                BondRequirement(
                    bond_type="advance_payment",
                    amount=MonetaryValue(
                        value=5_250_000.0,
                        currency="SAR",
                        needs_review=False,
                    ),
                    percentage=15.0,
                    conditions=(
                        "Advance Payment Guarantee, returned upon full repayment "
                        "of the advance by deductions from each IPC at 25% of "
                        "the IPC amount."
                    ),
                    source_chunk_index=1,
                ),
            ],
            liquidated_damages=LiquidatedDamages(
                rate=MonetaryValue(
                    value=5_000.0,
                    currency="SAR",
                    needs_review=False,
                ),
                period="per day",
                cap=MonetaryValue(
                    value=3_500_000.0,
                    currency="SAR",
                    needs_review=False,
                ),
                cap_percentage=10.0,
                source_chunk_index=2,
            ),
            payment_schedule=[
                PaymentMilestone(
                    description="Mobilisation advance payment",
                    percentage=15.0,
                    amount=MonetaryValue(
                        value=5_250_000.0,
                        currency="SAR",
                        needs_review=False,
                    ),
                    trigger=(
                        "on submission of the Performance Security and the "
                        "Advance Payment Guarantee"
                    ),
                ),
                PaymentMilestone(
                    description="Interim Payment Certificates (monthly)",
                    percentage=None,
                    amount=None,
                    trigger=(
                        "monthly on certification of the IPC by the Engineer, "
                        "subject to 5% retention"
                    ),
                ),
                PaymentMilestone(
                    description="First half of retention release",
                    percentage=2.5,
                    amount=None,
                    trigger="on issuance of the Taking-Over Certificate",
                ),
                PaymentMilestone(
                    description="Second half of retention release",
                    percentage=2.5,
                    amount=None,
                    trigger=(
                        "on issuance of the Performance Certificate at the end "
                        "of the Defects Notification Period"
                    ),
                ),
            ],
            retention_rate=5.0,
            advance_payment=MonetaryValue(
                value=5_250_000.0,
                currency="SAR",
                needs_review=False,
            ),
        ).model_dump(),
        "note": (
            "English-only Saudi construction tender. All four FinancialOutput "
            "categories are populated. The performance bond and the advance "
            "payment are stored with both the percentage (10%, 15%) AND the "
            "absolute amount, computed from the SAR 35M contract value stated "
            "in the same chunk set. Retention is stored separately as a 5% "
            "rate. The payment schedule is a 4-item merged list spanning all "
            "four input chunks — not truncated at chunk boundaries."
        ),
    },

    # ---- Example 2: Arabic-source chunk -----------------------------------
    {
        "input_chunks": [
            (
                "البند 4/2 – ضمان الأداء: يلتزم المقاول بتقديم ضمان أداء "
                "بموجب كفالة بنكية غير مشروطة صادرة عن بنك معتمد لدى صاحب "
                "العمل، بمبلغ يساوي 10% من قيمة العقد، على أن يظل الضمان "
                "صالحاً حتى إصدار شهادة حسن التنفيذ من صاحب العمل."
            ),
            (
                "البند 14/2 – الدفعة المقدمة: يستحق المقاول دفعة مقدمة بنسبة "
                "15% من قيمة العقد عند تقديم ضمان الأداء وكفالة الدفعة "
                "المقدمة. تسترد الدفعة المقدمة بخصم 25% من قيمة كل شهادة "
                "دفع مرحلية."
            ),
            (
                "البند 8/7 – تعويضات التأخير: في حال عدم التزام المقاول "
                "بالمواعيد المحددة لإتمام الأعمال وفقاً للبند 8/5، يلتزم "
                "المقاول بدفع تعويضات عن التأخير لصالح صاحب العمل بواقع "
                "5,000 ريال سعودي عن كل يوم تأخير، على ألا يتجاوز إجمالي "
                "التعويضات 10% من قيمة العقد. قيمة العقد المقبولة هي "
                "35,000,000 ريال سعودي."
            ),
        ],
        "expected_output": FinancialOutput(
            contract_value=MonetaryValue(
                value=35_000_000.0,
                currency="ريال سعودي",
                needs_review=False,
            ),
            bonds=[
                BondRequirement(
                    bond_type="performance",
                    amount=MonetaryValue(
                        value=3_500_000.0,
                        currency="ريال سعودي",
                        needs_review=False,
                    ),
                    percentage=10.0,
                    conditions=(
                        "Unconditional bank guarantee from an Employer-approved "
                        "bank, valid until issuance of the Performance "
                        "Certificate."
                    ),
                    source_chunk_index=0,
                ),
                BondRequirement(
                    bond_type="advance_payment",
                    amount=MonetaryValue(
                        value=5_250_000.0,
                        currency="ريال سعودي",
                        needs_review=False,
                    ),
                    percentage=15.0,
                    conditions=(
                        "Advance Payment Guarantee, returned upon full repayment "
                        "of the advance by deductions from each IPC at 25% of "
                        "the IPC amount."
                    ),
                    source_chunk_index=1,
                ),
            ],
            liquidated_damages=LiquidatedDamages(
                rate=MonetaryValue(
                    value=5_000.0,
                    currency="ريال سعودي",
                    needs_review=False,
                ),
                period="per day",
                cap=MonetaryValue(
                    value=3_500_000.0,
                    currency="ريال سعودي",
                    needs_review=False,
                ),
                cap_percentage=10.0,
                source_chunk_index=2,
            ),
            payment_schedule=[
                PaymentMilestone(
                    description="Mobilisation advance payment",
                    percentage=15.0,
                    amount=MonetaryValue(
                        value=5_250_000.0,
                        currency="ريال سعودي",
                        needs_review=False,
                    ),
                    trigger=(
                        "on submission of the Performance Security and the "
                        "Advance Payment Guarantee"
                    ),
                ),
            ],
            retention_rate=None,
            advance_payment=MonetaryValue(
                value=5_250_000.0,
                currency="ريال سعودي",
                needs_review=False,
            ),
        ).model_dump(),
        "note": (
            "Arabic-source chunk set. The model stores currency as 'ريال سعودي' "
            "(the exact Arabic form present in the source). The post-processor "
            "applies CURRENCY_NORMALISATION to map 'ريال سعودي' → 'SAR' before "
            "persistence. Free-text fields (conditions, description, trigger) "
            "are in English regardless of source language. The retention_rate "
            "is null because no retention clause is present in these three "
            "chunks. Arabic-text confidence: MEDIUM — plausible but "
            "unverified (see module docstring)."
        ),
    },

    # ---- Example 3: Bilingual deduplication case --------------------------
    {
        "input_chunks": [
            (
                "البند 8/7 – تعويضات التأخير: في حال عدم التزام المقاول "
                "بالمواعيد المحددة لإتمام الأعمال، يلتزم بدفع تعويضات بواقع "
                "4,000 ريال سعودي عن كل يوم تأخير، على ألا يتجاوز إجمالي "
                "التعويضات 10% من قيمة العقد."
            ),
            (
                "Section 8.7 — Liquidated Damages for Delay. If the Contractor "
                "fails to achieve Time for Completion, the Contractor shall "
                "pay Liquidated Damages at the rate of SAR 4,000 per day, "
                "capped at 10% of the Accepted Contract Amount."
            ),
        ],
        "expected_output": FinancialOutput(
            contract_value=None,
            bonds=[],
            liquidated_damages=LiquidatedDamages(
                rate=MonetaryValue(
                    value=4_000.0,
                    currency="SAR",
                    needs_review=False,
                ),
                period="per day",
                cap=None,
                cap_percentage=10.0,
                source_chunk_index=1,
            ),
            payment_schedule=[],
            retention_rate=None,
            advance_payment=None,
        ).model_dump(),
        "note": (
            "Bilingual deduplication case. The Arabic chunk (index 0) and "
            "the English chunk (index 1) describe the SAME liquidated- "
            "damages clause with identical monetary value (4,000), period "
            "(per day) and cap (10%). The output contains exactly ONE "
            "LiquidatedDamages entry, not two. The English chunk is "
            "preferred as the source_chunk_index because both versions are "
            "equally complete and the analyst reads in English. cap is null "
            "because no absolute cap amount is stated in either chunk — only "
            "the 10% percentage — so the absolute cap is left for the "
            "post-processor to compute from contract_value (which is "
            "unknown here). Arabic-text confidence: MEDIUM — plausible but "
            "unverified."
        ),
    },
]
