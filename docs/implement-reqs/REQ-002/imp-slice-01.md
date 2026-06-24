Read the following documents before writing any code:
- docs/reqs/REQ-002_Company_Profile_Management.md
- docs/02_Architecture.md

You are implementing **REQ-002 — Slice 1 (Backend) only**.

---

## Your scope (do not touch anything outside this list)
- app/api/routers/company.py
- app/db/models.py (add CompanyProfile model only)
- app/schemas/company.py (Pydantic request/response schemas)
- alembic/versions/xxxx_create_company_profiles_table.py

---

## What to implement

1. Alembic migration — create the company_profiles table with these columns:
   - id: UUID primary key
   - company_id: UUID foreign key → companies.id (unique, not null)
   - specializations: JSONB not null
   - financial_capacity: JSONB not null
   - geographic_reach: JSONB not null
   - past_projects: JSONB default []
   - max_project_value: float not null
   - updated_at: timestamp with timezone, server default now(), updated on every upsert

2. SQLAlchemy model — CompanyProfile mapped to the table above.

3. Pydantic schemas:
   - CompanyProfileSchema (request + response): all fields with validation rules
   - specializations: List[str], min 1 item, values from controlled list
   - financial_capacity: nested object with currency (ISO 4217), annual_turnover (float > 0), available_bonding_capacity (float >= 0)
   - past_projects: List[object] max 20 items, each has name/value/year/sector
   - geographic_reach: List[str] ISO 3166-1 alpha-2, min 1
   - max_project_value: float > 0
   - EmptyProfileResponse: same shape as CompanyProfileSchema but all fields nullable — used when no profile exists yet

4. GET /company-profile
   - Resolve company_id from the authenticated API key (use the existing auth dependency)
   - Query company_profiles for that company_id
   - If found: return HTTP 200 + full profile
   - If not found: return HTTP 200 + EmptyProfileResponse (all nulls/empty arrays) — NOT 404

5. PUT /company-profile
   - Validate request body against CompanyProfileSchema
   - Upsert using a single SQL statement: INSERT ... ON CONFLICT (company_id) DO UPDATE
   - Set updated_at server-side — never accept it from the client
   - Return HTTP 200 + full updated profile

6. All Alternative Flow error responses from REQ-002:
   - Empty specializations → HTTP 422 "At least one specialisation is required."
   - Negative/zero max_project_value → HTTP 422 "max_project_value must be a positive number."
   - Body fails schema → HTTP 422 with field-level detail (FastAPI handles this automatically via Pydantic)

---

## Rules
- Do NOT implement the profile_lookup agent tool — that is Slice 2.
- Do NOT create any frontend files.
- Do NOT write test files — that is Slice 4.
- financial_capacity must never appear in application logs — add a __repr__ override or log-safe wrapper.
- company_id is always derived from the API key context — never accept it as a request parameter.
- Use async SQLAlchemy (AsyncSession) consistent with the existing db/session.py pattern.
- Use the same dependency injection pattern already established in app/api/deps.py.

---

## When you finish
Show me a summary of every file you created or modified, and confirm:
1. The upsert is a single atomic SQL statement (not check-then-insert)
2. GET returns HTTP 200 (not 404) when no profile exists
3. financial_capacity does not appear in any log statement

Do not move to Slice 2 until I explicitly tell you to.