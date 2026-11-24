.PHONY: data dev migrate lint test ingest orchestrate-export orchestrate-import eval-day5 train-scorer record-replay

# ── Synthetic data ────────────────────────────────────────────────────────────
data:
	python data/generate.py --seed 42 --count 250

# ── Policy ingest (run after make data; requires DATABASE_URL + WATSONX_* set) ─
ingest:
	python backend/ingest_policies.py

# ── Local development ─────────────────────────────────────────────────────────
dev:
	docker compose up -d db
	cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# ── Database migrations ───────────────────────────────────────────────────────
migrate:
	cd backend && alembic upgrade head

# ── Lint & type-check ─────────────────────────────────────────────────────────
lint:
	ruff check backend/
	cd backend; mypy .

# ── Tests ─────────────────────────────────────────────────────────────────────
test:
	cd backend && pytest ../tests/ -v

# ── Orchestrate — export static OpenAPI file from live backend ─────────────────
# Run `make dev` first, then `make orchestrate-export` to regenerate the file.
orchestrate-export:
	curl -sf http://localhost:8000/tools/openapi.json \
		-o orchestrate/tools/claimback_openapi.json
	@echo "✓ orchestrate/tools/claimback_openapi.json updated"

# ── Orchestrate — full import sequence (checked against ADK 2.20) ─────────────
# Windows: use scripts/orchestrate-import.ps1 -BackendUrl https://<backend> instead.
# Prerequisites: `orchestrate env activate <env>`; a connection named claimback_api
# holding the backend TOOLS_API_KEY (see the PowerShell script); and the tool spec's
# `servers` URL pointing at the deployed backend (Orchestrate can't reach localhost).
orchestrate-import:
	orchestrate tools import -k openapi -f orchestrate/tools/claimback_openapi.json --app-id claimback_api
	orchestrate knowledge-bases import -f orchestrate/knowledge/faq.yaml
	orchestrate agents import -f orchestrate/agents/intake_agent.yaml
	orchestrate agents import -f orchestrate/agents/explainer_agent.yaml
	orchestrate agents import -f orchestrate/agents/followup_agent.yaml
	orchestrate agents import -f orchestrate/agents/claimback_supervisor.yaml
	@echo "Approval flow not imported: orchestrate/flows/approval_and_followup.py needs the real ADK flow API"

# ── Day-5 evals ───────────────────────────────────────────────────────────────
train-scorer:
	python evals/train_scorer.py

eval-day5:
	python evals/eval_writer_guardian.py --subset 5
	python evals/eval_scorer.py

# ── Replay fixture recording (Day 7) ─────────────────────────────────────────
# Requires a running backend (make dev) and DATABASE_URL set.
# Pass CASE_IDS as a space-separated list of UUIDs from live pipeline runs.
# Example: make record-replay CASE_IDS="uuid1 uuid2 uuid3 uuid4 uuid5"
record-replay:
	python -m scripts.record_replay --case-ids $(CASE_IDS)
