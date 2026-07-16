Read the following documents before writing any code:
- docs/reqs/REQ-002_Company_Profile_Management.md
- docs/01_PRD.md (section 8 — Frontend pages)

You are implementing **REQ-002 — Slice 3 (Frontend) only**.

Slices 1 and 2 are already complete and working:
- GET /company-profile → returns 200 with full profile or empty-profile structure
- PUT /company-profile → upserts and returns updated profile
- Both endpoints require Authorization: Bearer <api_key> header

---

## Your scope (do not touch anything outside this list)
- frontend/app/profile/page.tsx (create this file)
- frontend/components/CompanyProfileForm.tsx (create this file)
- frontend/lib/api/company.ts (create this file — API client functions only)

---

## What to implement

### 1. frontend/lib/api/company.ts
Two async functions that call the backend:

getCompanyProfile(): Promise<CompanyProfileSchema>
- GET /company-profile
- Sends Authorization header from env variable NEXT_PUBLIC_API_KEY
- Returns the response JSON as-is (always 200, either full or empty)

updateCompanyProfile(data: CompanyProfileSchema): Promise<CompanyProfileSchema>
- PUT /company-profile
- Sends Authorization header
- On 422: throws a structured ValidationError with field-level detail
- On other errors: throws a generic ApiError with the status code

Define the TypeScript types to match the backend Pydantic schema exactly:
- CompanyProfileSchema with all fields (specializations, financial_capacity,
  geographic_reach, past_projects, max_project_value)
- FinancialCapacity nested type: { currency, annual_turnover, available_bonding_capacity }
- PastProject nested type: { name, value, year, sector }

### 2. frontend/components/CompanyProfileForm.tsx
A form component with these exact behaviours:

Loading state:
- On mount, call getCompanyProfile()
- Show a skeleton loader while fetching
- Pre-populate all fields with returned data
- If all fields are null/empty (first-time setup), show an onboarding
  banner: "Complete your company profile to enable tender analysis."

Fields to render:
- specializations: multi-select or tag input from a controlled list:
  ["civil", "MEP", "fit-out", "roads", "water"]
- financial_capacity.currency: text input (ISO 4217, e.g. USD, EGP, SAR)
- financial_capacity.annual_turnover: number input (positive only)
- financial_capacity.available_bonding_capacity: number input (>= 0)
- geographic_reach: multi-select of ISO country codes,
  at minimum: EG, SA, AE, QA, KW, BH, OM
- past_projects: dynamic list — user can add/remove rows,
  each row has: name (text), value (number), year (number), sector (text)
  max 20 rows, show counter "X / 20 projects"
- max_project_value: number input (positive only)

Submit behaviour:
- On save: call updateCompanyProfile() with form data
- Show inline loading state on the save button ("Saving...")
- On success: show a success toast "Profile saved successfully"
- On 422: show field-level error messages directly under each field
  (map backend field names to form fields)
- On other error: show a top-level error banner with the status code

### 3. frontend/app/profile/page.tsx
Simple page wrapper:
- Title: "Company Profile"
- Subtitle: "This profile is used by the Feasibility Scorer to evaluate
  tender fit against your company's capabilities."
- Renders <CompanyProfileForm />

---

## Rules
- Do NOT modify any backend files.
- Do NOT create any other pages or components beyond the 3 files listed.
- Do NOT add any new npm packages — use only what is already in
  package.json (Shadcn/ui, TanStack Query v5, Zod, Zustand are available).
- Use TanStack Query v5 for all data fetching (useQuery + useMutation)
  — do NOT use useEffect + fetch directly.
- Use Zod for client-side form validation — define a schema that mirrors
  the backend validation rules (min 1 specialization, positive
  max_project_value, etc.) so errors show before the API call.
- Use Shadcn/ui components for all UI elements — do not write raw HTML
  inputs or custom CSS unless absolutely necessary.
- The API base URL must come from NEXT_PUBLIC_API_URL env variable —
  never hardcode localhost.
- TypeScript strict mode — no `any` types anywhere.

---

## When you finish
Show me:
1. The full file tree of what you created (3 files only)
2. Confirm TanStack Query v5 syntax is used (useQuery with queryFn,
   not the v4 pattern)
3. Confirm no raw fetch() calls inside components — all API calls go
   through frontend/lib/api/company.ts
4. Confirm the onboarding banner shows on first-time setup
   (when GET returns all nulls)
5. Show me the Zod schema you defined and confirm it matches
   the backend validation rules

Do not move to Slice 4 until I explicitly tell you to.