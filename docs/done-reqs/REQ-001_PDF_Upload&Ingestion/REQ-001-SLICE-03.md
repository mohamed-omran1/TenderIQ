## Slice 3 (Frontend) — done ✅

I implemented REQ-001 Slice 3, scoped strictly to its declared file ownership (`app/upload/page.tsx`, `components/TenderUpload.tsx`) plus the supporting scaffold the senior-fullstack skill prescribes (`lib/` types + API client). Typecheck, production build, and a dev-server smoke test all pass.

### What was built

| File | Role |
| :--- | :--- |
| `lib/types.ts` | TS types mirroring the backend Pydantic schemas + a discriminated `UploadError` union (one variant per Alternative Flow) |
| `lib/api.ts` | Typed fetch client: `uploadTender()` → 202, `getTender()` for polling, maps every HTTP failure → `UploadError` |
| `lib/useTenderPolling.ts` | Polling hook: `uploading → processing → ready \| failed`, with a hard timeout so a stuck status can't hang forever |
| `components/TenderUpload.tsx` | Drag-and-drop + click-to-select, API-key field, and **distinct UI for every state**: idle / uploading / processing / ready / + one error card per failure type |
| `app/upload/page.tsx` + `app/page.tsx` (redirect) + `app/layout.tsx` + `app/globals.css` | App Router shell |
| `package.json`, `next.config.ts`, `tsconfig.json`, `postcss.config.mjs` | Next 16.2.9 · React 19 · Tailwind v4 (CSS-first, no config file) · strict TS |

### REQ-001 coverage in this slice

- **Main Flow steps 1, 3, 5, 10** — multipart upload to `POST /tenders/upload`, client-side pre-validation, and polling `GET /tenders/{id}` for the terminal status.
- **All 6 Alternative Flows → distinct UI states** (acceptance criterion #8): wrong MIME → "Unsupported file type", >50MB → "File too large", rate-limit → "Too many uploads (retry in Ns)" reading the `Retry-After` header, quota → "Monthly quota reached", corrupt/scanned (ingestion `failed`) → "Couldn't process the PDF", plus network and auth errors.
- **Contract integrity** — `UploadError` and the response types are byte-for-byte aligned with `app/errors.py` and `app/schemas/tender.py`. The API key is sent only as a `Bearer` header, never in the body/query.
- **Cross-layer seam honored** — the client reads the `Retry-After` header on 429 and disambiguates transient rate-limit from monthly quota via the backend's `detail` string.

### Verification
- `tsc --noEmit` → clean
- `next build` → ✓ Compiled successfully, 3 routes prerendered
- `next dev` smoke test → `/` → 307→`/upload`, `/upload` → HTTP 200

### Notes / out-of-scope (not silently done)
- **Auth is a single API-key field** for this MVP upload screen, as Slice 3's scope implies — a real session layer is a later slice.
- **WebSocket streaming** (Architecture §2 step 5 / WS event path) is **not** implemented — polling covers REQ-001's stated "polling **or** WebSocket" requirement, and WS is explicitly a Week-3 item per the README.
- The build emitted two moderate npm audit advisories (transitive deps); I did not run `audit fix --force` since that can introduce breaking changes mid-slice.

The README already lists Slice 3 as "not here yet" — I haven't touched it or staged a commit, per the rule that slice selection and commits are the owner's call. Want me to update the README's "What's not here yet" section and/or commit this?