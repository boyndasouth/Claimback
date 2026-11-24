# Day 5 Plan — Agents 9–13, Writer/Guardian Loop, Success Scorer, Pipeline State Machine

## Overview

Day 5 completes the backend pipeline so that a case can travel all the way from
the end of the ANALYZING stage through to an approved-ready draft.  Five agents
are added (Evidence Builder, Appeal Writer, Groundedness Checker, Success Scorer,
Follow-up Tracker), the Writer ⇄ Guardian self-correction loop is wired with a
hard cap of 2 rewrites, the LightGBM success scorer is trained on synthetic
outcomes and augmented with a Granite plain-language explanation, the full
CaseStatus state machine transitions are enforced inside `_run_pipeline_background`,
and idempotent caching by `(case_id, agent, input_hash)` is surfaced as a
reusable helper used by every Day-5 agent run.

**Scope boundary:** no frontend changes; no new API routes; no Orchestrate flows.
The `/tools/case_get_draft`, `/tools/case_approve` stubs are wired up as a side
effect of sub-task 5 (pipeline wiring).

---

## Sub-Tasks

---

### Sub-Task 1 — Add `EvidenceChecklist` model and `EvidenceBuilderInput` composite

**Status:** [ ] pending

**Intent**

`EvidenceChecklist` is the output of Agent 9 and is referenced by Agent 10's
prompt template but is not yet defined in the codebase.  A lightweight composite
input type (`EvidenceBuilderInput`) is also needed to bundle the three inputs
Agent 9 consumes so the `@trace` decorator can hash them as a single object.

**Expected Outcomes**

- `EvidenceChecklist` and `EvidenceItem` are importable from `app.models`.
- `EvidenceBuilderInput` is importable from `app.models` (or from `agents/evidence_builder.py` as a local dataclass).
- All existing model tests continue to pass.

**Todo List**

1. Open `backend/app/models/appeal.py` and add `EvidenceItem` (fields: `item_id: str`, `description: str`, `rationale: str`, `priority: Literal["high","medium","low"]`) and `EvidenceChecklist` (fields: `items: list[EvidenceItem]`).
2. Export both names from `backend/app/models/__init__.py`.
3. Add a `WriterInput` composite to `backend/app/models/appeal.py` (fields: `checklist: EvidenceChecklist`, `claim: ExtractedClaim`, `clauses: list[PolicyClause]`, `issues: list[CodeIssue]`, `plan: AppealPlan`) — Agent 10 needs a single hashable input object.
4. Export `WriterInput` from `__init__.py`.
5. Add minimal Pydantic field tests for `EvidenceChecklist` and `WriterInput` to `tests/test_models.py` following the existing pattern.

**Relevant Context**

- `backend/app/models/appeal.py` — existing `AppealDraft`, `GroundingReport`, `SuccessEstimate` live here; follow same style.
- `backend/app/models/__init__.py` — public re-exports; must list new names.
- `tests/test_models.py` — existing test classes `TestAppealDraft`, `TestGroundingReport` show the pattern.

---

### Sub-Task 2 — Implement Agent 9: Evidence Builder

**Status:** [ ] pending

**Intent**

Agent 9 consumes the denial category, retrieved policy clauses, and code issues
from the ANALYZING stage and produces a structured `EvidenceChecklist` — an
ordered list of items the patient should gather before the appeal is written.
This output is passed directly to Agent 10 (Appeal Writer).

**Expected Outcomes**

- `backend/agents/evidence_builder.py` exists with class `EvidenceBuilder`.
- Class follows the established pattern: `name = "evidence_builder"`, `@trace(prompt_version="v1")`, async `run(input: EvidenceBuilderInput, ctx: CaseContext) -> AgentResult[EvidenceChecklist]`.
- Prompt template at `prompts/evidence_builder/v1.md` renders without error on a synthetic input.
- Unit test in `tests/test_evidence_builder.py` mocks `get_client()` and asserts the returned `AgentResult` has a non-empty `EvidenceChecklist`.

**Todo List**

1. Create `prompts/evidence_builder/v1.md` — Jinja2 template with YAML front-matter (`agent`, `version`); system block instructs Granite to output JSON `{"items": [...]}` with `item_id`, `description`, `rationale`, `priority`; inserts `{{ category }}`, `{{ clauses_json }}`, `{{ issues_json }}`.
2. Create `backend/agents/evidence_builder.py`:
   - Class `EvidenceBuilder` with `name = "evidence_builder"`.
   - Loads Jinja2 env pointing at `prompts/`.
   - `@trace(prompt_version="v1")` on `run()`.
   - Renders template, calls `client.generate(model_tier="routing", ...)`, parses JSON response, returns `AgentResult[EvidenceChecklist]`.
   - Uses `_extract_json_block` helper (copy from `denial_classifier.py` or factor into shared util).
3. Create `tests/test_evidence_builder.py` with one happy-path test (mock watsonx client) and one parse-error test.

**Relevant Context**

- `backend/agents/denial_classifier.py` — canonical pattern for prompt rendering + JSON parse + `AgentResult` construction.
- `prompts/denial_classifier/v1.md` — reference for YAML front-matter + Jinja2 template structure.
- Model tier: `"routing"` (Granite 3-8B).

---

### Sub-Task 3 — Implement Agent 10: Appeal Writer

**Status:** [ ] pending

**Intent**

Agent 10 is the first half of the self-correction loop.  It receives the full
`WriterInput` bundle and produces an `AppealDraft` with every factual claim
carrying a `[S#]` citation that resolves to a `SourceRef`.  On subsequent calls
(rewrites) it also receives the previous `GroundingReport`'s `flagged_spans` and
rewrites only the offending sentences, incrementing `version`.

**Expected Outcomes**

- `backend/agents/appeal_writer.py` exists with class `AppealWriter`.
- `run()` accepts either a bare `WriterInput` or a `WriterRetryInput` (adds `flagged_spans: list[FlaggedSpan]`, `previous_draft: AppealDraft`) without breaking the `@trace` hash — both are distinct hashable input types.
- Prompt at `prompts/appeal_writer/v1.md` instructs the model to include at least one `[S#]` citation per paragraph and emit JSON `{"body_markdown": "...", "citations": {"S1": {...}}}`.
- Unit test verifies that `citations` keys match all `[S#]` tags found in `body_markdown`.

**Todo List**

1. Add `WriterRetryInput` to `backend/app/models/appeal.py` (fields: `base: WriterInput`, `flagged_spans: list[FlaggedSpan]`, `previous_draft: AppealDraft`) and export it.
2. Create `prompts/appeal_writer/v1.md` — instructs Granite (writing tier) to produce a formal appeal letter with inline citations; includes a "REWRITE INSTRUCTIONS" section that is only rendered when `flagged_spans` is non-empty.
3. Create `backend/agents/appeal_writer.py`:
   - `AppealWriter` class, `name = "appeal_writer"`.
   - `run()` accepts `WriterInput | WriterRetryInput`; template branches on input type.
   - Calls `client.generate(model_tier="writing", max_new_tokens=1024)`.
   - Parses JSON; validates that every `[S#]` in `body_markdown` has a matching key in `citations`; sets `version = 1` on first call, `previous_draft.version + 1` on retry.
   - Returns `AgentResult[AppealDraft]`.
4. Unit test: mock watsonx, assert citation keys match tags; assert `version` increments on retry input.

**Relevant Context**

- SPEC §5.2: every sentence must carry a citation; uncited sentences will be flagged by Agent 11.
- `backend/app/models/base.py` — `FlaggedSpan`, `SourceRef` already defined.
- `AppealDraft.citations: dict[str, SourceRef]` — `SourceRef` has fields `clause_id`, `doc_id`, `section`, `text`.
- Model tier: `"writing"` (same Granite 3-8B model, different tier label — swap to larger if available).

---

### Sub-Task 4 — Implement Agent 11: Groundedness Checker (Guardian)

**Status:** [ ] pending

**Intent**

Agent 11 uses Granite Guardian to score each sentence in the draft against the
cited sources, returning a `GroundingReport`.  The report's `passed` flag drives
the loop-control logic in the pipeline runner (sub-task 6).

**Expected Outcomes**

- `backend/agents/groundedness_checker.py` exists with class `GroundednessChecker`.
- `run()` calls `client.generate(model_tier="guardian", ...)`.
- Returns `AgentResult[GroundingReport]` with `passed`, `flagged_spans`, `groundedness_score`.
- Unit test: a draft where all sentences are cited returns `passed=True`; a draft with an uncited sentence returns `passed=False` with one `FlaggedSpan`.

**Todo List**

1. Create `prompts/groundedness_checker/v1.md` — instructs Guardian to evaluate each sentence against the provided source texts and return JSON `{"passed": bool, "flagged_spans": [{"span": "...", "reason": "..."}], "groundedness_score": float}`.
2. Create `backend/agents/groundedness_checker.py`:
   - `GroundednessChecker`, `name = "groundedness_checker"`.
   - Input type: define `CheckerInput` (fields: `draft: AppealDraft`, `sources: list[SourceRef]`) in `app/models/appeal.py` and export it.
   - `@trace(prompt_version="v1")` on `run(input: CheckerInput, ctx: CaseContext)`.
   - Renders template injecting `body_markdown` + `source_texts` list.
   - Parses JSON response into `GroundingReport`.
   - Computes `groundedness_score = (total_sentences - len(flagged_spans)) / total_sentences` if model does not return it directly.
3. Unit test with mock client: happy-path and one-flagged-sentence scenario.

**Relevant Context**

- SPEC §10: target `groundedness_score >= 0.95` after the loop.
- Model tier: `"guardian"` → `ibm/granite-guardian-3-8b` from `config/models.yaml`.
- `backend/app/models/base.py` — `FlaggedSpan(span: str, reason: str)` already defined.
- SPEC §20: if Guardian API is unavailable on trial, fall back to a prompt-based judge using the `"routing"` tier.

---

### Sub-Task 5 — Implement Agent 12: Success Scorer (LightGBM + Granite explanation)

**Status:** [ ] pending

**Intent**

Agent 12 predicts appeal success probability using a LightGBM model trained on
synthetic outcome labels.  The numeric probability and top feature contributions
are then passed to Granite to produce a one-paragraph plain-language explanation,
all returned as `SuccessEstimate`.

**Expected Outcomes**

- `evals/train_scorer.py` script trains a LightGBM classifier, reports AUC, and serialises the model to `backend/models/success_scorer.pkl`.
- `backend/agents/success_scorer.py` loads the pkl file at import time, runs `predict_proba`, extracts top-3 SHAP-like feature importances, calls Granite for the explanation, and returns `AgentResult[SuccessEstimate]`.
- `SuccessScorerInput` composite defined in `app/models/appeal.py` (fields: `category: DenialCategory`, `billed_amount: float`, `state: str`, `plan_type: str`, `code_issues_count: int`, `expedited_eligible: bool`).
- Training script produces AUC printed to stdout (target ≥ 0.75).
- Unit test: load a tiny fixture model (or mock joblib.load) and assert returned probability is in `[0, 1]` and `explanation` is a non-empty string.

**Todo List**

1. Add `SuccessScorerInput` to `backend/app/models/appeal.py` and export it.
2. Create `evals/train_scorer.py`:
   - Load synthetic outcomes CSV from `data/outcomes.csv` (generated by `data/generate.py` — confirm field names match the 250-case set).
   - Feature-engineer the 6 input fields into a numeric matrix; one-hot encode `category`, `state`, `plan_type`.
   - Train LightGBM binary classifier with `random_state=42`; report train/val AUC.
   - Save model as `backend/models/success_scorer.pkl` using `joblib.dump`.
   - Document the rule-based outcome simulator in a docstring so users understand model limits (SPEC §9).
3. Create `backend/agents/success_scorer.py`:
   - `SuccessScorer`, `name = "success_scorer"`.
   - Load pkl at module import with `joblib.load`; fail gracefully with a logged warning and fall back to `probability=0.5` if file is absent (CI can run without the trained model).
   - `@trace(prompt_version="v1")` on `run(input: SuccessScorerInput, ctx)`.
   - Predict probability; extract top-3 feature-name/contribution pairs from `booster.feature_importances_` (or use `shap` if available).
   - Render `prompts/success_scorer/v1.md` with the numeric results and call `client.generate(model_tier="routing")` to produce `explanation`.
   - Return `AgentResult[SuccessEstimate]`.
4. Create `prompts/success_scorer/v1.md` — brief prompt asking Granite to explain in one paragraph why a claim has the given probability, referencing the top factors by name.
5. Unit test with joblib mock.

**Relevant Context**

- `backend/app/models/appeal.py` — `SuccessEstimate(probability, top_factors, explanation)` already defined.
- `data/generate.py` for synthetic outcome column names.
- `backend/models/` directory does not yet exist; create it with a `.gitkeep` and add the pkl to `.gitignore`.
- `cases.success_prob` column (Float) in `db/models.py` — the pipeline runner writes `SuccessEstimate.probability` here after Agent 12 returns.

---

### Sub-Task 6 — Implement Agent 13: Follow-up Tracker

**Status:** [ ] pending

**Intent**

Agent 13 reads the deadlines from `AppealPlan` and schedules two or three
`Reminder` rows in Postgres: one for the internal appeal deadline, one for the
external review deadline (if available), and optionally an escalation reminder.
The agent itself is a thin orchestration wrapper — the actual DB write reuses the
existing `case_schedule_followup` logic.

**Expected Outcomes**

- `backend/agents/followup_tracker.py` exists with `FollowupTracker`, `name = "followup_tracker"`.
- `run()` accepts `AppealPlan` and uses `AsyncSessionFactory` directly (no Granite call needed; no model tier).
- Creates `Reminder` rows for internal and (if `external_review_available`) external deadlines.
- Decorated with `@trace(prompt_version="v1")` for consistency (tokens_in/out will be 0).
- Unit test: mock DB session; assert the correct number of `Reminder` objects are added.

**Todo List**

1. Create `backend/agents/followup_tracker.py`:
   - `FollowupTracker`, `name = "followup_tracker"`.
   - `@trace(prompt_version="v1")`.
   - `run(input: AppealPlan, ctx: CaseContext) -> AgentResult[list[Reminder]]` — iterate over deadline fields; create `Reminder` rows via `AsyncSessionFactory`.
   - Return `AgentResult` with `tokens_in=0, tokens_out=0`.
2. Unit test with mocked `AsyncSessionFactory`; assert at least one reminder for `internal_appeal_deadline`; assert two reminders when `external_review_available=True`.

**Relevant Context**

- `db/models.py` — `Reminder(case_id, due_at, kind, sent)` ORM model.
- `app/tools/__init__.py` — `case_schedule_followup` already inserts a `Reminder`; Agent 13 follows the same DB pattern directly.
- Agent 13 is listed in SPEC §20 as a "cut-able to simple version" agent — keep it minimal.

---

### Sub-Task 7 — Idempotent cache helper

**Status:** [ ] pending

**Intent**

SPEC §16 requires that re-running the pipeline on the same inputs hits a cache
rather than re-invoking the LLM.  The `uq_agent_runs_idempotency` unique index
already exists; this sub-task adds a `get_cached_result` helper that queries
`agent_runs` by `(case_id, agent, input_hash)` and returns the stored output JSON
if found, so callers can short-circuit before calling `agent.run()`.

**Expected Outcomes**

- `backend/services/cache.py` exports `get_cached_result(case_id, agent_name, input_hash) -> dict | None`.
- `pipeline/runner.py` and the new drafting stage runner both call this helper before each agent invocation.
- Unit test asserts that a hit returns stored output and a miss returns `None`.

**Todo List**

1. Create `backend/services/cache.py`:
   - `async def get_cached_result(case_id: str, agent_name: str, input_hash: str) -> dict | None` — SELECT `output` FROM `agent_runs` WHERE `case_id=`, `agent=`, `input_hash=`; return `output` if found, else `None`.
2. The `_hash()` function in `services/tracing.py` already computes `input_hash`; expose it as `from services.tracing import _hash` for use by callers, or move it to `services/cache.py` and import it in `tracing.py`.
3. Document that the pipeline runner calls `get_cached_result` and skips the agent if a hit is found — avoiding duplicate DB inserts that would hit the unique constraint.
4. Unit test: mock `AsyncSessionFactory`; assert hit/miss behaviour.

**Relevant Context**

- `backend/db/migrations/versions/0001_initial.py` lines 100–105 — `uq_agent_runs_idempotency` unique index on `(case_id, agent, input_hash)`.
- `backend/services/tracing.py` — `_hash()` on line 42–45.
- `backend/db/models.py` — `AgentRun.output` (JSONB).

---

### Sub-Task 8 — DRAFTING stage runner (`pipeline/drafting.py`)

**Status:** [ ] pending

**Intent**

Mirrors `pipeline/runner.py` for the ANALYZING stage.  The drafting stage runs
Agent 9 (sequential), then the Writer ⇄ Guardian loop for Agents 10/11 (max
2 rewrites), then Agent 12.  Agent 13 is called after pipeline completion.
Encapsulating this in a dedicated module keeps `_run_pipeline_background` from
bloating.

**Expected Outcomes**

- `backend/pipeline/drafting.py` exports `run_drafting_stage(analysis, claim, ctx, on_status)` returning a `DraftingResult` dataclass.
- Writer/Guardian loop is capped at 2 rewrites; if loop exits without `passed=True`, raises a sentinel exception `NeedsHumanReview` caught by the pipeline background runner.
- Each draft version is persisted to the `drafts` table via `AsyncSessionFactory`.
- `SuccessEstimate.probability` is returned in `DraftingResult` for the caller to write to `cases.success_prob`.

**Todo List**

1. Define `DraftingResult` dataclass: fields `checklist`, `final_draft`, `grounding_report`, `success_estimate`, `rewrite_count`.
2. Define `NeedsHumanReview(Exception)` sentinel in `pipeline/drafting.py`.
3. Define `StatusCallback = Callable[[CaseStatus, dict], Awaitable[None]]` type alias in `pipeline/drafting.py`.
4. Implement `run_drafting_stage(analysis, claim, ctx, on_status: StatusCallback)`:
   - **Step 1:** Call `get_cached_result` for Agent 9; if miss, call `_evidence_builder.run(...)`.
   - **Step 2:** Loop (max 3 iterations = 1 initial + 2 rewrites), index `n` starting at 0:
     - `await on_status(CaseStatus.DRAFTING, {"iteration": n})` — signals writer pass starting.
     - `get_cached_result` for Agent 10; if miss, call `_appeal_writer.run(input_or_retry, ctx)`.
     - Persist `Draft` row (version = `n + 1`).
     - `await on_status(CaseStatus.VERIFYING, {"iteration": n})` — signals guardian check starting.
     - `get_cached_result` for Agent 11; if miss, call `_groundedness_checker.run(checker_input, ctx)`.
     - If `grounding_report.passed`: break.
     - If `n == MAX_REWRITES`: raise `NeedsHumanReview`.
     - Build `WriterRetryInput` from flagged spans and increment `n`.
   - **Step 3:** Call `get_cached_result` for Agent 12; if miss, call `_success_scorer.run(scorer_input, ctx)`.
   - Return `DraftingResult`.
5. Module-level singletons: `_evidence_builder`, `_appeal_writer`, `_groundedness_checker`, `_success_scorer`.
6. `MAX_REWRITES = 2` constant (matches SPEC §5.2).
7. Unit/integration test: pass a `fake_callback` recording `(status, detail["iteration"])` calls; assert sequence `[(DRAFTING, 0), (VERIFYING, 0)]` on a clean pass; assert `[(DRAFTING, 0), (VERIFYING, 0), (DRAFTING, 1), (VERIFYING, 1)]` on one rewrite; assert `NeedsHumanReview` raised when Guardian always returns `passed=False`.

**Relevant Context**

- `backend/pipeline/runner.py` — `run_analysis_stage` as the structural template.
- `backend/db/models.py` — `Draft` ORM model (fields: `case_id`, `version`, `body`, `grounding_report`).
- `backend/services/cache.py` — `get_cached_result` from sub-task 7.
- SPEC §5.2: "max 2 rewrite iterations; if still failing, mark case `NEEDS_HUMAN_REVIEW`".

---

### Sub-Task 9 — Wire DRAFTING stage into `_run_pipeline_background` and complete stubs

**Status:** [ ] pending

**Intent**

Extend `app/tools/__init__.py` to advance the state machine through
`ANALYZING → DRAFTING → VERIFYING → AWAITING_APPROVAL` (or `NEEDS_HUMAN_REVIEW`),
write `success_prob` to the `Case` row, call Agent 13 (FollowupTracker), and
wire the `case_get_draft` and `case_approve` stubs with real logic.

**Expected Outcomes**

- `_run_pipeline_background` advances through all five status values: `EXTRACTING → ANALYZING → DRAFTING → VERIFYING → AWAITING_APPROVAL` (or `NEEDS_HUMAN_REVIEW`).
- `case_get_draft` returns the latest draft body and `grounding_passed` from the most recent `Draft` row.
- `case_approve` sets the latest draft's `approved_by`/`approved_at` and advances status to `READY_TO_SEND`.
- Calling the pipeline twice on the same `(case_id, denial_text)` does not re-invoke any agent (cache hits).
- End-to-end smoke test: inject a synthetic case through the pipeline background function with all agents mocked; assert final status is `AWAITING_APPROVAL`.

**Todo List**

1. In `_run_pipeline_background`, after `run_analysis_stage` succeeds:
   - Define `async def _on_status(status: CaseStatus, detail: dict) -> None` as a closure that calls `_set_status(status)` and emits the SSE progress event (§7.1 `/api/cases/{id}/events`) with `{"agent_stage": status, **detail}` payload (SSE full wiring is Day 6; placeholder `logger.info` is sufficient here).
   - `result = await run_drafting_stage(analysis_result, redacted_claim, ctx, on_status=_on_status)` — wrap in `try/except NeedsHumanReview`.
   - The first `on_status(DRAFTING, ...)` call inside the stage handles the `ANALYZING → DRAFTING` transition; no manual `_set_status(DRAFTING)` needed here.
   - On `NeedsHumanReview`: `await _set_status(CaseStatus.NEEDS_HUMAN_REVIEW)` and return.
   - On success: write `result.success_estimate.probability` to `cases.success_prob`; `await _set_status(CaseStatus.AWAITING_APPROVAL)`.
   - Call `_followup_tracker.run(analysis_result.appeal_plan.output, ctx)` after status update.
2. Wire `case_get_draft`: query `drafts` table for the latest version by `case_id`; return `body` and `grounding_report.passed`.
3. Wire `case_approve`: update latest `Draft` with `approved_by`, `approved_at = now()`; set `cases.status = READY_TO_SEND`.
4. Add `FollowupTracker` singleton to `_run_pipeline_background` local imports.
5. Smoke test in `tests/test_pipeline_e2e.py`: mock all 13 agents; assert `case.status == AWAITING_APPROVAL`.

**Relevant Context**

- `backend/app/tools/__init__.py` lines 130–193 — `_run_pipeline_background` current implementation.
- `backend/pipeline/drafting.py` — `run_drafting_stage`, `NeedsHumanReview` from sub-task 8.
- `backend/db/models.py` — `Case.success_prob`, `Draft.approved_by`, `Draft.approved_at`.
- State machine transitions — driven by `on_status` callback from `run_drafting_stage`:
  - `ANALYZING → DRAFTING` (iteration 0, before Writer — first `on_status(DRAFTING, {"iteration": 0})` call)
  - `DRAFTING → VERIFYING` (iteration 0, before Guardian — `on_status(VERIFYING, {"iteration": 0})`)
  - `VERIFYING → DRAFTING` (rewrite: `on_status(DRAFTING, {"iteration": 1})`)
  - `VERIFYING → NEEDS_HUMAN_REVIEW` (after MAX_REWRITES exceeded — set directly in `_run_pipeline_background`)
  - `VERIFYING → AWAITING_APPROVAL` (Guardian passes — set directly in `_run_pipeline_background`)
- `on_status` signature: `Callable[[CaseStatus, dict], Awaitable[None]]`

---

### Sub-Task 10 — Eval harness entries for Agents 10–12

**Status:** [ ] pending

**Intent**

SPEC §10 defines three eval metrics that must be measured for Day-5 agents:
sentence groundedness ≥ 0.95 (Agents 10+11), letter quality ≥ 4.0 (Agent 10),
and Success Scorer AUC ≥ 0.75 (Agent 12).  These need eval runner entries so
CI can report them.

**Expected Outcomes**

- `evals/eval_writer_guardian.py` runs the Writer/Guardian loop on a 5-case smoke subset and reports mean `groundedness_score` and mean LLM-judge letter quality score.
- `evals/eval_scorer.py` loads the held-out 50 cases, runs `SuccessScorer`, and reports AUC (reuses the pkl from sub-task 5).
- Both scripts exit non-zero if targets are not met (for CI gate).
- Scores are printed in the same format as existing eval scripts so the `/api/evals/latest` endpoint can aggregate them.

**Todo List**

1. Create `evals/eval_writer_guardian.py`:
   - Load 5-case smoke subset from `data/`.
   - For each case: run Agent 10 → Agent 11 loop (real agents with mocked watsonx using stored responses, or against real watsonx in nightly mode).
   - Compute mean `groundedness_score`; compute letter quality via an LLM-judge prompt (a simple Granite call scoring 1–5 on a rubric in `prompts/evals/letter_quality_judge.md`).
   - Print results; assert mean groundedness ≥ 0.95 and mean quality ≥ 4.0.
2. Create `evals/eval_scorer.py`:
   - Load `data/outcomes_holdout.csv` (50 cases).
   - Run `SuccessScorer` on each (no LLM call — scorer is deterministic).
   - Compute AUC with `sklearn.metrics.roc_auc_score`; assert ≥ 0.75.
3. Register both scripts in `Makefile` under `make eval-day5`.

**Relevant Context**

- `evals/` directory — existing eval structure.
- SPEC §10 metrics table (lines 307–319).
- `prompts/evals/` — create this directory for the judge prompt.

---

## Implementation Order

Sub-tasks are designed to be executed sequentially in the order listed because
each depends on the outputs of the previous one:

1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10

Sub-tasks 2–6 (the five agents) may be partially parallelised during implementation
since they share no internal dependencies, but sub-tasks 7 and 8 must follow all
agents, and sub-task 9 must follow sub-task 8.

---

## Key Constraints & Thresholds

| Constraint | Value | Source |
|---|---|---|
| Max Writer rewrite iterations | 2 | SPEC §5.2 |
| Groundedness target | ≥ 0.95 | SPEC §10 |
| Letter quality target | ≥ 4.0 (1–5 rubric) | SPEC §10 |
| Success Scorer AUC target | ≥ 0.75 | SPEC §10 |
| p95 end-to-end latency target | ≤ 120 s | SPEC §10 |
| Unit-test coverage target | ≥ 70% on `agents/` + `pipeline/` | SPEC §16 |
| Model IDs | From `backend/config/models.yaml` only | SPEC §5.1 |
| Idempotency key | `(case_id, agent, input_hash)` | SPEC §16 |
| Synthetic data only | No real PHI/PII | Workspace rules |

---

## Files to Create

| File | Purpose |
|---|---|
| `backend/app/models/appeal.py` | Add `EvidenceItem`, `EvidenceChecklist`, `WriterInput`, `WriterRetryInput`, `CheckerInput`, `SuccessScorerInput` |
| `backend/agents/evidence_builder.py` | Agent 9 |
| `backend/agents/appeal_writer.py` | Agent 10 |
| `backend/agents/groundedness_checker.py` | Agent 11 |
| `backend/agents/success_scorer.py` | Agent 12 |
| `backend/agents/followup_tracker.py` | Agent 13 |
| `backend/services/cache.py` | Idempotent cache helper |
| `backend/pipeline/drafting.py` | DRAFTING stage runner + `NeedsHumanReview` |
| `evals/train_scorer.py` | LightGBM training script |
| `evals/eval_writer_guardian.py` | Groundedness + quality eval |
| `evals/eval_scorer.py` | AUC eval |
| `prompts/evidence_builder/v1.md` | Agent 9 prompt |
| `prompts/appeal_writer/v1.md` | Agent 10 prompt |
| `prompts/groundedness_checker/v1.md` | Agent 11 prompt |
| `prompts/success_scorer/v1.md` | Agent 12 Granite explanation prompt |
| `prompts/evals/letter_quality_judge.md` | LLM judge rubric |
| `backend/models/.gitkeep` | Placeholder for trained model directory |
| `tests/test_evidence_builder.py` | Agent 9 unit tests |
| `tests/test_appeal_writer.py` | Agent 10 unit tests |
| `tests/test_groundedness_checker.py` | Agent 11 unit tests |
| `tests/test_success_scorer.py` | Agent 12 unit tests |
| `tests/test_followup_tracker.py` | Agent 13 unit tests |
| `tests/test_cache.py` | Cache helper unit tests |
| `tests/test_drafting.py` | Drafting stage unit tests (loop logic) |
| `tests/test_pipeline_e2e.py` | End-to-end smoke test |

## Files to Modify

| File | Change |
|---|---|
| `backend/app/models/__init__.py` | Export new model types |
| `backend/app/tools/__init__.py` | Extend `_run_pipeline_background`; wire `case_get_draft`, `case_approve` |
| `backend/agents/__init__.py` | Export new agent classes |
| `tests/test_models.py` | Add tests for new Pydantic models |
| `Makefile` | Add `eval-day5` target |
