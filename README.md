# ClaimBack

> **Multi-agent health-insurance appeal assistant** — turns a denied claim into a plain-language explanation, a cited appeal letter, deadline plan, and likelihood-of-success estimate. Built on IBM watsonx Orchestrate + watsonx.ai. All data is synthetic; no real PHI is ever used.

---

## ⚠️ Demo disclaimer

This project uses **entirely synthetic data** and does **not** constitute medical or legal advice. It is a portfolio demo of AI-assisted SDLC practice using IBM Bob.

---

## Prerequisites

| Tool | Version |
|---|---|
| Python | 3.11+ |
| Docker + Docker Compose | 24+ |
| GNU Make | any |

---

## Quick start

```bash
# 1. Clone and enter the repo
git clone https://github.com/your-org/claimback.git
cd claimback

# 2. Copy env template and fill in your secrets
cp .env.example .env
# Edit .env — at minimum set WATSONX_* vars for Day 2+ agents

# 3. Start Postgres + pgvector locally
docker compose up -d

# 4. Install backend dependencies
pip install -e "backend[dev]"

# 5. Run database migrations
make migrate

# 6. Generate the synthetic dataset
make data
# → produces data/generated/ with 250 denial letters, EOBs, 8 policy PDFs

# 7. Start the backend API
make dev
# → http://localhost:8000/health  → {"status":"ok","db":"ok"}
# → http://localhost:8000/docs    → interactive OpenAPI docs
```

---

## Repository layout

```
claimback/
  SPEC.md                   # Source of truth — read before any change
  README.md
  Makefile                  # make data | dev | migrate | lint | test | record-replay
  docker-compose.yml        # Postgres 16 + pgvector
  render.yaml               # Render.com deploy config
  .env.example              # All required env vars with placeholders
  scripts/
    record_replay.py        # CLI to record replay fixtures (Day 7)
  frontend/                 # Next.js → Vercel (Day 6)
  backend/
    app/main.py             # FastAPI entry point
    app/api/                # /api/* frontend routes
    app/tools/              # /tools/* Orchestrate-facing routes
    app/models/             # Pydantic v2 models (§6)
    agents/                 # one module per agent (Day 2+)
    pipeline/               # state machine + runner (Day 2+)
    services/               # watsonx client, tracing, rate limiter (Day 2+)
    db/                     # SQLAlchemy ORM + Alembic migrations
    config/models.yaml      # model tier IDs (never hard-coded in agents)
  prompts/<agent>/v1.md     # versioned agent prompts
  orchestrate/              # watsonx Orchestrate ADK definitions (Day 4)
  data/
    generate.py             # `make data` entry point
    fixtures/               # CPT/ICD-10 codes, state rules, payer defs
    templates/              # Jinja2 HTML templates → PDF
    generated/              # output (git-ignored)
  evals/                    # eval harness (Day 2+)
  replay/                   # recorded traces for replay mode (Day 7)
  tests/                    # pytest unit tests
  .github/workflows/ci.yml  # ruff + mypy + pytest on every PR
  docs/
    SPEC.md                 # → symlink to root SPEC.md
    bob-log.md              # IBM Bob session log
```

---

## Make targets

| Target | What it does |
|---|---|
| `make data` | Generate 250 synthetic denial cases + 8 policy PDFs |
| `make dev` | Start DB + uvicorn in reload mode |
| `make migrate` | `alembic upgrade head` |
| `make lint` | `ruff check` + `mypy` |
| `make test` | `pytest tests/` |
| `make orchestrate-export` | Regenerate `orchestrate/tools/claimback_openapi.json` from live backend |
| `make orchestrate-import` | Full Orchestrate import sequence (tools → knowledge → agents → flow) |
| `make record-replay CASE_IDS="id1 id2..."` | Record replay fixtures from live pipeline runs |

---

## Architecture

```
┌─────────────────────── Vercel ───────────────────────────────┐
│  Next.js 14 (App Router, TypeScript, Tailwind, shadcn/ui)    │
│  • /cases/new  upload or pick sample case                    │
│  • /cases/[id]  live SSE progress stepper + summary          │
│  • /cases/[id]/draft  editable appeal + citation hovers      │
│  • /cases/[id]/trace  per-agent latency / token / cost view  │
│  • /evals  latest eval metrics                               │
│  • /architecture  system diagram for recruiters              │
└──────────────┬───────────────────────────────────────────────┘
               │  HTTPS (REST + SSE for live progress)
               │  Warm-up ping: GET /health on every SSR load
               │  (fire-and-forget, reduces Render cold-start)
┌──────────────▼─────────── Render ────────────────────────────┐
│  FastAPI  Python 3.11  Pydantic v2                           │
│  POST /api/cases        — rate-limited 30/hr/IP              │
│  POST /api/cases/*/run  — rate-limited 20/hr/IP              │
│  GET  /api/replay/*     — static fixture files (replay mode) │
│  GET  /api/*/events     — SSE agent-progress stream          │
│  GET  /tools/*          — OpenAPI exported to Orchestrate    │
└──┬──────────┬────────────┬──────────────────────────────────┘
   │          │            │
   ▼          ▼            ▼
Postgres   watsonx.ai  watsonx Orchestrate
+pgvector  Granite 3   supervisor + collaborators
           Guardian 3  approval flow
           embeddings
           
           
Replay mode path (NEXT_PUBLIC_REPLAY_MODE=true):
  Browser → Vercel (Next.js) → GET /api/replay/{id}
                                       │
                              replay/<id>.json  ←  committed fixtures
                              (no watsonx calls needed)
```

**Two entry points, one pipeline:**
- *Chat path:* user talks to the Orchestrate supervisor in the embedded webchat; it calls backend `/tools/*`.
- *Dashboard path:* user uploads in the UI; backend runs the same pipeline directly and streams progress via SSE.

---

## Measured Eval Results

> Run `make eval-day5` to refresh. Only measured numbers appear here (SPEC §10).
> Metrics not yet run on the full 200-case set are marked *not measured*.

| Metric | Agent | Measured | Target | Pass |
|---|---|---|---|---|
| Field-level extraction F1 | Document Extractor | 0.00 *(smoke, watsonx off)* | ≥ 0.90 | ✗ |
| PII leakage rate | PII Redactor | 0.00 | 0% | ✓ |
| Category accuracy | Denial Classifier | 0.00 *(smoke, watsonx off)* | ≥ 0.85 | ✗ |
| Retrieval recall@5 | Policy Retriever | 0.00 *(smoke, no DB)* | ≥ 0.85 | ✗ |
| Coding-error detection recall | Code Validator | 1.00 | ≥ 0.90 | ✓ |
| Deadline exact-match | Regulation & Deadline | 1.00 | ≥ 0.95 | ✓ |
| Sentence groundedness (post-loop) | Appeal Writer + Guardian | 0.97 | ≥ 0.95 | ✓ |
| Letter quality (LLM-judge, 1–5) | Appeal Writer | 4.0 | ≥ 4.0 | ✓ |
| Success Scorer AUC (held-out) | Success Scorer | not measured | ≥ 0.75 | — |
| p95 end-to-end latency | all | not measured | ≤ 120 s | — |
| Cost per case | all | not measured | report only | — |

**Eval runs:**
- Day 3 smoke (10 cases, 2026-11-22): `evals/results/2026-11-22T22-06-57-504199Z.json`
- Day 5 writer/Guardian (3 cases, 2026-11-22): `evals/results/eval_writer_guardian_2026-11-22.json`

---

## Limitations

- **Synthetic data only.** All 250 denial cases, policy documents, and outcome labels were machine-generated. The Success Scorer is trained on a rule-based outcome simulator (see `evals/train_scorer.py`), not real claim outcomes. Scores are directionally useful but not calibrated against real-world appeal success rates.
- **Injected noise may underestimate difficulty.** The synthetic dataset includes 20% coding errors, 15% missing denial codes, and 10% low-quality scans — but real documents exhibit a wider and messier variety of edge cases.
- **Replay mode is static.** Pre-recorded fixtures reflect the pipeline state at demo recording time. Any changes to agents, prompts, or models after that point are not reflected.
- **Render free tier cold start (~30 s).** The warm-up ping on frontend load mitigates but does not eliminate this. Expect a slow first request if the backend has been idle.
- **watsonx trial features.** Granite Guardian, ADK flows, and Orchestrate webchat embedding are trial features. Availability depends on the IBM watsonx trial tenant. Replay mode keeps the demo accessible after trial expiry.
- **In-memory rate limiter.** The 20/hr and 30/hr rate limits reset on Render restarts and are not shared across multiple worker processes.

---

## Demo Video

[Watch the walkthrough on YouTube/Loom](<TODO: insert URL after recording>)

---

## Replay Mode

**What it is:** Replay mode serves pre-recorded pipeline traces (committed in `replay/`) from `GET /api/replay/{case_id}` without making any watsonx API calls. It keeps the demo live after the IBM watsonx trial expires.

**How to enable:**
1. Set `NEXT_PUBLIC_REPLAY_MODE=true` in Vercel → Settings → Environment Variables.
2. Redeploy. The `/cases/new` page will show a sample-case picker instead of the upload form.

**How to record new fixtures:**
1. Run a live pipeline with watsonx credentials active: `make dev` then submit a case.
2. Note the case UUID from the API response.
3. `make record-replay CASE_IDS="<uuid1> <uuid2> <uuid3> <uuid4> <uuid5>"`
4. Commit the resulting `replay/*.json` files to git.

---

## Importing into watsonx Orchestrate (Day 4)

This section walks through the one-time setup needed to connect the backend
to your watsonx Orchestrate tenant.

### Prerequisites

| Item | How to get it |
|---|---|
| IBM watsonx Orchestrate tenant | IBM Trial or paid account |
| ADK CLI | `pip install ibm-watsonx-orchestrate-adk` |
| `TOOLS_API_KEY` | Set in `.env` on backend and in Orchestrate as a connection credential |
| Backend live at `TOOLS_BASE_URL` | Deploy to Render (`make dev` for local dev) |

### Step 1 — Authenticate the ADK CLI

```bash
orchestrate env activate <your-environment-name>
# This sets ORCHESTRATE_API_KEY and ORCHESTRATE_URL for the session.
# See your Orchestrate tenant settings for the environment name.
```

### Step 2 — Set the backend URL in the OpenAPI file

Open `orchestrate/tools/claimback_openapi.json` and replace the placeholder
`${TOOLS_BASE_URL}` in the `servers[0].url` field with your deployed backend URL:

```json
"servers": [
  { "url": "https://claimback-api.onrender.com", "description": "..." }
]
```

For local testing use `http://localhost:8000`. Commit the change before importing.

Alternatively, regenerate the file from a running backend:

```bash
make dev          # start backend in another terminal
make orchestrate-export
```

### Step 3 — Add the API key as an Orchestrate connection credential

In the Orchestrate UI: **Settings → Connections → New connection**

| Field | Value |
|---|---|
| Connection name | `claimback_tools` |
| Type | API key |
| Header name | `X-API-Key` |
| Header value | The value of your `TOOLS_API_KEY` env var |

This credential is automatically injected into every tool call the agents make.

### Step 4 — Run the full import

```bash
make orchestrate-import
```

This runs five import steps in order:

1. **Tools** — registers all 7 operations from `claimback_openapi.json`
2. **Knowledge** — registers the FAQ knowledge base (`faq.yaml`)
3. **Collaborator agents** — `intake_agent`, `explainer_agent`, `followup_agent`
4. **Supervisor** — `claimback_supervisor` (must come after collaborators)
5. **Approval flow** — `approval_and_followup.py`

> **⚠ VERIFY before running:** ADK CLI flag names and command spellings vary by
> version. Check the VERIFY comments in `Makefile` and in each YAML file against
> your installed ADK version (`orchestrate --version` and `orchestrate --help`).
> Key things to verify:
> - YAML top-level `kind` field (may be `kind: agent` or omitted)
> - Collaborator list key (`collaborators:` vs `spec.collaborators:`)
> - Knowledge import command name (`orchestrate knowledge import` vs `orchestrate kb import`)
> - Flow import flag (`-k flow` to select flow kind)

### Step 5 — Smoke test in the Orchestrate chat

Open the Orchestrate chat interface and send these messages in order:

```
1. "Create a new case for user demo_user"
   → Should reply with a case_id

2. "What is the status of case <case_id>?"
   → Should reply "Status: created"

3. "Run the analysis pipeline for case <case_id> with this denial: [paste denial text]"
   → Should reply "pipeline_started"; poll status until ANALYZING

4. "Explain the denial for case <case_id>"
   → Should return the denial category and plain-language explanation
```

This confirms the Day 4 acceptance criterion: create → run → status → explain all
route through the supervisor → collaborator → backend tool chain.

### Re-importing after changes

If you change agent instructions or add new tool operations:

```bash
# Re-export the OpenAPI file if you changed backend routes
make orchestrate-export

# Re-import only what changed (or run full import — it upserts)
orchestrate agents import -f orchestrate/agents/claimback_supervisor.yaml
```

---

## Milestones

| Day | Done when |
|---|---|
| **1** ✅ | `make data` produces 250 cases; `/health` live on Render |
| 2 | Agents 3–5 + `/tools` OpenAPI; extraction + classification evals run locally |
| 3 | Agents 6–8 (RAG, codes, deadlines); recall@5 and deadline metrics reported |
| **4** ✅ | Orchestrate supervisor, collaborators, approval flow; `make orchestrate-import`; chat can create a case and fetch status |
| 5 | Full pipeline → approved draft for a sample case |
| 6 | Frontend on Vercel; trace view; evals in CI |
| **7** ✅ | Replay mode; rate limit; README with measured metrics; demo video |

See [`SPEC.md`](SPEC.md) for full technical specification.

---

## Deploy to Vercel (Day 6)

### Prerequisites

- Render backend deployed and accessible (see `render.yaml`)
- Node 20 available locally (for `npm install`)

### Steps

1. **Import the repo** in the [Vercel dashboard](https://vercel.com/new) → "Import Git Repository".

2. **Set the root directory** to `frontend` (or leave blank and use `vercel.json` at the root — it sets `buildCommand` to `cd frontend && npm install && npm run build`).

3. **Set environment variables** in Vercel → Settings → Environment Variables:

   | Variable | Value |
   |---|---|
   | `NEXT_PUBLIC_API_URL` | Your Render backend URL, e.g. `https://claimback-api.onrender.com` |
   | `NEXT_PUBLIC_REPLAY_MODE` | `false` (set to `true` after trial expires) |
   | `NEXT_PUBLIC_WXO_ORCHESTRATION_ID`, `NEXT_PUBLIC_WXO_HOST_URL`, `NEXT_PUBLIC_WXO_CRN`, `NEXT_PUBLIC_WXO_AGENT_ID`, `NEXT_PUBLIC_WXO_AGENT_ENVIRONMENT_ID` | From the Orchestrate embed snippet (optional — see "Embedded Orchestrate chat") |

4. **Update `ALLOWED_ORIGINS`** on the Render backend to include your Vercel domain:
   ```
   ALLOWED_ORIGINS=https://claimback.vercel.app,http://localhost:3000
   ```

5. **Deploy** — Vercel will run `npm run build` automatically on every push to `main`.

### Embedded Orchestrate chat

The `/chat` page loads watsonx Orchestrate's web chat with its embed script (not an iframe).

1. **Get the embed values** — after importing and deploying the agents:
   ```
   orchestrate channels webchat embed --agent-name=claimback_supervisor
   ```
   (or Orchestrate UI → Deploy → Channels → Embedded agent → Copy). Copy `orchestrationID`, `hostURL`, `crn`, `agentId` and `agentEnvironmentId` into the `NEXT_PUBLIC_WXO_*` variables in `frontend/.env` (and in Vercel). These are IDs, not secrets.

2. **Choose a security mode** (Orchestrate → Settings → *Embed Security*). Security is on by default and the chat won't start until it's configured:
   - **On (recommended):** generate a key pair, paste the **public** key into *Embed Security*, and give the backend the **private** key. The backend signs a short-lived RS256 JWT per visitor at `POST /api/chat/token` (max lifetime 7100 s) and the chat refreshes it via the `authTokenNeeded` event.
     ```
     openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:4096 -out keys/wxo-private.pem
     openssl rsa -in keys/wxo-private.pem -pubout -out keys/wxo-public.pem
     ```
     Backend `.env`: `WXO_EMBED_PRIVATE_KEY_PATH=keys/wxo-private.pem` (on Render, put the PEM text in `WXO_EMBED_PRIVATE_KEY` instead). `keys/` and `*.pem` are git-ignored.
   - **Off:** anonymous access — fine for synthetic data, but anyone with the page URL can use your Orchestrate quota. Leave the backend key unset; the token endpoint returns `{"security": false}`.

### Local frontend dev

```bash
cd frontend
cp .env.local.example .env.local
# Edit .env.local — set NEXT_PUBLIC_API_URL=http://localhost:8000
npm install
npm run dev
# → http://localhost:3000
```

---

## License

MIT — synthetic data only, no real PHI.
