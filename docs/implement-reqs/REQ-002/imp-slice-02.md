Read the following documents before writing any code:
- docs/reqs/REQ-002_Company_Profile_Management.md 
- docs/02_Architecture.md 

You are implementing **REQ-002 — Slice 2 (Agent Tool) only**.

Slice 1 (Backend) is already complete. The following are available and working:
- app/db/models.py → CompanyProfile SQLAlchemy model
- app/schemas/company.py → CompanyProfileSchema Pydantic model
- GET + PUT /company-profile endpoints

---

## Your scope (do not touch anything outside this list)
- app/agents/tools/profile_lookup.py (create this file)
- app/agents/tools/__init__.py (add the new tool to exports if it exists)

---

## What to implement

A LangChain tool called `profile_lookup` that:

1. Takes `company_id: str` (UUID) as its only input argument.

2. Queries the database directly via AsyncSession — does NOT call the
   HTTP endpoint. Import and reuse the existing CompanyProfile model
   from app/db/models.py and the AsyncSession from app/db/session.py.

3. Returns a CompanyProfileSchema Pydantic object (imported from
   app/schemas/company.py) populated with the stored profile data.

4. If no profile exists for the given company_id:
   - Raises a descriptive exception:
     ValueError(f"No company profile found for company_id={company_id}. 
     The profile must be created before running an analysis.")
   - Does NOT return None silently.

5. Is decorated with @tool from langchain_core.tools so it can be
   registered directly on a LangGraph node.

6. Has a clear docstring explaining what it does — this docstring is
   what the LLM reads to decide when to call this tool, so make it
   specific: "Retrieves the company benchmarking profile used by the
   Feasibility Scorer to evaluate tender fit."

---

## Rules
- Do NOT modify any router, migration, or model files — Slice 1 owns those.
- Do NOT create any frontend files.
- Do NOT write the Feasibility Scorer node — that is a later REQ.
- Do NOT create test files — that is Slice 4.
- The tool must be independently callable without running the full
  LangGraph graph (no graph imports inside this file).
- Use async def — the tool must be async-compatible with LangGraph's
  async execution model.
- Do not log or print the full profile object — financial_capacity
  is sensitive and must not appear in logs.

---

## When you finish
Show me:
1. The full contents of app/agents/tools/profile_lookup.py
2. Confirm the tool raises ValueError (not returns None) when no profile exists
3. Confirm financial_capacity does not appear in any log or print statement
4. Show me a quick manual test I can run in a Python shell to verify
   the tool works independently — without starting the full app

Do not move to Slice 3 until I explicitly tell you to.