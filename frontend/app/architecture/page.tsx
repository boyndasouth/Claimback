export default function ArchitecturePage() {
  return (
    <div className="mx-auto max-w-3xl px-4 py-10 space-y-8">
      <h1 className="text-2xl font-bold text-gray-900">System architecture</h1>
      <p className="text-sm text-gray-600">
        ClaimBack is a multi-agent system built on IBM watsonx Orchestrate and
        watsonx.ai. It runs the full pipeline from document upload to an
        approved, cited appeal letter — with a human reviewing every outbound
        action.
      </p>

      {/* ASCII diagram */}
      <div>
        <h2 className="text-base font-semibold text-gray-900 mb-3">
          High-level architecture
        </h2>
        <pre className="overflow-x-auto rounded-lg bg-gray-900 text-gray-100 p-5 text-xs leading-relaxed">
{`┌──────────────────────── Vercel ────────────────────────┐
│ Next.js 14 (App Router, TS, Tailwind)                   │
│  • Case upload & dashboard   • Agent trace timeline     │
│  • Embedded Orchestrate webchat   • Eval results page   │
└───────────────┬────────────────────────────────────────┘
                │ HTTPS (REST + SSE for live progress)
┌───────────────▼──────────── Render ────────────────────┐
│ FastAPI (Python 3.11, Pydantic v2)                      │
│  • /api/* for frontend   • /tools/* for Orchestrate     │
│  • Agent runtime + state machine                        │
│  • Background worker for pipeline runs                  │
└──┬──────────────┬───────────────┬──────────────┬────────┘
   │              │               │              │
   ▼              ▼               ▼              ▼
watsonx.ai   watsonx          Postgres +     Langfuse /
(Granite,    Orchestrate      pgvector       OpenTelemetry
 Guardian,   (supervisor,     (Supabase/Neon)
 embeddings, collaborators,
 extraction) flows)`}
        </pre>
      </div>

      {/* Tech stack */}
      <div>
        <h2 className="text-base font-semibold text-gray-900 mb-4">
          Technology stack
        </h2>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {[
            {
              layer: "Frontend",
              items: [
                "Next.js 14 (App Router)",
                "TypeScript strict mode",
                "Tailwind CSS",
                "Radix UI primitives",
              ],
            },
            {
              layer: "Backend",
              items: [
                "FastAPI + Pydantic v2",
                "Python 3.11",
                "SQLAlchemy 2 + asyncpg",
                "Alembic migrations",
              ],
            },
            {
              layer: "AI platform",
              items: [
                "IBM watsonx.ai",
                "Granite 3 (routing + writing)",
                "Granite Guardian (groundedness)",
                "Granite embeddings (768-dim)",
              ],
            },
            {
              layer: "Orchestration",
              items: [
                "IBM watsonx Orchestrate ADK",
                "Supervisor + collaborator agents",
                "Approval flow",
                "OpenAPI tool import",
              ],
            },
            {
              layer: "Data & search",
              items: [
                "PostgreSQL + pgvector",
                "Hybrid BM25 + vector search",
                "Synthetic data (250 cases)",
                "Encrypted PII vault",
              ],
            },
            {
              layer: "Quality & ops",
              items: [
                "Langfuse / OTel tracing",
                "Ruff + mypy + tsc strict",
                "pytest ≥70% coverage",
                "CI eval smoke suite",
              ],
            },
          ].map(({ layer, items }) => (
            <div key={layer} className="card">
              <div className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-2">
                {layer}
              </div>
              <ul className="space-y-0.5">
                {items.map((item) => (
                  <li key={item} className="text-sm text-gray-700 flex gap-2">
                    <span className="text-blue-400">·</span>
                    {item}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </div>

      {/* 13-agent pipeline */}
      <div>
        <h2 className="text-base font-semibold text-gray-900 mb-3">
          13-agent pipeline
        </h2>
        <ol className="space-y-2">
          {[
            { n: 1, name: "Supervisor", desc: "Orchestrate native agent — routes user messages to collaborator agents" },
            { n: 2, name: "Intake", desc: "Creates a case from uploaded files or chat" },
            { n: 3, name: "Document Extractor", desc: "watsonx.ai text extraction → structured ExtractedClaim" },
            { n: 4, name: "PII Redactor", desc: "Regex + Granite NER — encrypts token map, never passes PII to downstream LLMs" },
            { n: 5, name: "Denial Classifier", desc: "Granite (routing tier) → DenialCategory + rationale" },
            { n: 6, name: "Policy Retriever", desc: "Granite embeddings + pgvector hybrid BM25 → PolicyClause[]" },
            { n: 7, name: "Code Validator", desc: "Deterministic Python rules over CPT/ICD-10 tables → CodeIssue[]" },
            { n: 8, name: "Regulation & Deadline", desc: "RAG over state rules + date math → AppealPlan with deadlines" },
            { n: 9, name: "Evidence Builder", desc: "Granite → EvidenceChecklist from policy clauses + code issues" },
            { n: 10, name: "Appeal Writer", desc: "Granite (writing tier) → AppealDraft with inline [S#] citations" },
            { n: 11, name: "Groundedness Checker", desc: "Granite Guardian → GroundingReport; triggers rewrite loop (max 2×)" },
            { n: 12, name: "Success Scorer", desc: "LightGBM + Granite explanation → SuccessEstimate probability" },
            { n: 13, name: "Follow-up Tracker", desc: "Orchestrate flow → reminders, deadline escalation" },
          ].map(({ n, name, desc }) => (
            <li key={n} className="flex gap-3">
              <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-blue-100 text-xs font-bold text-blue-700">
                {n}
              </div>
              <div>
                <span className="font-medium text-gray-900">{name}</span>
                <span className="text-gray-500"> — {desc}</span>
              </div>
            </li>
          ))}
        </ol>
      </div>

      <p className="text-xs text-gray-400">
        Built with IBM Bob · Synthetic data only · No real PHI/PII
      </p>
    </div>
  );
}
