"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  createCase,
  runPipeline,
  listReplayCases,
  REPLAY_MODE,
} from "@/lib/api";

const SAMPLE_DENIAL = `Date: March 15, 2024
Patient: Jane Smith  DOB: 1989-04-10
Claim #: CLM-2024-00342
Service: MRI Brain without Contrast (CPT 70553)  Date of Service: March 1, 2024
Payer: BlueSky Health Plan  Plan Type: Employer

NOTICE OF CLAIM DETERMINATION — DENIED

Your claim for the above referenced service has been denied for the following reason:
Medical Necessity Not Established. The requested MRI did not meet our clinical coverage
criteria under Policy Section 4.2 (Diagnostic Imaging). Your treating physician may
submit additional clinical documentation for reconsideration within 30 days.

CARC: 50  RARC: M86
Billed Amount: $1,850.00  Covered: $0.00`;

// ── Replay-mode case picker ───────────────────────────────────────────────────

function ReplayPicker() {
  const router = useRouter();
  const [cases, setCases] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    listReplayCases()
      .then((r) => setCases(r.cases))
      .catch(() => setCases([]))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="mx-auto max-w-2xl px-4 py-10">
      {/* Replay mode banner */}
      <div className="mb-6 rounded-md bg-blue-50 border border-blue-200 px-4 py-3 text-sm text-blue-800">
        <span className="font-semibold">Replay mode:</span> recorded runs from
        the live system. No watsonx credentials required.
      </div>

      <h1 className="text-2xl font-bold text-gray-900 mb-2">
        Pick a sample case
      </h1>
      <p className="text-sm text-gray-500 mb-8">
        These are pre-recorded pipeline runs. Select one to explore the full
        analysis, appeal draft, and agent trace.
      </p>

      {loading ? (
        <div className="text-sm text-gray-400">Loading sample cases…</div>
      ) : cases.length === 0 ? (
        <div className="rounded-md bg-amber-50 border border-amber-200 px-3 py-2 text-sm text-amber-700">
          No replay fixtures found. Run{" "}
          <code className="font-mono">make record-replay</code> to generate
          them.
        </div>
      ) : (
        <ul className="divide-y divide-gray-100 rounded-lg border border-gray-200 overflow-hidden">
          {cases.map((caseId) => (
            <li key={caseId}>
              <button
                onClick={() => router.push(`/cases/${caseId}`)}
                className="w-full text-left px-4 py-3 text-sm hover:bg-gray-50 transition-colors"
              >
                <span className="font-mono text-gray-700">{caseId}</span>
                <span className="ml-3 text-xs text-gray-400">
                  → View analysis &amp; draft
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ── Live upload form ──────────────────────────────────────────────────────────

function LiveForm() {
  const router = useRouter();
  const [denialText, setDenialText] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!denialText.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const { case_id } = await createCase("demo");
      await runPipeline(case_id, denialText);
      router.push(`/cases/${case_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start analysis");
      setLoading(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl px-4 py-10">
      <h1 className="text-2xl font-bold text-gray-900 mb-2">
        Start a new appeal case
      </h1>
      <p className="text-sm text-gray-500 mb-8">
        Paste the text from your denial letter. The analysis pipeline will
        extract key information and build a cited appeal draft.
      </p>

      <form onSubmit={handleSubmit} className="space-y-5">
        <div>
          <div className="flex items-center justify-between mb-2">
            <label
              htmlFor="denial-text"
              className="block text-sm font-medium text-gray-700"
            >
              Denial letter text
            </label>
            <button
              type="button"
              onClick={() => setDenialText(SAMPLE_DENIAL)}
              className="text-xs text-blue-600 hover:underline"
            >
              Use sample denial
            </button>
          </div>
          <textarea
            id="denial-text"
            value={denialText}
            onChange={(e) => setDenialText(e.target.value)}
            rows={14}
            placeholder="Paste your denial letter text here…"
            className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500 resize-y"
            required
          />
        </div>

        {error && (
          <div className="rounded-md bg-red-50 border border-red-200 px-3 py-2 text-sm text-red-700">
            {error}
          </div>
        )}

        <div className="flex gap-3">
          <button
            type="submit"
            disabled={loading || !denialText.trim()}
            className="btn-primary"
          >
            {loading ? "Starting analysis…" : "Analyse claim"}
          </button>
          <a href="/" className="btn-secondary">
            Cancel
          </a>
        </div>
      </form>
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function NewCasePage() {
  return REPLAY_MODE ? <ReplayPicker /> : <LiveForm />;
}
