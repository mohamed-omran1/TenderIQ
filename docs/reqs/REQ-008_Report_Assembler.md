```markdown
# REQ-008: Report Assembler — Go/No-Go Brief Generation

| Status | READY FOR IMPLEMENTATION |
| :--- | :--- |
| **Sprint** | Week 3 — HITL + Streaming |
| **Priority** | P0 — This completes the MVP pipeline. After this REQ, TenderIQ has a full demo-able end-to-end flow. |
| **Dependencies** | REQ-007 complete (HITL gate working, graph resumes after approval). hitl_override_score available in TenderState. REQ-004/005/006 complete (real agent outputs in aggregated_results). |
| **Related Docs** | TenderIQ_PRD_v1.0 §1 (Executive Summary), §5.2 (Report Assembler) \| TenderIQ_Architecture_v1.0 §2 (Request Lifecycle step 10-11) |

---

### Owning Component

| Report Assembler Node | Report Synthesis Skill Package | Full Report Page (Frontend) |
| :--- | :--- | :--- |
| app/agents/nodes/report_assembler.py | app/agents/skills/report_synthesis.py | "app/tenders/[id]/report/full/page.tsx" |

---

### Description
[cite_start]Replace the report_assembler stub from REQ-003 with a real LLM-based report synthesis node[cite: 7].

[cite_start]After the analyst approves or overrides the feasibility score (REQ-007), this node reads the complete aggregated_results from TenderState and produces a structured Go/No-Go brief — the primary deliverable of TenderIQ[cite: 8].

[cite_start]The report is not a free-form narrative[cite: 9]. [cite_start]It is a structured document with defined sections in a defined order, using the effective feasibility score (hitl_override_score if set, otherwise feasibility_score), the risk findings from REQ-004, and the financial summary from REQ-006[cite: 9].

[cite_start]The LLM's job is synthesis and clear writing — not analysis[cite: 10]. [cite_start]All analysis has already been done by REQ-004/005/006[cite: 10].

[cite_start]Critical rule from REQ-007: the node must read hitl_override_score first and fall back to feasibility_score only if hitl_override_score is None[cite: 11]. [cite_start]Never read feasibility_score directly without checking for an override first[cite: 12]. [cite_start]A hitl_override_score of 0.0 is valid and must not be treated as None[cite: 13].

---

### Preconditions
* [cite_start]**REQ-007 complete:** graph.aupdate_state() has injected hitl_approved=True and (optionally) hitl_override_score into the checkpoint before this node runs[cite: 15].
* [cite_start]**state["aggregated_results"]** is populated with: risk_findings, feasibility_score, feasibility_breakdown, financial_summary, source_languages[cite: 16].
* [cite_start]**state["hitl_approved"] == True** — the graph interrupt_before gate only releases this node after HITL approval[cite: 17].
* [cite_start]**CostTrackingHandler wired (REQ-003 Slice 3)** — this node makes real LLM calls and must log cost[cite: 18].

---

### Main Flow
1. [cite_start]The report_assembler node receives the full TenderState after HITL approval[cite: 20].
2. [cite_start]Determine the effective feasibility score: `effective_score = state["hitl_override_score"] if state["hitl_override_score"] is not None else state["feasibility_score"]`[cite: 21].
3. [cite_start]This check must use "is not None" — never falsy — to handle score=0.0 correctly[cite: 22].
4. [cite_start]Determine if analyst overrode the score: `is_overridden = state["hitl_override_score"] is not None`[cite: 23].
5. [cite_start]Build the report context dict from aggregated_results — all the structured data the LLM needs to synthesise the report[cite: 24].
6. [cite_start]Call the LLM with REPORT_SYNTHESIS_PROMPT and the report context, requesting a structured ReportOutput (see Data Requirements)[cite: 25].
7. [cite_start]The LLM call is wrapped with `CostTrackingHandler(node_name="report_assembler")`[cite: 26].
8. [cite_start]Write `state["final_report"]` as a JSON-serialisable dict matching ReportOutput schema and return[cite: 27].

---

### Alternative Flows

| Condition | System Response | Resulting State |
| :--- | :--- | :--- |
| LLM returns malformed structured output | Retry once. [cite_start]On second failure: produce a minimal fallback report with available data and a warning banner: `{"error": "Report synthesis incomplete", "go_no_go": "REVIEW", "executive_summary": "Automated synthesis failed. Review findings manually."}` [cite: 29] | final_report contains fallback. [cite_start]Run still completes — never fails on report assembly alone[cite: 29]. |
| LLM API failure | Retry with exponential backoff, 3 attempts. On exhausted retries: use fallback report (same as malformed output path). [cite_start]Do NOT raise — a report assembly failure after HITL approval must never revert the run to "failed"[cite: 29]. | Fallback report stored. [cite_start]Run completes[cite: 29]. |
| aggregated_results is missing a field (e.g. financial_summary has error key) | Include a "Data Unavailable" section in the report for the missing field. [cite_start]Never crash on missing data — degrade gracefully per section[cite: 29]. | Report generated with partial data. [cite_start]Missing sections clearly flagged[cite: 29]. |
| hitl_override_score is 0.0 (analyst set score to zero) | Use 0.0 as the effective score. Go/No-Go recommendation becomes "DECLINE". [cite_start]This is a valid analyst decision, not an error[cite: 29]. | [cite_start]Report reflects analyst's explicit 0.0 score[cite: 29]. |

---

### Postconditions
* [cite_start]`state["final_report"]` is always a dict — never None, never a string[cite: 31]. [cite_start]Even the fallback report is a structured dict[cite: 31].
* [cite_start]`analysis_runs.state = "complete"` and `analysis_runs.completed_at` is set after this node finishes (handled by `_resume_graph()` in REQ-007)[cite: 32].
* [cite_start]The `effective_score` in the report matches `hitl_override_score` if set, otherwise `feasibility_score` — never a recalculated value[cite: 33].
* [cite_start]At least one `llm_cost_events` row with `node_name="report_assembler"` exists after this node completes (unless fallback path triggered before any LLM call)[cite: 34].

---

### Data Requirements

#### Report Output Schema
```python
class GoNoGo(str, Enum):
    GO      = "GO"       # score >= 70
    REVIEW  = "REVIEW"   # score 40-69
    DECLINE = "DECLINE"  # score < 40

class RiskSummaryItem(BaseModel):
    category:    str
    severity:    str
    description: str  # plain-English, 1 sentence

class ReportSection(BaseModel):
    title:   str
    content: str  # markdown-formatted prose

class ReportOutput(BaseModel):
    [cite_start]go_no_go:               GoNoGo [cite: 37, 38]
    [cite_start]effective_score:        float   # the score used (override or AI) [cite: 38]
    [cite_start]is_analyst_override:    bool [cite: 38]
    [cite_start]executive_summary:      str     # 3-5 sentences max [cite: 38]
    [cite_start]recommendation:         str     # 1 clear sentence [cite: 38]
    [cite_start]risk_summary:           list[RiskSummaryItem]  # top 5 risks max [cite: 38]
    [cite_start]feasibility_highlights: list[str]  # 3-5 bullet points [cite: 38]
    [cite_start]financial_highlights:   list[str]  # 3-5 bullet points [cite: 38]
    analyst_note:           str | [cite_start]None  # shown if is_analyst_override [cite: 38, 39]

```

#### Go/No-Go Thresholds

| Score Range | Recommendation | Display |
| --- | --- | --- |
| 70 – 100 | GO | Green badge — "Recommended to Bid" 

 |
| 40 – 69 | REVIEW | Amber badge — "Review Carefully Before Bidding" 

 |
| 0 – 39 | DECLINE | Red badge — "Consider Declining This Tender" 

 |

#### analysis_runs update

* When the graph completes (handled by `_resume_graph()` in REQ-007), write `state["final_report"]` to `analysis_runs.agent_trace` under the "report_assembler" key.


* No new DB table is needed — the report is stored as JSONB in `agent_trace` and retrieved via `GET /tenders/{id}/report`.



---

### Report Synthesis Skill Package

* Defined in `app/agents/skills/report_synthesis.py` — same pattern as REQ-004/005/006. Pure constants and Pydantic schemas, zero LangChain/LangGraph imports.


* **System prompt requirements:**
* The LLM's role is SYNTHESIS, not analysis. All findings, scores, and commitments come from the structured data provided — the LLM must not add new analysis, inferences, or opinions not supported by the input data.


* Go/No-Go must be determined by the `effective_score` using the fixed thresholds above — the LLM must not override this with its own judgment.


* 
`executive_summary` must be 3-5 sentences maximum — never longer.


* 
`risk_summary` must include the top 5 risks by severity (critical first) — never more than 5.


* If `is_analyst_override` is True, `analyst_note` must acknowledge the score was adjusted: *"Feasibility score adjusted from {ai_score} to {override_score} by analyst review."* 


* Output language is always English regardless of tender source language.




* **Few-shot examples:**
* 
*Example 1 — GO report:* high-scoring tender, mostly low/medium risks, straightforward financial profile.


* 
*Example 2 — DECLINE report:* low-scoring tender, multiple critical risks, financial commitments exceeding company capacity.


* 
*Example 3 — REVIEW with analyst override:* AI scored 35 (DECLINE), analyst overrode to 65 (REVIEW), report shows both scores and analyst_note.





---

### Non-Functional Requirements

* 
**Performance:** Must complete within 45 seconds — this is the final node and runs after HITL, so latency here is less critical than REQ-004/005/006. The analyst has already waited; 45s for final report assembly is acceptable.


* 
**Correctness:** Go/No-Go must be determined by Python code using the effective_score and fixed thresholds — never by asking the LLM to decide. The LLM receives the Go/No-Go determination as input, not as a question. `effective_score` in the report must exactly match the score used for Go/No-Go determination — verified by a test that cross-checks both fields.


* 
**Resilience:** This node must NEVER cause the run to transition to "failed". A fallback report is always better than a failed run — the analyst has already committed their HITL decision and cannot re-approve.


* 
**Security:** `financial_summary` values must not appear in logs — consistent with REQ-006 security rules.



---

### Implementation Slices

Each slice is implemented and reviewed independently. The agent must not expand scope beyond the declared files for each slice.

| Slice | Owns | Scope |
| --- | --- | --- |
| **1. Skill Package** | `agents/skills/report_synthesis.py` | GoNoGo enum, RiskSummaryItem, ReportSection, ReportOutput Pydantic schemas. REPORT_SYNTHESIS_PROMPT with synthesis discipline rules. 3 few-shot examples (GO, DECLINE, REVIEW with override). Zero LangChain/LangGraph imports.

 |
| **2. Node Logic** | `agents/nodes/report_assembler.py` | Replace REQ-003 stub: effective_score determination (is not None check), Go/No-Go computed in Python, structured LLM call with ReportOutput schema, fallback report on failure, CostTrackingHandler wiring. Must never raise — always produce a report dict.

 |
| **3. API Endpoint** | `routers/tenders.py` (add `GET /tenders/{id}/report`), `schemas/analysis.py` (`ReportResponse`) | `GET /tenders/{id}/report` — reads final_report from `analysis_runs.agent_trace["report_assembler"]`, returns typed ReportResponse. Returns 404 if run not yet complete. Returns 200 with report if complete.

 |
| **4. Frontend** | `app/tenders/[id]/report/full/page.tsx` (create), `components/GoNoGoBadge.tsx`, `components/FullReportView.tsx`, `lib/api/report.ts` | Full report page with: large Go/No-Go badge at top (coloured by recommendation), effective score with override note if applicable, executive summary, risk summary table (top 5), feasibility highlights list, financial highlights list, PDF download button (triggers browser print). Replace "coming soon" placeholder.

 |
| **5. QA** | `tests/test_report_assembler.py` | Tests: effective_score uses hitl_override_score when set, effective_score=0.0 is valid (not None), Go/No-Go computed in Python not LLM, fallback report on malformed output, fallback report on API failure (run never fails), report stored in agent_trace, GET /report returns 404 before complete, GET /report returns report after complete, is_analyst_override=True when override set.

 |

**Slice Activation Rule:** The project owner selects which slice is executed and when — this decision is never delegated to the AI agent. Once a slice is selected, the agent may automatically activate whichever skill(s) best match that slice's declared scope (e.g. Slice 1 → senior-prompt-engineer; Slice 2 → agent-designer; Slice 4 → senior-fullstack; Slice 5 → senior-qa). The agent must not expand scope to cover other slices, and must not select the next slice on its own.

---

### Acceptance Criteria / Definition of Done

* [ ] `report_assembler_node` replaces the REQ-003 stub and the graph completes end-to-end (upload → agents → HITL → report) without any change to `graph.py`.


* [ ] `effective_score` uses `hitl_override_score` when set — verified by test with `hitl_override_score=85.0` and `feasibility_score=40.0`: effective_score must be 85.0.


* [ ] `effective_score=0.0` is handled correctly (not treated as None) — verified by test with `hitl_override_score=0.0`: Go/No-Go must be DECLINE, not based on feasibility_score.


* [ ] Go/No-Go determination is made in Python using fixed thresholds — not by the LLM. Verified by asserting `go_no_go` field matches expected value for known effective_score.


* [ ] A malformed LLM response produces a fallback report dict and the run transitions to "complete" (never "failed").


* [ ] An LLM API failure produces a fallback report and the run transitions to "complete" — the analyst's HITL decision is never invalidated by a report assembly failure.


* [ ] `GET /tenders/{id}/report` returns HTTP 404 before the run is complete and HTTP 200 with a `ReportResponse` after completion.


* [ ] Full report page renders Go/No-Go badge in correct colour, shows override note when `is_analyst_override=True`, and displays all 5 report sections.


* [ ] At least one `llm_cost_events` row with `node_name="report_assembler"` exists after a successful (non-fallback) run.



---

### Document Control

After REQ-008 is complete, TenderIQ has a full MVP demo flow: PDF upload → bilingual ingestion → parallel agent analysis → HITL review → final Go/No-Go report. REQ-009 (WebSocket) and REQ-012 (Eval Harness) enhance this flow but are not required for the first pilot demo.


```

```