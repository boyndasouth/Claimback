# Day 1 — Scaffold Plan

> **Scope:** SPEC §14 repo layout, §6 Pydantic + SQLAlchemy models, §6.1 first Alembic
> migration, §9 synthetic data generator, FastAPI `/health`, docker-compose
> (Postgres + pgvector), §15 `.env.example`, and a Render deploy config.
>
> **Done when:** `make data` produces 250 cases; backend `/health` returns 200
> (locally via docker-compose and on Render).

---

## File inventory & build order

Build order is strict — later items depend on earlier ones.

| Order | File | What it is |
|---|---|---|
| 1 | `Makefile` | `make data`, `make dev`, `make migrate` targets |
| 2 | `.env.example` | All §15 vars with placeholder values |
| 3 | `docker-compose.yml` | Postgres 16 + pgvector, backend service |
| 4 | `render.yaml` | Render web service + managed Postgres |
| 5 | `backend/pyproject.toml` | Python 3.11 deps: FastAPI, SQLAlchemy 2, Alembic, pgvector, Pydantic v2, Faker, python-dotenv, ruff |
| 6 | `backend/app/__init__.py` | Empty package marker |
| 7 | `backend/app/models/__init__.py` | Re-exports all Pydantic models |
| 8 | `backend/app/models/base.py` | `SourceRef`, `FlaggedSpan`, `CaseContext` shared types |
| 9 | `backend/app/models/claim.py` | `DenialCategory`, `ExtractedClaim`, `PolicyClause` |
| 10 | `backend/app/models/appeal.py` | `AppealPlan`, `AppealDraft`, `GroundingReport`, `SuccessEstimate` |
| 11 | `backend/app/models/case.py` | `CaseStatus`, `AgentResult` (Generic), `Agent` (Protocol) |
| 12 | `backend/db/__init__.py` | SQLAlchemy engine + `get_session` async context |
| 13 | `backend/db/models.py` | All 7 ORM table classes (§6.1) |
| 14 | `backend/db/migrations/env.py` | Alembic env wired to `DATABASE_URL` and ORM metadata |
| 15 | `backend/db/migrations/versions/0001_initial.py` | First Alembic revision: CREATE TABLE for all 7 tables + pgvector extension |
| 16 | `backend/config/models.yaml` | Model tier config (§5.1) |
| 17 | `backend/app/main.py` | FastAPI app: CORS, `/health`, mounts `app/api` and `app/tools` routers (stubs) |
| 18 | `backend/app/api/__init__.py` | Empty stub router |
| 19 | `backend/app/tools/__init__.py` | Empty stub router |
| 20 | `data/generate.py` | Synthetic data generator with `--seed` flag |
| 21 | `data/templates/denial_letter.html` | Jinja2 HTML template for denial letters |
| 22 | `data/templates/eob.html` | Jinja2 HTML template for EOBs |
| 23 | `data/fixtures/cpt_icd10.json` | Static ~300-code CPT/ICD-10 subset with valid-pair table |
| 24 | `data/fixtures/state_rules.json` | 10-state appeal rules (deadlines, external review) |
| 25 | `data/fixtures/payers.json` | 4 payer × 2 plan-type definitions |
| 26 | `frontend/` | Minimal Next.js placeholder (`create-next-app` output — no logic yet) |
| 27 | `orchestrate/` | Empty directory structure as per §14 (`.gitkeep` files) |
| 28 | `prompts/` | Empty per-agent prompt directories (`.gitkeep`) |
| 29 | `evals/` | Empty directory (`.gitkeep`) |
| 30 | `replay/` | Empty directory (`.gitkeep`) |
| 31 | `.github/workflows/ci.yml` | Minimal CI: ruff lint + mypy type-check only (tests added Day 2+) |
| 32 | `README.md` | Day-1 stub: project summary, local setup, make targets |

---

## Sub-tasks

---

### ST-1 — Root config & tooling files

**Intent:** Create all files that every other sub-task depends on: environment variables
template, Makefile convenience targets, docker-compose, and Render deploy config.
Nothing else can be tested without these in place.

**Expected outcomes:**
- `docker-compose up` starts Postgres 16 with pgvector enabled on port 5432.
- `.env.example` documents every §15 variable with safe placeholder values.
- `make dev` and `make data` are defined (may fail until later sub-tasks exist).
- `render.yaml` declares a web service pointing to `backend/`.

**Todo list:**
1. Create `.env.example` with all variables from §15 table, grouped by service, with `# comment` annotations. Local defaults: `DATABASE_URL=postgresql+asyncpg://claimback:claimback@localhost:5432/claimback`.
2. Create `docker-compose.yml`: service `db` using `ankane/pgvector:v0.7.0` (Postgres 16 + pgvector), port 5432, volume `pgdata`, env vars from file. Add a `backend` service stub (build from `backend/`, depends_on `db`) disabled with a `profiles: [app]` so plain `docker-compose up` only starts the DB.
3. Create `Makefile` with targets: `data` (runs `python data/generate.py`), `dev` (docker-compose up + uvicorn), `migrate` (alembic upgrade head), `lint` (ruff + mypy), `test` (pytest).
4. Create `render.yaml` with one web service: `runtime: python`, `buildCommand: pip install -e backend`, `startCommand: uvicorn backend.app.main:app --host 0.0.0.0 --port $PORT`, env group referencing all §15 backend vars.

**Relevant context:** SPEC §14 (repo root), §15 (env vars), §4 (Render deployment).

**Status:** [x] done

---

### ST-2 — Python package & Pydantic models

**Intent:** Create the `backend/` Python package and all Pydantic v2 models from §6
exactly as specified. These are the data contracts used by every agent and the DB layer.

**Expected outcomes:**
- `python -c "from backend.app.models import *"` runs without error.
- All types from §6 are importable: `DenialCategory`, `ExtractedClaim`, `PolicyClause`, `AppealPlan`, `AppealDraft`, `GroundingReport`, `SuccessEstimate`, `CaseStatus`, `AgentResult`, `Agent`.
- Supporting types `SourceRef`, `FlaggedSpan`, `CaseContext` are defined in `base.py`.

**Todo list:**
1. Create `backend/pyproject.toml` with `[project]` metadata (name `claimback-backend`, python `>=3.11`) and `[project.dependencies]`: `fastapi>=0.111`, `uvicorn[standard]`, `sqlalchemy[asyncio]>=2.0`, `alembic`, `asyncpg`, `pgvector`, `pydantic>=2.0`, `faker`, `python-dotenv`, `ruff`, `mypy`. Add `[tool.ruff]` and `[tool.mypy]` sections.
2. Create `backend/__init__.py` (empty).
3. Create `backend/app/__init__.py` (empty).
4. Create `backend/app/models/__init__.py` that re-exports all public symbols.
5. Create `backend/app/models/base.py` with `SourceRef` (clause_id, doc_id, section, text), `FlaggedSpan` (span text + reason), `CaseContext` (case_id, user_id, trace_id).
6. Create `backend/app/models/claim.py` with `DenialCategory` enum and `ExtractedClaim`, `PolicyClause` exactly matching §6.
7. Create `backend/app/models/appeal.py` with `AppealPlan`, `AppealDraft`, `GroundingReport`, `SuccessEstimate` exactly matching §6.
8. Create `backend/app/models/case.py` with `CaseStatus` (StrEnum + auto()), `AgentResult[T]` (Generic BaseModel), `Agent` Protocol.

**Relevant context:** SPEC §6 (complete Python code blocks). No fields may be added or removed.

**Status:** [x] done

---

### ST-3 — SQLAlchemy ORM models + Alembic migration

**Intent:** Define the 7 Postgres tables from §6.1 as SQLAlchemy 2 ORM classes, wire
Alembic to the same metadata, and produce the first migration revision that includes the
`CREATE EXTENSION IF NOT EXISTS vector` DDL and all tables.

**Expected outcomes:**
- `make migrate` (i.e. `alembic upgrade head`) against a live Postgres + pgvector container applies cleanly with no errors.
- `alembic current` shows revision `0001`.
- All 7 tables exist in the DB with the column types from §6.1.
- `chunks.embedding` is `vector(768)`.

**Todo list:**
1. Create `backend/db/__init__.py`: define async SQLAlchemy engine from `DATABASE_URL` env var, `AsyncSession` factory, `Base` declarative base, and async `get_session` dependency.
2. Create `backend/db/models.py` with ORM classes for all 7 tables from §6.1:
   - `Case`: id (UUID PK), status (str), created_at (datetime), user_id (str), category (str nullable), success_prob (float nullable).
   - `Document`: id (UUID PK), case_id (UUID FK→cases), kind (str), storage_url (str), extracted_json (JSONB nullable).
   - `Chunk`: id (UUID PK), doc_id (UUID FK→documents), text (text), embedding (Vector(768)), metadata (JSONB).
   - `AgentRun`: id (UUID PK), case_id (UUID FK→cases), agent (str), input_hash (str), output (JSONB), tokens_in (int), tokens_out (int), latency_ms (int), cost_usd (numeric nullable), trace_id (str).
   - `Draft`: id (UUID PK), case_id (UUID FK→cases), version (int), body (text), grounding_report (JSONB nullable), approved_by (str nullable), approved_at (datetime nullable).
   - `Reminder`: id (UUID PK), case_id (UUID FK→cases), due_at (datetime), kind (str), sent (bool default false).
   - `PiiVault`: case_id (UUID PK FK→cases), token_map (text — stored as encrypted blob).
3. Create `backend/db/migrations/` directory with `alembic.ini` pointing `script_location = db/migrations`, `sqlalchemy.url` overridden from env.
4. Create `backend/db/migrations/env.py`: import `Base` from `backend.db`, set `target_metadata = Base.metadata`, configure `run_migrations_online` for async engine.
5. Create `backend/db/migrations/versions/0001_initial.py`: revision creates pgvector extension then all 7 tables. Use `op.execute("CREATE EXTENSION IF NOT EXISTS vector")` before table creation.

**Relevant context:** SPEC §6.1 table, §16 (idempotency key on agent_runs). Use `pgvector.sqlalchemy.Vector` for the embedding column.

**Status:** [x] done

---

### ST-4 — FastAPI app + `/health` endpoint

**Intent:** Stand up the minimum viable FastAPI application: CORS middleware restricted to
`ALLOWED_ORIGINS`, a stub `app/api` router, a stub `app/tools` router, and the `/health`
endpoint. This is the deliverable that must be "live on Render".

**Expected outcomes:**
- `GET /health` returns `{"status": "ok", "db": "ok"}` (db check: simple `SELECT 1`).
- `GET /openapi.json` documents the health endpoint.
- App starts with `uvicorn backend.app.main:app`.
- CORS origin is read from `ALLOWED_ORIGINS` env var.

**Todo list:**
1. Create `backend/app/api/__init__.py` with an empty `APIRouter` exported as `router`.
2. Create `backend/app/tools/__init__.py` with an empty `APIRouter` exported as `router`.
3. Create `backend/config/models.yaml` with the 4 model-tier keys from §5.1.
4. Create `backend/app/main.py`: instantiate `FastAPI(title="ClaimBack API")`, add `CORSMiddleware` with origins from env, include `api.router` under `/api` prefix and `tools.router` under `/tools` prefix, define `GET /health` that runs `SELECT 1` via `get_session` and returns JSON status.

**Relevant context:** SPEC §4 (Render deployment), §7 (API structure), §12 (CORS). Keep the router mounts minimal — no route logic beyond `/health` on Day 1.

**Status:** [x] done

---

### ST-5 — Synthetic data generator

**Intent:** Implement `data/generate.py` using Faker (deterministic via `--seed`) and
Jinja2 HTML templates to produce 250 denial-letter cases + ground-truth labels. No
watsonx calls on Day 1 — text fields are filled from templates and Faker. The generator
must be runnable standalone (`python data/generate.py --seed 42`).

**Expected outcomes:**
- `python data/generate.py --seed 42 --count 250` exits 0 and writes:
  - `data/generated/cases.jsonl` — 250 ground-truth label records.
  - `data/generated/denial_letters/<id>.html` — 250 denial letter HTML files.
  - `data/generated/eobs/<id>.html` — 250 EOB HTML files.
- Running twice with the same seed produces identical output (deterministic).
- 20% of cases have injected coding errors, 15% have no denial code, 10% are expedited-eligible, 10% have a "low quality scan" flag.
- 200 cases labelled `split: eval`, 50 labelled `split: held_out`.

**Todo list:**
1. Create `data/fixtures/cpt_icd10.json`: ~300 CPT codes and ~100 ICD-10 codes as a JSON object with a `valid_pairs` list (list of `[cpt, icd10]` tuples representing valid combinations). Keep codes realistic but entirely fictionalised.
2. Create `data/fixtures/state_rules.json`: 10 US states with fields `internal_days`, `external_review_available`, `external_days_or_null`, `expedited_days`. Values are fictionalised but plausible (30–180 day range).
3. Create `data/fixtures/payers.json`: 4 payer names × 2 plan types (employer, marketplace) = 8 policy definitions with payer name, plan type, and a short coverage description string.
4. Create `data/templates/denial_letter.html`: Jinja2 template receiving `case` dict; renders a realistic denial letter with payer logo placeholder, patient name, claim ID, CPT codes, denial reason, and appeal deadline notice.
5. Create `data/templates/eob.html`: Jinja2 template rendering a one-page explanation of benefits with billed amount, adjustment, and denial code.
6. Create `data/generate.py`:
   - Parse `--seed` (default 42) and `--count` (default 250) CLI args.
   - Seed `random`, `faker` with the given seed.
   - Load fixtures from `data/fixtures/`.
   - For each case: generate a `case_id` (UUID), pick payer + plan_type + state, generate service/denial dates, pick CPT + ICD-10 codes (from fixtures), apply difficulty injections (coding_error, missing_denial_code, low_quality, expedited), set `split` (first 200 = eval, last 50 = held_out).
   - Render denial letter and EOB HTML using Jinja2.
   - Write `cases.jsonl` (one JSON object per line, schema matches `ExtractedClaim` + ground-truth fields).
   - Write HTML files to `data/generated/denial_letters/` and `data/generated/eobs/`.
   - Print summary counts to stdout.
   - `generate.py` must have no watsonx imports — purely Faker + Jinja2 + stdlib.

**Relevant context:** SPEC §9 (artifact counts, difficulty injections, splits). The output schema for `cases.jsonl` must match `ExtractedClaim` fields so Agents 3–5 have known targets.

**Status:** [x] done

---

### ST-6 — Repo skeleton, CI stub, README

**Intent:** Create the remaining directories and placeholder files so the full §14 layout
exists in Git, the CI workflow runs ruff + mypy without errors, and a minimal README
explains local setup.

**Expected outcomes:**
- `git status` shows no untracked directories outside the spec layout.
- `ruff check backend/` passes (zero errors on the scaffolded code).
- `mypy backend/` passes (zero errors; models are fully annotated).
- GitHub Actions CI workflow file is present and valid YAML.
- `README.md` documents: prerequisites, `docker-compose up`, `make migrate`, `make data`, `make dev`.

**Todo list:**
1. Create `.gitkeep` files in: `orchestrate/agents/`, `orchestrate/tools/`, `orchestrate/flows/`, `orchestrate/knowledge/`, `prompts/`, `evals/results/`, `replay/`, `frontend/` (placeholder until Next.js scaffold on Day 6).
2. Create `.github/workflows/ci.yml`: trigger on push/PR to `main`; job: checkout, setup Python 3.11, install `backend[dev]`, run `ruff check backend/`, run `mypy backend/`.
3. Create `README.md`: title, one-line description, prerequisites (Docker, Python 3.11, make), "Quick start" section (clone, copy `.env.example`, docker-compose up, make migrate, make data, make dev), link to SPEC.md.

**Relevant context:** SPEC §14 (full directory tree), §16 (ruff + mypy required), §17 (CI mentioned).

**Status:** [x] done

---

## Implementation order

```
ST-1  →  ST-2  →  ST-3
                      ↓
               ST-4  (needs ST-2 models + ST-3 db)
               ST-5  (needs ST-2 models for output schema)
               ST-6  (needs all prior files to lint cleanly)
```

ST-2 and ST-3 must complete before ST-4. ST-5 is independent of ST-3/ST-4 and can
run in parallel with them, but should wait for ST-2 (needs `ExtractedClaim` schema).
ST-6 runs last.
