---
name: version-resolver
description: |
  Resolves the latest stable versions of libraries and frameworks BEFORE writing any code, generating any skill, or running any install commands. Use this skill whenever the task involves: installing npm/pip/yarn packages, scaffolding a project, writing import statements, creating a skill that references a specific tech stack, generating package.json or requirements.txt, or any situation where a library version needs to be known. Also triggers when the user says "use latest", "make sure versions are correct", "don't use old syntax", or when building with libraries that change APIs frequently (Next.js, React, LangChain, LangGraph, FastAPI, Tailwind, shadcn/ui, etc.). This skill must run before code generation, not after. Never skip this skill when library versions are relevant — training data is always outdated.
---

# Version Resolver

Your training data is frozen in the past. Every library version you "know" may be one or more major versions behind. This skill forces you to resolve current versions **before** writing a single line of code, generating a skill file, or producing an install command.

---

## When This Skill Applies

Trigger this skill at the **start** of any task that involves:

- Writing `npm install`, `pip install`, `yarn add`, or any package manager command
- Generating `package.json`, `requirements.txt`, `pyproject.toml`
- Scaffolding a new project or boilerplate
- Creating a skill file that references a tech stack
- Writing import statements for third-party libraries
- Any prompt containing words like: "latest", "current", "up to date", "don't use old syntax"

---

## Resolution Protocol

### Step 1 — Identify All Libraries

Before touching any code, list every library the task requires. Do not guess versions yet.

Example for a Next.js + LangGraph fullstack task:
```
Libraries to resolve:
- next
- react
- react-dom
- typescript
- tailwindcss
- @shadcn/ui
- langchain
- langgraph
- langchain-anthropic
- fastapi
- pydantic
- uvicorn
```

### Step 2 — Fetch via Context7 (Required)

Use the Context7 MCP tool to fetch the latest docs for each library. Do NOT skip this step and do NOT fall back to memory.

For each library, call Context7 with the library name. Extract:
- **Latest stable version number**
- **Correct import syntax** (this changes between major versions)
- **Any breaking changes** from the previous major version
- **Deprecated APIs** to avoid

```
# Example Context7 queries:
resolve_library_id("nextjs")         → fetch docs → extract version + app router syntax
resolve_library_id("langchain")      → fetch docs → extract version + LCEL syntax
resolve_library_id("langgraph")      → fetch docs → extract version + StateGraph API
resolve_library_id("tailwindcss")    → fetch docs → extract version (v3 vs v4 syntax differs heavily)
resolve_library_id("shadcn-ui")      → fetch docs → extract version + install method
```

### Step 3 — Build a Version Manifest

After resolving, output a version manifest **before any code block**. This is non-negotiable.

Format:
```
## ✅ Resolved Versions (via Context7, [date])
| Library         | Version  | Key Notes                              |
|-----------------|----------|----------------------------------------|
| next            | 15.x.x   | App Router default, no pages/ dir      |
| react           | 19.x.x   | use() hook, no forwardRef needed       |
| tailwindcss     | 4.x.x    | CSS-first config, no tailwind.config.js|
| langchain       | 0.3.x    | LCEL pipe syntax, no deprecated chains |
| langgraph       | 0.2.x    | StateGraph, Command returns            |
| fastapi         | 0.115.x  | Annotated deps, lifespan events        |
```

Only after this manifest is written should you proceed to generate code.

### Step 4 — Write Code From Manifest, Not Memory

Every import, every config option, every API call must reference the manifest above — not your training data. If you're uncertain about an API that wasn't covered in the Context7 fetch, say so explicitly and fetch again before writing.

---

## Common Traps to Avoid

These are high-risk areas where training data frequently produces wrong syntax:

### Next.js
- ❌ Old: `import { NextApiRequest } from 'next'` with `pages/api/`
- ✅ New: Route Handlers in `app/api/route.ts`, use `NextRequest`
- ❌ Old: `getServerSideProps`, `getStaticProps`
- ✅ New: `async` Server Components, `fetch()` with cache options

### Tailwind CSS v4
- ❌ Old: `tailwind.config.js` with `theme.extend`
- ✅ New: CSS-first config via `@theme` in your CSS file
- ❌ Old: `@layer utilities` with custom classes
- ✅ New: `@utility` directive

### LangChain (Python)
- ❌ Old: `from langchain.chat_models import ChatAnthropic`
- ✅ New: `from langchain_anthropic import ChatAnthropic`
- ❌ Old: `LLMChain`, `ConversationalRetrievalChain`
- ✅ New: LCEL — `chain = prompt | llm | parser`

### LangGraph
- ❌ Old: `MessageGraph`
- ✅ New: `StateGraph` with typed `State` (TypedDict)
- Check: `Command` object for node routing (introduced ~0.2)

### shadcn/ui
- ❌ Old: `npx shadcn-ui@latest add button`
- ✅ New: `npx shadcn@latest add button` (package renamed)

### FastAPI
- ❌ Old: `@app.on_event("startup")`
- ✅ New: `lifespan` context manager via `asynccontextmanager`

---

## If Context7 Is Unavailable

If the Context7 MCP is not connected or returns no results:

1. **State this explicitly** — do not silently fall back to memory
2. Ask the user: "Context7 is unavailable. Do you want me to proceed with my best-known versions, or should we connect Context7 first?"
3. If the user says proceed: prefix all version assumptions with `⚠️ UNVERIFIED —` and recommend the user runs `npm outdated` or checks PyPI after generation

---

## Output Checklist

Before handing off to the next task (code generation, skill writing, etc.), confirm:

- [ ] All required libraries identified upfront
- [ ] Context7 queried for each library
- [ ] Version manifest table written
- [ ] No imports written from memory without manifest backing
- [ ] Breaking changes and deprecated APIs noted where relevant

Do not proceed past this checklist until all items are checked.
