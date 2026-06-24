# REQ-002: Company Profile Management

| | |
| :--- | :--- |
| **Status** | READY FOR IMPLEMENTATION |
| **Sprint** | Week 1 — Foundation (runs in parallel with REQ-001 Slice 1) |
| **Priority** | P0 — Blocking (Feasibility Scorer cannot run without a company profile) |
| **Related Docs** | TenderIQ_PRD_v1.0 §6.2, §7  |  TenderIQ_Architecture_v1.0 §2 |

---

## Owning Component

| FastAPI Router | Profile Lookup Tool | company_profiles table |
| :--- | :--- | :--- |
| app/api/routers/company.py | app/agents/tools/profile_lookup.py | app/db/models.py |

---

## Description
Enable an authenticated company to create, read, and update their benchmarking profile — the structured data the Feasibility Scorer node uses to evaluate whether a given tender is a good fit.

The profile captures the company's technical specialisations, financial capacity, geographic reach, past project experience, and maximum project value.

Without a completed profile, the Feasibility Scorer cannot produce a meaningful score, so this REQ is a hard dependency for all analysis runs.

* **Note:** Profile deletion is intentionally out of scope for MVP.
* A company has exactly one profile (1:1 with the companies table). Multiple profiles per company are deferred to v2.

---

## Preconditions
* The requesting company has a valid, active API key (Authorization: Bearer header).
* The company row already exists in the companies table (created at registration — out of scope for this REQ).
* **For GET:** a company_profiles row may or may not exist yet — both states must be handled gracefully.
* **For PUT:** the request body passes Pydantic schema validation (see Data Requirements).

---

## Main Flow

### GET /company-profile
1. Client sends GET /company-profile with a valid API key.
2. FastAPI resolves company_id from the API key.
3. Backend queries company_profiles for the matching company_id.
4. If a profile exists, return HTTP 200 with the full profile JSON.
5. If no profile exists yet, return HTTP 200 with a structured empty profile (all fields null / empty arrays) so the frontend can render a blank form — do not return 404.

### PUT /company-profile
1. Client sends PUT /company-profile with a valid API key and a JSON body matching the CompanyProfileSchema.
2. FastAPI resolves company_id from the API key and validates the request body via Pydantic.
3. Backend performs an upsert on company_profiles (INSERT ... ON CONFLICT (company_id) DO UPDATE).
4. Return HTTP 200 with the full updated profile.
5. Emit a cache-invalidation event (simple DB flag or Redis key deletion) so any in-progress analysis run that cached the profile is aware it may be stale.
6. (For MVP: analysis runs always fetch the profile fresh at run start — caching is a v2 concern.)

---

## Alternative Flows

| Condition | System Response | Resulting State |
| :--- | :--- | :--- |
| PUT body fails Pydantic validation | HTTP 422 with field-level error detail. | No DB write. Existing profile unchanged. |
| max_project_value is negative or zero | HTTP 422 — "max_project_value must be a positive number." | No DB write. |
| specializations array is empty on PUT | HTTP 422 — "At least one specialisation is required." | No DB write. |
| GET called before any profile is created | HTTP 200 with empty-profile structure (all nulls/empty arrays). | No DB write. Frontend renders blank form. |
| Concurrent PUT from two sessions | DB upsert is atomic — last writer wins. No 409 conflict at MVP. | Profile reflects the last successful PUT. |

---

## Postconditions
* After a successful PUT: company_profiles contains exactly one row for the company with updated_at refreshed, and all submitted fields persisted as provided.
* After a GET: the response shape is always consistent — never null at the top level — whether or not a profile exists, so the frontend can render without null-checks on the root object.
* The profile is immediately available to the Feasibility Scorer on the next analysis run triggered by this company.

---

## Data Requirements

### company_profiles schema

| Field | Type | Required | Validation Rules |
| :--- | :--- | :--- | :--- |
| **company_id** | UUID FK | Yes (system) | Set from API key context — never supplied by client. |
| **specializations** | JSONB (string[]) | Yes | Min 1 item. Allowed values from a controlled list (e.g. civil, MEP, fit-out, roads, water). Free-text allowed in v2. |
| **financial_capacity** | JSONB (object) | Yes | Must include: currency (ISO 4217), annual_turnover (float > 0), available_bonding_capacity (float >= 0). |
| **past_projects** | JSONB (object[]) | No | Each item: { name, value, year, sector }. Max 20 items at MVP. |
| **geographic_reach** | JSONB (string[]) | Yes | ISO 3166-1 alpha-2 country codes. Min 1. |
| **max_project_value** | float | Yes | Must be > 0. In same currency as financial_capacity.currency. |
| **updated_at** | timestamp | Yes (system) | Set server-side on every upsert — never supplied by client. |

---

## Profile Lookup Tool
* The Feasibility Scorer accesses the company profile via a LangChain tool (app/agents/tools/profile_lookup.py), not directly via the FastAPI router.
* The tool takes company_id as input and returns the profile as a structured Pydantic object.
* This decoupling means the scorer can be tested independently of the HTTP layer.

---

## Non-Functional Requirements

### Performance
* GET /company-profile must respond in under 200ms (profile is a single DB row with no joins at MVP).
* PUT /company-profile must respond in under 300ms including the DB upsert.

### Security
* A company may only read or update their own profile.
* The company_id is always derived from the authenticated API key — it is never accepted as a request parameter.
* The financial_capacity field contains sensitive data — it must never appear in application logs.

### Reliability
* The upsert operation must be atomic — a partial write must not be possible.
* Use a single SQL statement (INSERT ... ON CONFLICT DO UPDATE) rather than a check-then-insert pattern.

### Usability
* The empty-profile GET response must include all expected keys with null/empty-array values so the frontend can render a form without defensive null-checks on every field.

---

## Implementation Slices
Each slice is implemented and reviewed independently. The agent must not expand scope beyond the declared files for each slice.

| Slice | Owns | Scope |
| :--- | :--- | :--- |
| **1. Backend** | routers/company.py, db/models.py (company_profiles), alembic migration | Implement GET + PUT /company-profile with Pydantic schema validation, upsert logic, and all Alternative Flow error responses. The profile_lookup tool is NOT part of this slice. |
| **2. Agent Tool** | agents/tools/profile_lookup.py | Implement the LangChain tool that fetches a company profile by company_id and returns a typed Pydantic object. Must be independently testable without running the full LangGraph graph. |
| **3. Frontend** | app/profile/page.tsx, components/CompanyProfileForm.tsx | Form UI for all profile fields. Calls GET on mount to pre-populate. Calls PUT on submit. Shows field-level validation errors from 422 responses. Handles the empty-profile (first-time setup) state with a clear onboarding message. |
| **4. QA** | tests/test_company_profile.py | Test cases: GET with no existing profile, GET with profile, valid PUT (create), valid PUT (update), each validation failure from Alternative Flows, concurrent PUT idempotency, profile_lookup tool unit test. |

---

## Slice Activation Rule
* The project owner selects which slice is executed and when — this decision is never delegated to the AI agent.
* Once a slice is selected, the agent may automatically activate whichever skill(s) best match that slice's declared scope (e.g. Slice 1 → senior-fullstack + database-designer; Slice 2 → agent-designer).
* The agent must not expand scope to cover other slices, and must not select the next slice on its own.

---

## Acceptance Criteria / Definition of Done
* [ ] GET /company-profile returns HTTP 200 with a consistent JSON shape whether or not a profile exists — never 404, never null root.
* [ ] PUT /company-profile with a valid body creates the profile if it does not exist and updates it if it does (verified by calling GET immediately after).
* [ ] PUT with an empty specializations array returns HTTP 422 with a descriptive error message.
* [ ] PUT with a negative max_project_value returns HTTP 422 with a descriptive error message.
* [ ] A company cannot read or modify another company's profile (verified by a cross-tenant test using two distinct API keys).
* [ ] financial_capacity data does not appear in application logs (verified by log inspection in the QA test).
* [ ] profile_lookup tool returns a typed Pydantic object matching the stored profile when called with a valid company_id.
* [ ] profile_lookup tool raises a descriptive exception when called with a company_id that has no profile (not a silent None return).
* [ ] Frontend form pre-populates all fields from GET on mount and submits a valid PUT on save.

---

## Document Control
This REQ is the contract for implementation. Any deviation discovered during build should be added back into Alternative Flows before the slice is marked complete — not silently handled in code.
