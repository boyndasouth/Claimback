# ClaimBack project rules

- SPEC.md is the source of truth. Read the relevant section before any change.
- Build only the milestone (SPEC §18) named in the request; don't jump ahead.
- Python 3.11 + FastAPI + Pydantic v2 in /backend; Next.js 14 + TypeScript in /frontend.
- Use the data models from SPEC §6 exactly; don't invent new fields.
- Model IDs come from backend/config/models.yaml, never hard-coded.
- Synthetic data only. Never add real PHI/PII.
- Never commit secrets; use environment variables listed in SPEC §15.
- Every new agent gets a unit test and the tracing decorator (SPEC §11).
- Keep changes minimal and scoped to the request; don't refactor unrelated files.
- If the spec is ambiguous, ask instead of guessing.
