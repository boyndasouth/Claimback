# IBM Bob prompts — ClaimBack build

**Each day:**
1. **Chat A:** new chat → **Plan** prompt → review/edit the plan → switch to **Agent** mode in the *same* chat → build prompt → fix-ups until it runs → commit.
2. **Chat B:** new chat → **Ask** mode check prompt (fresh eyes, small context) → fix anything it finds back in Chat A → commit.

The project rules in `.bob/rules/claimback.md` are injected into every conversation automatically.

---

## Day 1 — Scaffold, schemas, synthetic data

**Plan**
> Read SPEC.md §6, §9, §14, §15, §18. Plan Day 1 only: the repo layout from §14, Pydantic models from §6, SQLAlchemy models + first Alembic migration for §6.1, the synthetic data generator from §9 with a `--seed` flag, FastAPI `/health`, docker-compose with Postgres + pgvector, `.env.example` from §15, and a Render deploy config. List each file and the build order. No code yet.

**Agent**
> Implement the approved Day 1 plan. Generator target: `make data` produces 250 cases (denial letter PDF + EOB PDF + ground-truth JSON), 8 policy PDFs, 10 state rule files, and the CPT/ICD-10 subset, with the injected-difficulty percentages from §9. Write unit tests for the schemas and the generator.

**Ask (check)**
> Compare the repo against SPEC §14 and §18 Day 1 "Done when". List anything missing or anything that differs from §6.

---

## Day 2 — Extraction, redaction, classification + tools API

**Plan**
> Plan Day 2 from SPEC §5 (agents 3, 4, 5), §5.1, §7.2, §11, §16. Include: a shared `Agent` interface and `AgentResult`, the watsonx client in `services/watsonx.py` with timeout/retry/circuit breaker, a tracing decorator, prompts in `prompts/<agent>/v1.md`, and the `/tools` router with an OpenAPI export and an X-API-Key check. No code yet.

**Agent**
> Implement the Day 2 plan. Add a Docling fallback for the Document Extractor. The PII Redactor must store its token map encrypted with PII_VAULT_KEY. Write tests, including one that asserts no PII reaches downstream prompts. Add `evals/run.py` with the extraction F1, PII leakage and category accuracy metrics from §10.

**Ask (check)**
> Review `services/watsonx.py` and the agents for missing error handling, hard-coded model IDs or secrets. Point to exact lines.

---

## Day 3 — RAG, code validator, deadlines

**Plan**
> Plan Day 3 for agents 6, 7 and 8 (SPEC §5). Cover: chunking strategy for policy PDFs, Granite embeddings into pgvector, hybrid retrieval (vector + BM25), the CPT/ICD valid-pair rules, deadline date math from the state rules, and running 6/7/8 in parallel with asyncio.gather (§16). No code yet.

**Agent**
> Implement the Day 3 plan, including an ingestion script `make ingest`. Add recall@5, coding-error recall and deadline exact-match to `evals/run.py`. Write tests for the deadline edge cases: weekends, expedited cases, missing denial date.

**Ask (check)**
> Run through one synthetic case manually: which policy clauses come back for a medical-necessity MRI denial, and why? Are the scores sensible?

---

## Day 4 — watsonx Orchestrate

**Plan**
> Plan Day 4 from SPEC §8 and §7.2. I'm using the watsonx Orchestrate ADK. Plan the YAML for the supervisor and its collaborator agents (intake, explainer, followup), exporting `/tools/openapi.json` for import, the approval_and_followup flow, the FAQ knowledge base, and a `make orchestrate-import` target. Flag anything you're unsure about in the ADK format so I can check it against IBM's docs.

**Agent**
> Implement the Day 4 plan. Write the supervisor instructions from §8 exactly. Add a README section on importing into Orchestrate step by step.

**Ask (check)**
> Check the agent YAML and the tool OpenAPI spec for mismatches: operationIds, required params, auth headers.

> ⚠️ Check the ADK file format against IBM's current docs yourself — Bob may guess at the syntax.

---

## Day 5 — Writer, Guardian loop, scorer, follow-up

**Plan**
> Plan Day 5: agents 9–13 from SPEC §5, the Writer ⇄ Guardian self-correction loop in §5.2 (max 2 rewrites, then NEEDS_HUMAN_REVIEW), the success scorer trained on the synthetic outcomes (LightGBM + Granite explanation), the full pipeline state machine using the CaseStatus values, and idempotent caching by (case_id, agent, input_hash). No code yet.

**Agent**
> Implement the Day 5 plan plus the `/api` endpoints from §7.1, including SSE progress events. Add the groundedness score, LLM-judge letter rubric and scorer AUC to evals. Write an end-to-end test that runs one sample case to AWAITING_APPROVAL.

**Ask (check)**
> Trace one full case through the pipeline. Where could it loop forever, lose state or leak PII?

---

## Day 6 — Frontend + CI

**Plan**
> Plan Day 6 from SPEC §13: Next.js routes, the SSE progress UI, the draft editor with citation hovers and highlighted flagged spans, the trace timeline, the /evals page and the Orchestrate webchat embed. Also `.github/workflows/ci.yml` (ruff, mypy, tsc, tests, a 30-case eval smoke run) and Vercel config. No code yet.

**Agent**
> Implement the Day 6 plan with shadcn/ui and Tailwind. Show the synthetic-data/not-advice banner from §12 on every page. Make it work on mobile. Set CORS on the backend to ALLOWED_ORIGINS.

**Ask (check)**
> Run a security review of the upload path, CORS, rate limiting and secrets handling against SPEC §12.

---

## Day 7 — Replay mode, hardening, docs

**Plan**
> Plan Day 7: replay mode from §13 (record traces to /replay, serve them from `/api/replay`, NEXT_PUBLIC_REPLAY_MODE), the frontend warm-up ping for Render, the rate limit, and the README with an architecture diagram, measured eval results, limitations and the demo-video link. No code yet.

**Agent**
> Implement the Day 7 plan. Record replay traces for 5 sample cases. Write the README using only the numbers in the latest `evals/results` file.

**Ask (check)**
> Check every item in SPEC §19's acceptance criteria and say pass or fail for each, with evidence.

---

## Saving bobcoins

- When Bob gets something wrong, reply in the same chat: *"Fix only X in file Y; don't touch anything else."*
- Make small edits yourself rather than prompting Bob.
- One chat per milestone — long chats cost more.
- Log every session in `docs/bob-log.md`.
