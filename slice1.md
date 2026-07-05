 Todos
[✓] Read source docs (imp-slice-01, REQ-005, REQ-004) and existing skill package pattern
[✓] Create app/agents/skills/feasibility_scoring.py with schemas, SCORING_DIMENSIONS, anchor queries, system prompt, and 3 few-shot examples
[✓] Verify zero LangChain/LangGraph imports via grep
[✓] Confirm system prompt explicitly forbids generic rationales and show anchors 0/20 for all 5 dims
Slice 1 complete. File: backend/app/agents/skills/feasibility_scoring.py (591 lines, compiles clean, mirrors risk_clause_extraction.py structure).
1. Full contents of the file
→Read backend\app\agents\skills\feasibility_scoring.py 
2. Score anchors at 0 and 20 for each of the 5 dimensions
technical_fit
- 0: Tender requires a sector or discipline that is NOT present in the company's specialisations list. Example: profile specialisations = [civil, roads] and tender requires MEP fit-out works; or profile has no specialisations declared.
- 20: Tender scope aligns exactly with all declared specialisations, with no gaps. Example: profile [civil, roads, water] and tender is a road + drainage project where every sub-scope falls inside a declared specialisation.
financial_capacity
- 0: Tender value exceeds company max_project_value by more than 50% (e.g. tender EGP 75M vs max EGP 50M), OR available_bonding_capacity is less than the required performance bond (e.g. bond requirement EGP 5M vs bonding capacity EGP 2M), OR max_project_value / available_bonding_capacity is missing/null.
- 20: Tender value is less than 80% of max_project_value (e.g. tender EGP 30M vs max EGP 50M) AND available_bonding_capacity exceeds 15% of tender value (e.g. bonding capacity EGP 10M vs tender EGP 30M = 33%).
timeline
- 0: past_projects is empty or contains no projects with a derivable duration, OR tender duration is less than 50% of the average duration of past_projects of comparable sector (tender demands a delivery speed materially faster than anything the company has previously demonstrated).
- 20: Tender duration is 110%–150% of the average duration of comparable past_projects (tender gives the company a comfortable time buffer relative to its track record).
geographic_scope
- 0: Tender country is NOT in company geographic_reach, OR geographic_reach is empty, OR geographic_reach is missing from the profile.
- 20: Tender country IS in geographic_reach AND the tender location matches the operating footprint of the company's past_projects (e.g. profile has EG and SA, and past projects include work in the exact tender city/region).
past_experience
- 0: past_projects is empty, OR contains zero projects in the tender's sector (no sector match at all).
- 20: past_projects contains 3 or more projects in the tender's sector, with at least one project value at 75% or more of the tender value (e.g. three or more civil projects, one of which is EGP 25M or more vs tender EGP 30M).
Every anchor cites numeric thresholds, named profile fields, or named tender values — no vague adjectives.
3. Zero LangChain/LangGraph imports
$ grep -n "langchain\|langgraph" backend/app/agents/skills/feasibility_scoring.py
(no matches, exit 1)
The only third-party import is from pydantic import BaseModel, Field. No async functions either.
4. The exact sentences in the prompt that forbid generic rationales
From FEASIBILITY_SYSTEM_PROMPT, section 3, lines 333–352:
3. WRITE A RATIONALE THAT REFERENCES SPECIFIC DATA. NEVER WRITE A GENERIC RATIONALE.
   - This is the single most important rule. A rationale that does not cite specific profile data AND specific tender data is a defect and will be rejected downstream.
   - FORBIDDEN RATIONALE PHRASES (non-exhaustive): "good fit", "strong fit", "reasonable fit", "acceptable fit", "poor fit", "limited fit", "matches well", "has experience", "has capacity", "is capable", "has the resources", "is well-positioned", "is a suitable match", "has done similar work". Any rationale that could be copy-pasted across different profiles and different tenders is too generic.
   - REQUIRED: every rationale must cite at least one concrete profile value (e.g. "Company max_project_value of EGP 20M", "specialisations of civil and roads", "past_projects list of 3 civil projects with values EGP 8M, EGP 12M, EGP 15M", "available_bonding_capacity of EGP 3M", "geographic_reach of EG, SA") AND at least one concrete tender value (e.g. "tender value of EGP 35M", "project location in SA", "project duration of 24 months", "required sector: dam construction", "required performance bond of 10% of contract value").
The same prohibition is also encoded in the DimensionScore.rationale Field(description=...) (lines 39–56), so a future prompt change cannot quietly relax it — both schema and prompt must be edited in lockstep.
Few-shot composites verified: Example 1 = 85 (strong), Example 2 = 25 (poor), Example 3 = 55 (mixed) — exactly the spec's stated targets. Standing by for Slice 2 when you give the go.