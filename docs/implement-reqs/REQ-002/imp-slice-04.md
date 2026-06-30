Read the following documents before writing any code:
- docs/reqs/REQ-002_Company_Profile_Management.md

You are implementing **REQ-002 — Slice 4 (QA) only**.

Slices 1, 2, and 3 are already complete and working:
- GET /company-profile → 200 with full profile or empty-profile structure
- PUT /company-profile → upserts and returns updated profile
- app/agents/tools/profile_lookup.py → LangChain tool, raises ValueError if no profile
- Frontend form at /profile → pre-populates, validates, submits

---

## Your scope (do not touch anything outside this list)
- tests/test_company_profile.py (create this file)
- tests/conftest.py (add fixtures if not already there — do not remove existing ones)

---

## What to implement

A pytest test suite using httpx.AsyncClient and pytest-asyncio.
Every test case maps directly to a specific item in the
Acceptance Criteria section of REQ-002.

### Fixtures (in conftest.py if not already present)
- async_client: AsyncClient pointed at the FastAPI app
- company_api_key: a valid test API key resolving to a test company
- second_company_api_key: a DIFFERENT company's API key (for cross-tenant tests)
- clean_profile: fixture that deletes any existing profile for the
  test company before and after each test (ensures test isolation)

### Test cases — implement ALL of the following

# --- GET tests ---

test_get_profile_returns_200_when_no_profile_exists:
- Call GET /company-profile with company_api_key
- Assert HTTP 200 (NOT 404)
- Assert response JSON has all expected keys
  (specializations, financial_capacity, geographic_reach,
  past_projects, max_project_value)
- Assert all values are null or empty arrays
- Assert the root object itself is not null

test_get_profile_returns_200_with_data_when_profile_exists:
- First PUT a valid profile
- Then GET
- Assert HTTP 200
- Assert all returned fields match what was PUT

# --- PUT tests ---

test_put_profile_creates_profile_on_first_call:
- Clean state (no profile)
- PUT a valid full profile
- Assert HTTP 200
- GET immediately after
- Assert GET returns the same data

test_put_profile_updates_existing_profile:
- PUT a valid profile (creates it)
- PUT again with different max_project_value
- Assert HTTP 200
- GET and assert max_project_value reflects the second PUT
- Assert updated_at is more recent than after the first PUT

test_put_profile_is_atomic_upsert:
- Verify no partial state is possible by checking the DB directly
  after a successful PUT — all fields must be present together,
  never a mix of old and new values from two concurrent writes
- (Simulate with two sequential PUTs and verify final state is
  exactly the last PUT, no field bleed from the first)

# --- Validation tests ---

test_put_empty_specializations_returns_422:
- PUT with specializations: []
- Assert HTTP 422
- Assert error message contains "specialisation"

test_put_negative_max_project_value_returns_422:
- PUT with max_project_value: -1
- Assert HTTP 422
- Assert error message contains "max_project_value"

test_put_zero_max_project_value_returns_422:
- PUT with max_project_value: 0
- Assert HTTP 422

test_put_missing_required_field_returns_422:
- PUT with financial_capacity omitted entirely
- Assert HTTP 422

test_put_invalid_currency_format_returns_422:
- PUT with financial_capacity.currency: "INVALID"
  (not a valid ISO 4217 code)
- Assert HTTP 422

# --- Security tests ---

test_company_cannot_read_another_companys_profile:
- PUT a profile using company_api_key
- GET using second_company_api_key
- Assert the second company receives an empty profile
  (not the first company's data)

test_company_cannot_overwrite_another_companys_profile:
- PUT a profile using company_api_key
- PUT a different profile using second_company_api_key
- GET using company_api_key
- Assert the first company's profile is unchanged

test_financial_capacity_not_in_logs:
- Capture log output during a PUT call
- Assert the string "annual_turnover" does not appear in any log line
- Assert the string "available_bonding_capacity" does not appear
  in any log line

# --- Agent tool tests ---

test_profile_lookup_tool_returns_pydantic_object:
- Insert a profile directly in the DB (bypass HTTP)
- Call await profile_lookup.ainvoke({"company_id": str(company_id)})
- Assert the return type is CompanyProfileSchema
- Assert all fields match what was inserted

test_profile_lookup_tool_raises_value_error_when_no_profile:
- Ensure no profile exists for a random UUID
- Call await profile_lookup.ainvoke({"company_id": str(random_uuid)})
- Assert ValueError is raised
- Assert the error message contains "No company profile found"

---

## Rules
- Do NOT modify any router, model, schema, or frontend files.
- Do NOT use unittest — use pytest only.
- Do NOT use mocks for the database — use a real test database
  (separate from dev, configured via TEST_DATABASE_URL env variable).
- Every test must be fully isolated — no test should depend on
  the state left by another test.
- Use pytest.mark.asyncio for all async tests.
- Valid profile payload for reuse across tests — define once as a
  pytest fixture or module-level constant, not repeated in each test:

  VALID_PROFILE = {
    "specializations": ["civil", "roads"],
    "financial_capacity": {
      "currency": "EGP",
      "annual_turnover": 50000000.0,
      "available_bonding_capacity": 10000000.0
    },
    "geographic_reach": ["EG", "SA"],
    "past_projects": [
      {"name": "Cairo Ring Road", "value": 5000000, "year": 2022, "sector": "roads"}
    ],
    "max_project_value": 20000000.0
  }

---

## When you finish
Show me:
1. Total number of test functions created
2. Run the full suite and show me the output:
   pytest tests/test_company_profile.py -v
3. Confirm every Acceptance Criteria item from REQ-002 is covered
   by at least one test — map them explicitly:
   "AC1 → test_get_profile_returns_200_when_no_profile_exists ✓"
4. Confirm no test depends on another test's state
   (each test passes when run in isolation with pytest -k)

Do not start REQ-003 until I explicitly tell you to.