# REQ-006: Financial Analyst Node — Bond & Commitment Extraction

| Property | Value |
| --- | --- |
| **Status** | READY FOR IMPLEMENTATION |
| **Sprint** | Week 2 — Core Agents (parallel with REQ-004 and REQ-005) |
| **Priority** | P0 — Financial summary is a required section of the final report and a primary client deliverable. |
| **Dependencies** | REQ-003 complete (graph skeleton, financial_analyst stub wired). Does not depend on REQ-004 or REQ-005 — runs in parallel. |
| **Related Docs** | TenderIQ_PRD_v1.0 §5.2 (Financial Analyst responsibility) \| TenderIQ_Architecture_v1.0 §3 |

## Owning Component

| Financial Analyst Node | Financial Extraction Skill Package | financial_commitments table |
| --- | --- | --- |
| app/agents/nodes/financial_analyst.py | app/agents/skills/financial_extraction.py | app/db/models.py |

---

## Description
Replace the financial_analyst stub from REQ-003 with real LLM-based extraction. The node identifies and normalises all financial commitments in the tender document — performance bonds, advance payment guarantees, retention amounts, liquidated damages caps, and payment schedules — into a structured summary that gives the CFO or finance lead a consolidated view of the company's financial exposure before bidding.

This node is the third parallel branch in the REQ-003 fan-out pattern. It runs concurrently with REQ-004 (Risk Radar) and REQ-005 (Feasibility Scorer) and feeds the Results Aggregator. Unlike REQ-005 which compares the tender against the company profile, REQ-006 makes no reference to the company profile — it only extracts what is stated in the tender document itself.

Currency normalisation is a core requirement: GCC tenders may quote values in EGP, SAR, AED, USD, or a mixture. All extracted values must be stored with their original currency code — never silently converted. If a tender states "SAR 500,000", it must be stored as value=500000, currency="SAR".

---

## Preconditions
* REQ-003 Slice 1 complete: graph compiles and financial_analyst is wired as a node between supervisor and aggregator.
* state["chunks"] is non-empty (guaranteed by Supervisor node validation).
* CostTrackingHandler (REQ-003 Slice 3) is wired and ready to receive real on_llm_end events from this node's LLM calls.
* No dependency on Company Profile — this node reads strictly from state["chunks"] and never looks up the profile.

---

## Main Flow
1. The financial_analyst node receives TenderState containing chunks.
2. The node retrieves financial-relevant chunks using financial anchor queries (e.g., bond requirements, payment terms, penalty amounts, retention clauses) rather than processing the entire document.
3. Retrieved chunks are passed to the LLM with a structured output schema (see Data Requirements) requiring explicit qualitative extraction per commitment category.
4. The LLM call is wrapped with the CostTrackingHandler callback (node_name="financial_analyst") so every call is logged to llm_cost_events.
5. For bilingual tenders, the model extracts from whichever language version provides the most complete financial data — parallel Arabic and English text for the same item must not result in duplicate entries.
6. All monetary values are stored with their original currency code (ISO 4217) — no silent conversion. If the tender states "SAR 500,000", it is captured as `{value: 500000, currency: "SAR"}`.
7. The node writes state["financial_summary"] as a structured dict and returns to the graph — matching the exact schema the Aggregator expects.

---

## Alternative Flows

| Condition | System Response | Resulting State |
| --- | --- | --- |
| LLM returns malformed structured output (schema violation) | Retry once with the same prompt. On second failure, log the raw response and proceed with financial_summary={"error": "Financial extraction unavailable — malformed LLM response", "bonds": [], "commitments": [], "payment_schedule": None} rather than failing the whole run. | Run continues to aggregator with the error summary. The analyst sees the error on the report page. |
| No financial-relevant chunks found by anchor retrieval | Fall back to scoring on the first 15 chunks ordered by chunk_index — a tender always contains some financial terms even if not in explicit clauses. | Ingestion continues based on reduced context. |
| LLM API call fails (network/rate-limit) | Retry with exponential backoff (3 attempts, consistent with REQ-004/005). | On exhausted retries: this node fails and graph-level failure handling from REQ-003 applies. |
| A monetary value does not have a specified currency | Store currency as "UNKNOWN" — do not guess or default to USD. Mark the item with needs_review: true. | Item is recorded in the summary with a needs_review flag for the analyst to verify. |
| Same financial clause appears in both Arabic and English (parallel text) | Deduplicate — keep one entry only, preferring the version with more complete metadata (e.g., explicit currency symbol vs implied). | One commitment recorded, not two. |
| Payment schedule spans multiple chunks | Merge milestone entries from all relevant chunks into a single cohesive schedule array. Do not truncate at chunk boundaries. | Complete payment schedule captured in output. |

---

## Postconditions
* state["financial_summary"] is always a dict — never None. On failure, it contains an "error" key and empty lists, not a null object.
* All monetary values include their original currency code — no naked numbers without currency are ever produced.
* Items with unknown currencies are marked with needs_review=true instead of being silently dropped.
* At least one llm_cost_events row exists for this run with node_name="financial_analyst" after this node completes.
* The shape of the financial_summary dict remains structurally identical whether extraction succeeded or failed — downstream Aggregator and Report Assembler will never crash due to missing keys.

---

## Data Requirements

### Structured Output Schema
```python
class MonetaryValue(BaseModel):
    value:        float
    currency:     str         # ISO 4217 or "UNKNOWN"
    needs_review: bool = False

class BondRequirement(BaseModel):
    bond_type:          Literal["performance", "advance_payment", "retention", "other"]
    amount:             MonetaryValue
    percentage:         float | None  # % of contract value if stated
    conditions:         str           # plain-English summary
    source_chunk_index: int

class PaymentMilestone(BaseModel):
    description: str
    percentage:  float | None  # % of contract value
    amount:      MonetaryValue | None
    trigger:     str           # what event triggers payment

class LiquidatedDamages(BaseModel):
    rate:               MonetaryValue  # per day/week amount
    period:             str            # "per day", "per week"
    cap:                MonetaryValue | None # max LD amount
    cap_percentage:     float | None   # cap as % of contract value
    source_chunk_index: int

class FinancialOutput(BaseModel):
    contract_value:     MonetaryValue | None
    bonds:              list[BondRequirement]
    liquidated_damages: LiquidatedDamages | None
    payment_schedule:   list[PaymentMilestone]
    retention_rate:     float | None   # % held until defects period
    advance_payment:    MonetaryValue | None
```

### financial_commitments table
| Column | Type | Notes |
| --- | --- | --- |
| **id** | UUID PK | Server-side default `gen_random_uuid()` |
| **run_id** | UUID FK | References `analysis_runs.id` |
| **commitment_type** | VARCHAR | `bond` \| `liquidated_damages` \| `payment_milestone` \| `retention` \| `advance_payment` \| `contract_value` |
| **amount_value** | FLOAT nullable | The raw numeric value |
| **amount_currency** | VARCHAR(10) | ISO 4217 code or "UNKNOWN" |
| **percentage** | FLOAT nullable | Stated percentage of contract value, if any |
| **description** | TEXT | Plain-English summary of this commitment |
| **needs_review** | BOOLEAN | True if currency is unknown or value is ambiguous |
| **source_chunk_index** | INTEGER nullable | Traceability link back to source chunk |

### State field written
```python
state["financial_summary"] = {
    "contract_value": {"value": 35000000.0, "currency": "SAR"},
    "bonds": [
        {
            "bond_type": "performance", 
            "amount": {"value": 3500000.0, "currency": "SAR", "needs_review": False}, 
            "percentage": 10.0, 
            "conditions": "...", 
            "source_chunk_index": 12
        }
    ],
    "liquidated_damages": {
        "rate": {"value": 5000.0, "currency": "SAR", "needs_review": False},
        "period": "per day",
        "cap": {"value": 500000.0, "currency": "SAR", "needs_review": False},
        "cap_percentage": 10.0,
        "source_chunk_index": 18
    },
    "payment_schedule": [...],
    "retention_rate": 5.0,
    "advance_payment": {"value": 3500000.0, "currency": "SAR", "needs_review": False},
}
```

---

## Financial Extraction Skill Package
Defined in `app/agents/skills/financial_extraction.py` — follows the same isolated paradigm as REQ-004 and REQ-005. Contains pure constants and Pydantic schemas, zero LangChain or LangGraph imports.

### Financial Anchor Queries
* `"performance bond and bank guarantee requirements"`
* `"advance payment and mobilisation amount"`
* `"liquidated damages penalty per day or week"`
* `"retention money and defects liability period"`
* `"payment terms milestones and schedule"`
* `"contract value total price sum"`

### Core Extraction Prompt Rules
* Never convert currencies — store the original value and currency exactly as stated.
* If the same amount appears in parallel Arabic and English text, extract it only once.
* If a bond is expressed as a percentage (e.g., "10% of contract value"), store both the percentage and the calculated amount if the `contract_value` is known — otherwise store the percentage only.
* Payment schedule milestones must include the trigger event (e.g., "upon signing", "completion of substructure"), not just the amount.

---

## Non-Functional Requirements

### Performance
* Must complete within 30 seconds — runs concurrently with REQ-004 and REQ-005, ensuring it does not add to the overall pipeline bottleneck.

### Data Integrity
* Currency keys are validated strictly against standard ISO 4217 formatting — verified by a Python validation step in the node and a test that injects an invalid currency string.

### Security
* Financial values (`amount_value`, `amount_currency`) must never appear in application logs outside the database persistence layer.

---

## Implementation Slices
Each slice is implemented and reviewed independently. The agent must not expand scope beyond the declared files for each slice.

| Slice | Owns | Scope |
| --- | --- | --- |
| 1. Skill Package | `agents/skills/financial_extraction.py` | Define schemas (`MonetaryValue`, `BondRequirement`, `PaymentMilestone`, `LiquidatedDamages`, `FinancialOutput`), anchors list, and system prompt. Pure constants, zero framework dependencies. |
| 2. Node Logic | `agents/nodes/financial_analyst.py` | Replace REQ-003 stub: anchor-query retrieval over chunks, structured LLM extraction, currency parsing, deduplication, `CostTrackingHandler` integration, and Alternative Flow schema violation/API fallbacks. |
| 3. Persistence | `db/models.py` (`FinancialCommitment` model only), alembic migration | Add `financial_commitments` table and implementation logic to persist extracted rows inside the existing `awaiting_hitl` state transition code block in `routers/tenders.py`. |
| 4. Frontend | `components/FinancialSummaryCard.tsx` (create) | Build UI tables displaying: Contract Value overview, Bonds breakdown, Liquidated Damages caps, and the structured Payment Schedule. Replace the static placeholder on `app/tenders/[id]/report/page.tsx`. |
| 5. QA | `tests/test_financial_analyst.py` | Code tests covering: currency validation, bilingual deduplication, handling of unknown currencies (`needs_review=true`), malformed output grace fallbacks, log privacy checks, and atomic DB commit integration. |

### Slice Activation Rule
The project owner selects which slice is executed and when — this decision is never delegated to the AI agent. Once a slice is selected, the agent may automatically activate whichever skill(s) best match that slice's declared scope. The agent must not expand scope to cover other slices, and must not select the next slice on its own.

---

## Acceptance Criteria / Definition of Done
* [ ] `financial_analyst_node` replaces the REQ-003 stub and the graph still compiles and runs end-to-end without any change to `graph.py`.
* [ ] Currency values match ISO 4217 format — verified by a Python validation step in the node and a test that injects an invalid currency string.
* [ ] A bilingual tender with the same financial clause in Arabic and English produces exactly one commitment entry, not two — verified by test.
* [ ] Items with unknown currency have `needs_review=true` — verified by test.
* [ ] A malformed LLM response results in `financial_summary` with an "error" key and empty bond/commitment lists — the graph continues without crashing.
* [ ] `financial_commitments` rows are persisted atomically with `risk_findings`, `feasibility_score`, and state transition — all four operations in a single `db.commit()`.
* [ ] Financial values (`amount_value`, `amount_currency`) do not appear in application logs — verified by log capture test.
* [ ] At least one `llm_cost_events` row with `node_name="financial_analyst"` exists after a successful run.
* [ ] `FinancialSummaryCard` renders contract value, bonds table, liquidated damages, and payment schedule correctly for a real sample run.
* [ ] The "Financial Summary — Coming in full report" placeholder in the report page is replaced by the real `FinancialSummaryCard`.

---

## Document Control
The `financial_summary` schema is final — REQ-008 (Report Assembler) reads from it directly. The atomic commit block in `routers/tenders.py` now covers REQ-004, REQ-005, and REQ-006 together — any future REQ that adds persistence at `awaiting_hitl` must extend this same block, never add a separate commit.