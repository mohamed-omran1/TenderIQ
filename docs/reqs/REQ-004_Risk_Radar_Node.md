# REQ-004: Risk Radar Node — FIDIC Clause Extraction



| Property | Value |
| --- | --- |
| **Status** | READY FOR IMPLEMENTATION

 |
| **Sprint** | Week 2 — Core Agents

 |
| **Priority** | P0 — Core differentiator. This is the highest-value, highest-risk node in the product.

 |
| **Dependencies** | REQ-003 complete (graph skeleton + stub node contract). Replaces the risk_radar stub only — does not touch graph wiring.

 |
| **Related Docs** | TenderIQ_PRD_v1.0 §1, §5.2 (Risk Radar responsibility) | TenderIQ_Architecture_v1.0 §3

 |

## Owning Component

| Risk Radar Node | Risk Clause Skill Package | risk_findings table |
| --- | --- | --- |
| app/agents/nodes/risk_radar.py

 | app/agents/skills/risk_clause_extraction.py

 | app/db/models.py

 |

---

## Description

Replace the risk_radar stub from REQ-003 with real LLM-based extraction. Given the tender chunks already retrieved and embedded (REQ-001), this node identifies and classifies risk-bearing clauses — FIDIC conditions, penalty clauses, letter-of-guarantee (LG) / performance bond requirements, and termination rights — and returns each finding with a severity rating and a plain-English explanation. This is the single feature most likely to differentiate TenderIQ from a generic chatbot, so its output quality is held to a measurable accuracy bar (see Acceptance Criteria), not just "looks reasonable."

This node does not replace the chunking or retrieval logic — it consumes the chunks already in TenderState and performs targeted retrieval + classification over them using a structured output schema, never free-form text.

---

## Preconditions

* REQ-003 Slice 1 complete: the graph compiles with risk_radar wired as a node between supervisor and aggregator.


* state["chunks"] is non-empty (guaranteed by Supervisor node validation from REQ-003).


* A labelled evaluation tender (at least one sample tender PDF with manually identified risk clauses) exists for accuracy testing — this is an Open Question from the PRD that must be resolved before this REQ is marked complete.


* CostTrackingHandler (REQ-003 Slice 3) is wired and ready to receive real on_llm_end events from this node's LLM calls.



---

## Main Flow

1. The risk_radar node receives TenderState containing chunks from the Ingestor (REQ-001).


2. The node retrieves the subset of chunks most likely to contain risk-relevant content using a targeted similarity search against a small set of "risk anchor" queries (e.g. "penalty for delay", "performance bond", "termination for default") rather than processing every chunk — this keeps token usage proportional to risk density, not document length.


3. Retrieved chunks are passed to the LLM with a structured output schema (see Data Requirements) requiring category, severity, clause_text (verbatim quote), and explanation (plain-English, in English regardless of source language) for every identified risk clause.


4. The LLM call is wrapped with the CostTrackingHandler callback (node_name="risk_radar") so every call is logged to llm_cost_events.


5. If the source chunk language is Arabic, the model is explicitly instructed to quote clause_text in the original Arabic but always produce explanation in English (per the PRD's English-first reporting decision).


6. The node deduplicates findings that reference the same underlying clause across overlapping chunks (chunks have a deliberate overlap from the Ingestor's chunking strategy).


7. The node writes state["risk_findings"] as a list of structured findings and returns the updated state to the graph — exactly matching the schema the Aggregator (REQ-003) already expects.



---

## Alternative Flows

| Condition | System Response | Resulting State |
| --- | --- | --- |
| LLM returns malformed structured output (schema violation) | Retry once with the same prompt. On second failure, log the raw response and proceed with risk_findings=[] rather than failing the whole run. | Run continues to aggregator with an empty findings list — never blocks the pipeline on this node alone.

 |
| No risk-relevant chunks found by anchor retrieval | Return risk_findings=[] — this is a valid, non-error outcome (a very short or simple tender may genuinely have few risk clauses). | Run continues normally.

 |
| LLM API call fails (network/rate-limit) | Retry with exponential backoff (3 attempts, consistent with REQ-001's embedding retry pattern). | On exhausted retries: this node fails and the graph-level failure handling from REQ-003 applies (analysis_runs.state = "failed").

 |
| A clause is ambiguous between two categories (e.g. penalty vs termination) | The model assigns the single most specific category and may note the ambiguity in explanation — multi-category tagging is out of scope for MVP. | Finding is recorded under one category only.

 |
| Document is bilingual and the same clause appears in both languages (parallel text) | Deduplicate by semantic similarity, not exact text match — keep the finding once, preferring the English version of clause_text if both are present. | One finding recorded, not two.

 |

---

## Postconditions

* state["risk_findings"] is always a list (possibly empty), never None — the Aggregator from REQ-003 depends on this invariant.


* Every finding includes a severity from a fixed enum (critical | high | medium | low) — free-text severity values are never produced.


* Every finding's clause_text is a verbatim substring traceable back to a specific tender_chunk — fabricated or paraphrased clause_text is treated as a defect, not an acceptable variation.


* At least one llm_cost_events row exists for this run with node_name="risk_radar" after this node completes (unless risk_findings=[] was returned via the zero-relevant-chunks path, where a retrieval-only call may still log embedding cost but not necessarily a generation cost).



---

## Data Requirements

### Structured Output Schema

```python
class RiskFinding(BaseModel):
    category: Literal[
        "fidic", "penalty", "lg_bond",
        "termination", "other"
    ]
    severity: Literal["critical", "high", "medium", "low"]
    clause_text: str   # verbatim quote from source chunk
    explanation: str   # plain-English, regardless of source language
    source_chunk_index: int   # traceability back to tender_chunks
    confidence: float  # 0.0-1.0, model self-reported certainty

class RiskRadarOutput(BaseModel):
    findings: list[RiskFinding]
```[cite: 3]

### risk_findings persistence
| Table | Fields Written | Notes |
| --- | --- | --- |
| risk_findings | id, run_id, category, severity, clause_text, explanation, source_chunk_index, confidence | One row per finding, written when analysis_runs transitions to "awaiting_hitl" (not incrementally during the LLM call) so a retry never produces duplicate rows.[cite: 3] |

---

## Risk Clause Extraction Skill Package
Per the harness/skills approach established for this project, the prompt, few-shot examples, and category taxonomy for this node live in a dedicated skill package file — not inline in the node code — so the extraction logic can be iterated on and evaluated independently of the node's control flow[cite: 3].

`app/agents/skills/risk_clause_extraction.py` contains: the system prompt, the FIDIC clause taxonomy reference, 4-6 few-shot examples (at least 2 in Arabic source text with English explanation output), and the severity rubric definition[cite: 3].

The severity rubric must be explicit and reproducible: 
* **critical**: clauses with uncapped or asymmetric financial liability[cite: 3];
* **high**: capped but materially significant penalties (>5% of contract value)[cite: 3];
* **medium**: standard penalty clauses within typical market range[cite: 3];
* **low**: administrative or procedural risk with minimal financial exposure[cite: 3].

---

## Non-Functional Requirements

### Performance
* For a 100-page bilingual tender, the risk_radar node must complete within 30 seconds — this runs in parallel with scorer and financial (REQ-005/006), so it should not become the bottleneck of the 90-second total ingestion+analysis target from the PRD[cite: 3].

### Accuracy (Evaluation Threshold)
* Recall on a labelled test tender: at least 85% of manually identified risk clauses must be found (PRD §3.2 success metric)[cite: 3]. This is measured via the /eval/run endpoint introduced in REQ-012 — until that REQ is implemented, accuracy is measured manually against the labelled sample tender[cite: 3].
* Precision is secondary to recall for MVP — a missed penalty clause (false negative) is more costly to a client than an over-flagged low-risk item (false positive), so the prompt should be tuned to favour catching borderline clauses over staying silent on them[cite: 3].

### Security
* clause_text and explanation must never be logged at INFO/DEBUG level outside of the persisted risk_findings table — tender content is commercially sensitive (Architecture §6.3)[cite: 3].

### Reliability
* A malformed LLM response must never crash the graph run — degrade to risk_findings=[] per Alternative Flows rather than propagating an unhandled exception[cite: 3].

---

## Implementation Slices
Each slice is implemented and reviewed independently. The agent must not expand scope beyond the declared files for each slice[cite: 3].

| Slice | Owns | Scope |
| --- | --- | --- |
| 1. Skill Package | agents/skills/risk_clause_extraction.py | Write the system prompt, FIDIC taxonomy reference, few-shot examples (including Arabic source examples), and severity rubric. No LangGraph or LangChain wiring in this file — pure prompt/schema content, independently reviewable by a non-engineer (e.g. a contracts professional) before it touches code.[cite: 3] |
| 2. Node Logic | agents/nodes/risk_radar.py, agents/state.py (no field changes — read only) | Implement the real risk_radar_node replacing the REQ-003 stub: anchor-query retrieval over chunks, structured LLM call using the Slice 1 skill package, deduplication logic, CostTrackingHandler wiring, and all Alternative Flow error handling. Must preserve the exact state["risk_findings"] schema the Aggregator already expects.[cite: 3] |
| 3. Persistence | db/models.py (RiskFinding model only), alembic migration | Add the risk_findings table and write logic that persists findings when the run reaches "awaiting_hitl" (not incrementally). This is separate from the in-memory state["risk_findings"] used during the graph run.[cite: 3] |
| 4. Frontend | components/RiskRadarTable.tsx (create), app/tenders/[id]/report/page.tsx (create — minimal, full report UI is REQ-008) | A table component showing findings grouped by severity with colour coding (critical=red, high=amber, medium=yellow, low=grey), each row showing category, clause_text (collapsible/truncated), and explanation. This is a preview view ahead of the full REQ-008 report — keep it minimal.[cite: 3] |
| 5. QA + Eval | tests/test_risk_radar.py, eval/labelled_sample_tender.json (create — manually labelled ground truth) | Unit tests for deduplication logic, malformed-output fallback, and schema validation using a mocked LLM. Separately, manually create a labelled ground-truth file for ONE real sample tender (from the PRD's open questions) and run the node against it to measure actual recall — this is not a unit test but a recorded accuracy measurement that becomes the baseline for REQ-012's automated eval harness.[cite: 3] |

### Slice Activation Rule
The project owner selects which slice is executed and when — this decision is never delegated to the AI agent[cite: 3]. Once a slice is selected, the agent may automatically activate whichever skill(s) best match that slice's declared scope (e.g. Slice 1 → senior-prompt-engineer; Slice 2 → agent-designer + rag-architect; Slice 5 → senior-qa)[cite: 3]. The agent must not expand scope to cover other slices, and must not select the next slice on its own[cite: 3].

---

## Acceptance Criteria / Definition of Done
* [ ] risk_radar_node replaces the REQ-003 stub and the graph still compiles and runs end-to-end without any change to graph.py[cite: 3].
* [ ] On the labelled sample tender, recall is >= 85% (this is the non-deterministic-output evaluation threshold — measured manually until REQ-012 automates it)[cite: 3].
* [ ] Every returned finding has clause_text that is a verbatim, traceable substring of its source_chunk_index — verified by a test that checks clause_text appears in the corresponding chunk content[cite: 3].
* [ ] A malformed LLM response (simulated in tests) results in risk_findings=[] and the graph continues to the aggregator without crashing[cite: 3].
* [ ] A bilingual tender with the same clause in Arabic and English produces exactly one finding, not two (deduplication verified by test)[cite: 3].
* [ ] At least one llm_cost_events row with node_name="risk_radar" exists after a successful run with non-empty findings[cite: 3].
* [ ] clause_text and explanation content do not appear in application logs at INFO or DEBUG level (verified by log inspection, consistent with REQ-002's financial_capacity precedent)[cite: 3].
* [ ] The severity rubric is documented in the skill package file with concrete thresholds (not vague language) and is reviewable independently of code[cite: 3].
* [ ] RiskRadarTable frontend component renders findings grouped and colour-coded by severity correctly for a sample run[cite: 3].

---

## Document Control
This REQ is the contract for implementation[cite: 3]. The risk_findings schema is final and must not change without updating REQ-003's Aggregator and REQ-008's Report Assembler[cite: 3]. The labelled sample tender produced in Slice 5 becomes a reusable asset for REQ-005, REQ-006, and REQ-012[cite: 3].


```