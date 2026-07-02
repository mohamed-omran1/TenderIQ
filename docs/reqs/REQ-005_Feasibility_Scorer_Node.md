# REQ-005: Feasibility Scorer Node — Company Profile Matching



| Property | Value |
| --- | --- |
| **Status** | READY FOR IMPLEMENTATION

 |
| **Sprint** | Week 2 — Core Agents (parallel with REQ-004)

 |
| **Priority** | P0 — Feasibility score is the primary Go/No-Go signal in the final report.

 |
| **Dependencies** | REQ-002 complete (company profile exists + profile_lookup tool). REQ-003 complete (graph skeleton, scorer stub wired). Does not depend on REQ-004.

 |
| **Related Docs** | TenderIQ_PRD_v1.0 §5.2 (Feasibility Scorer responsibility), §3.2 (Success Metrics) | TenderIQ_Architecture_v1.0 §3

 |

## Owning Component

| Feasibility Scorer Node | Feasibility Scoring Skill Package | analysis_runs table (score columns) |
| --- | --- | --- |
| app/agents/nodes/feasibility_scorer.py

 | app/agents/skills/feasibility_scoring.py

 | app/db/models.py (AnalysisRun)

 |

---

## Description

Replace the feasibility_scorer stub from REQ-003 with real LLM-based scoring. The node evaluates a tender against the company's stored profile across five dimensions, producing a 0–100 composite score with a per-dimension breakdown. This score is the primary Go/No-Go signal displayed in the final report and is the value the analyst can override in the HITL gate (REQ-007). The score must be reproducible and explainable — the analyst must be able to understand why a score was given, not just see a number.

Unlike REQ-004 (Risk Radar) which retrieves risk-specific chunks, the Feasibility Scorer retrieves tender-scope chunks (project description, requirements, timelines, geographic scope) to compare against the company profile. These two retrieval strategies are intentionally different and must not be merged.

---

## Preconditions

* REQ-002 Slice 2 complete: profile_lookup tool available and returning CompanyProfileSchema.


* REQ-003 Slice 1 complete: graph compiled with feasibility_scorer wired between supervisor and aggregator.


* state["chunks"] non-empty (guaranteed by Supervisor).


* A valid company profile exists (Supervisor already validates this — if not, the run fails before reaching this node).



---

## Main Flow

1. The feasibility_scorer node receives TenderState containing chunks and company_id.


2. The node fetches the company profile via profile_lookup(company_id) — even though Supervisor already validated it exists, the Scorer fetches it fresh to get the full profile data for scoring.


3. The node retrieves scope-relevant chunks using tender-scope anchor queries (see Skill Package section) — project description, requirements, value, timeline, location, sector — different from REQ-004's risk anchor queries.


4. The LLM evaluates the tender against the company profile across 5 dimensions (see schema below), producing a score (0–20) and a one-sentence rationale for each dimension.


5. The composite score is computed as the sum of all 5 dimension scores (0–100). This calculation is done deterministically in Python after the LLM call — not by the LLM — to ensure it is always mathematically correct.


6. The node writes state["feasibility_score"] (float, 0.0–100.0) and state["feasibility_breakdown"] (dict with per-dimension scores and rationales) and returns.


7. The LLM call is wrapped with CostTrackingHandler(node_name="feasibility_scorer").



---

## Alternative Flows

| Condition | System Response | Resulting State |
| --- | --- | --- |
| LLM returns dimension score outside 0–20 range | Clamp the value to [0, 20] in Python — do not reject the response. Log a WARNING with the run_id and which dimension was clamped. | Scoring continues with clamped value. Composite score remains valid.

 |
| LLM returns malformed structured output | Retry once. On second failure: set feasibility_score=0.0, feasibility_breakdown={"error": "Scoring unavailable — malformed LLM response"}. Do not fail the run. | Run continues to aggregator with a zero score and error breakdown.

 |
| No scope-relevant chunks found by retrieval | Score on the full chunk set (first 20 chunks, ordered by chunk_index) rather than returning 0 — a tender with no obvious scope description is still scoreable on available content. | Scoring continues on reduced context.

 |
| LLM API call fails | Retry with exponential backoff, 3 attempts. On exhausted retries: raise exception — consistent with REQ-004 API failure handling. | Graph-level failure handling from REQ-003 applies.

 |
| Company profile is incomplete (some fields null) | Score only the dimensions for which profile data exists. Dimensions with missing profile data receive a score of 0 with rationale: "Insufficient profile data for this dimension." | Composite score reflects available data only — never blocked by incomplete profile.

 |

---

## Postconditions

* state["feasibility_score"] is always a float between 0.0 and 100.0 — never None, never outside this range.


* state["feasibility_breakdown"] is always a dict with exactly 5 dimension keys — never missing a dimension, even if that dimension scored 0.


* The composite score equals the arithmetic sum of the 5 dimension scores — verified by a Python assertion in the node code itself (assert abs(composite - sum(scores)) < 0.01).


* At least one llm_cost_events row with node_name="feasibility_scorer" exists after this node completes.



---

## Data Requirements

### Structured Output Schema

```python
class DimensionScore(BaseModel):
    score:     int = Field(ge=0, le=20)
    rationale: str  # one sentence, plain English

class FeasibilityOutput(BaseModel):
    technical_fit:      DimensionScore
    financial_capacity: DimensionScore
    timeline:           DimensionScore
    geographic_scope:   DimensionScore
    past_experience:    DimensionScore

# Composite computed in Python, not by LLM:
# composite = sum of all 5 dimension scores (0–100)
```[cite: 4]

### State fields written

| Field | Type | Example Value |
| --- | --- | --- |
| feasibility_score | float | 73.0[cite: 4] |
| feasibility_breakdown | dict | {"technical_fit": {"score": 18, "rationale": "..."}, "financial_capacity": {"score": 14, "rationale": "..."}, ...}[cite: 4] |

### analysis_runs columns updated
When the run reaches "awaiting_hitl" (in routers/tenders.py, consistent with REQ-004's persistence pattern), write the feasibility_score to analysis_runs.feasibility_score (column already exists from REQ-003 migration)[cite: 4]. No separate table is needed — the breakdown is stored in analysis_runs.aggregated_results JSONB[cite: 4].

---

## Feasibility Scoring Skill Package
Defined in `app/agents/skills/feasibility_scoring.py` — same separation principle as REQ-004's skill package[cite: 4]. Pure constants and Pydantic schemas, zero LangChain/LangGraph imports[cite: 4].

### 5 Scoring Dimensions

| Dimension | Weight (max score) | What the LLM evaluates |
| --- | --- | --- |
| technical_fit | 20 points | Does the tender's technical scope (civil, MEP, roads, etc.) match the company's declared specializations?[cite: 4] |
| financial_capacity | 20 points | Is the tender's estimated value within the company's max_project_value and available_bonding_capacity?[cite: 4] |
| timeline | 20 points | Is the project duration and start date feasible given the company's current commitments (as declared in past_projects)?[cite: 4] |
| geographic_scope | 20 points | Is the project location within the company's declared geographic_reach?[cite: 4] |
| past_experience | 20 points | Does the company's past_projects list include comparable projects in sector, scale, and complexity?[cite: 4] |

### Tender-scope anchor queries
* "project description and scope of work"[cite: 4]
* "contract value and estimated budget"[cite: 4]
* "project timeline completion date and duration"[cite: 4]
* "project location and geographic requirements"[cite: 4]
* "required certifications experience and qualifications"[cite: 4]

---

## Non-Functional Requirements

### Performance
* Must complete within 30 seconds — runs in parallel with REQ-004 and REQ-006, so latency is not additive for the overall pipeline[cite: 4].

### Determinism
* The composite score is computed in Python (sum of dimension scores), never by the LLM[cite: 4]. This ensures the math is always correct and auditable regardless of LLM output variability[cite: 4].
* Dimension scores are clamped to [0, 20] in Python before summing — the LLM can produce 21 or -1, the node must handle it silently[cite: 4].

### Explainability
* Every dimension score must be accompanied by a one-sentence rationale[cite: 4]. The rationale must reference specific profile data (e.g. "Company's max project value of EGP 20M is below the tender's estimated EGP 35M") — generic rationales like "company is not a good fit" are treated as a defect[cite: 4].

### Security
* Company profile data (financial_capacity, past_projects) must never appear in application logs — only metadata (run_id, node_name, composite score) may be logged[cite: 4].

---

## Implementation Slices
Each slice is implemented and reviewed independently. The agent must not expand scope beyond the declared files for each slice[cite: 4].

| Slice | Owns | Scope |
| --- | --- | --- |
| 1. Skill Package | agents/skills/feasibility_scoring.py | Define DimensionScore + FeasibilityOutput Pydantic schemas, SCORING_DIMENSIONS dict with rubric per dimension (0/5/10/15/20 score anchors with descriptors), tender-scope anchor queries list, and system prompt. Zero LangChain/LangGraph imports. Independently reviewable.[cite: 4] |
| 2. Node Logic | agents/nodes/feasibility_scorer.py | Replace REQ-003 stub: fetch profile via profile_lookup, retrieve scope-relevant chunks, structured LLM call with FeasibilityOutput schema, Python-side composite score computation (with assert), dimension clamping, CostTrackingHandler wiring, all Alternative Flow error handling.[cite: 4] |
| 3. Persistence | app/api/routers/tenders.py (one addition only) | When run reaches "awaiting_hitl", write state["feasibility_score"] to analysis_runs.feasibility_score column (already exists from REQ-003 migration). Atomic with the existing findings INSERT from REQ-004 Slice 3 — all in a single commit.[cite: 4] |
| 4. Frontend | components/FeasibilityScoreCard.tsx (create) | A score display component showing: the composite score as a large number with a colour-coded gauge (0-39 red, 40-69 amber, 70-100 green), a 5-row dimension breakdown table with score/20 and rationale per dimension, and a "Pending HITL approval" banner. Add this component to app/tenders/[id]/report/page.tsx below the RiskRadarTable. The "Approve" button remains disabled (REQ-007).[cite: 4] |
| 5. QA | tests/test_feasibility_scorer.py | Tests: composite score equals sum of dimensions, clamping out-of-range scores, malformed output degrades to 0.0, API failure raises, profile_lookup called with correct company_id, cost tracker fires, company profile data never appears in logs, GET /tenders/{id}/status returns feasibility_score after run completes.[cite: 4] |

### Slice Activation Rule
The project owner selects which slice is executed and when — this decision is never delegated to the AI agent[cite: 4]. Once a slice is selected, the agent may automatically activate whichever skill(s) best match that slice's declared scope (e.g. Slice 1 → senior-prompt-engineer; Slice 2 → agent-designer; Slice 5 → senior-qa)[cite: 4]. The agent must not expand scope to cover other slices, and must not select the next slice on its own[cite: 4].

---

## Acceptance Criteria / Definition of Done
* [ ] feasibility_scorer_node replaces the REQ-003 stub and the graph still compiles and runs end-to-end without any change to graph.py[cite: 4].
* [ ] Composite score always equals the arithmetic sum of the 5 dimension scores — verified by a Python assert inside the node and a dedicated test[cite: 4].
* [ ] All dimension scores are clamped to [0, 20] before summing — verified by a test that injects an out-of-range dimension score from the mock LLM[cite: 4].
* [ ] A malformed LLM response results in feasibility_score=0.0 and feasibility_breakdown={"error": "..."} — the graph continues without crashing[cite: 4].
* [ ] Every dimension rationale references specific profile data, not generic language — verified by inspecting real output on a sample tender[cite: 4].
* [ ] analysis_runs.feasibility_score is populated when the run reaches "awaiting_hitl" — verified by direct DB query[cite: 4].
* [ ] Company profile data (financial_capacity, past_projects) does not appear in application logs — verified by log capture in tests[cite: 4].
* [ ] FeasibilityScoreCard renders the correct colour for each score range: red (<40), amber (40-69), green (>=70)[cite: 4].
* [ ] At least one llm_cost_events row with node_name="feasibility_scorer" exists after a successful run[cite: 4].

---

## Document Control
The feasibility_breakdown schema is final — REQ-007 (HITL override) and REQ-008 (Report Assembler) both read from it directly[cite: 4]. Do not rename dimension keys without updating both downstream REQs[cite: 4].


```