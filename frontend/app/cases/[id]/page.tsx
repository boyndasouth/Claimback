"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  getCase,
  getCaseEvents,
  getCaseSummary,
  getReplayTrace,
  REPLAY_MODE,
  type CaseSummary,
  type ReplayEvent,
} from "@/lib/api";
import ProgressStepper from "@/components/ProgressStepper";
import SuccessGauge from "@/components/SuccessGauge";

const TERMINAL_STATUSES = new Set([
  "AWAITING_APPROVAL",
  "NEEDS_HUMAN_REVIEW",
  "READY_TO_SEND",
  "CLOSED",
]);

function normaliseStatus(raw: string): string {
  return String(raw).toUpperCase();
}

interface PageProps {
  params: { id: string };
}

type SseEvent = {
  type: string;
  status?: string;
  detail?: Record<string, unknown>;
};

export default function CasePage({ params }: PageProps) {
  const { id } = params;
  const [status, setStatus] = useState("CREATED");
  const [summary, setSummary] = useState<CaseSummary | null>(null);
  const [sseError, setSseError] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  // Fetch summary whenever a stage completes
  async function refreshSummary() {
    try {
      const s = await getCaseSummary(id);
      setSummary(s);
    } catch {
      // Not fatal — summary appears progressively
    }
  }

  useEffect(() => {
    if (REPLAY_MODE) {
      // Replay mode: load fixture once and play back the events array
      let cancelled = false;
      (async () => {
        try {
          const fixture = await getReplayTrace(id);
          setSummary(fixture.summary);

          const events: ReplayEvent[] = fixture.events ?? [];
          let prevElapsed = 0;

          for (const ev of events) {
            if (cancelled) break;
            const delay = Math.max(0, ev.elapsed_ms - prevElapsed);
            await new Promise<void>((res) => setTimeout(res, delay));
            if (cancelled) break;
            prevElapsed = ev.elapsed_ms;

            if (ev.status) {
              setStatus(normaliseStatus(ev.status));
            }
          }
        } catch {
          setSseError("Could not load replay fixture.");
        }
      })();
      return () => {
        cancelled = true;
      };
    }

    // Live mode: SSE stream.
    // The backend sends statuses in lowercase ("awaiting_approval"); the UI
    // compares uppercase, so normalise every status at the edge.
    let latest = "CREATED";
    const applyStatus = (raw: string) => {
      latest = normaliseStatus(raw);
      setStatus(latest);
      refreshSummary();
      return latest;
    };

    // Show the real current state immediately (page refresh / late visit).
    getCase(id)
      .then((c) => applyStatus(c.status))
      .catch(() => {});

    const es = getCaseEvents(id);
    esRef.current = es;

    es.addEventListener("message", (event: MessageEvent) => {
      try {
        const data: SseEvent = JSON.parse(event.data as string);
        if (data.status) {
          // Close stream on terminal status
          if (TERMINAL_STATUSES.has(applyStatus(data.status))) {
            es.close();
          }
        }
      } catch {
        // malformed event — ignore
      }
    });

    es.addEventListener("error", () => {
      es.close();
      // The server closes the stream after a terminal status; that also fires
      // "error". Only report a problem if the case really isn't finished.
      if (TERMINAL_STATUSES.has(latest)) return;
      getCase(id)
        .then((c) => {
          if (!TERMINAL_STATUSES.has(applyStatus(c.status))) {
            setSseError("Lost connection to the server. Refresh to retry.");
          }
        })
        .catch(() => setSseError("Lost connection to the server. Refresh to retry."));
    });

    return () => {
      es.close();
    };
  }, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  const isTerminal = TERMINAL_STATUSES.has(status);
  const isFailed = status === "NEEDS_HUMAN_REVIEW";
  const isApproved = status === "READY_TO_SEND";

  return (
    <div className="mx-auto max-w-3xl px-4 py-8 space-y-6">
      {/* Replay mode banner */}
      {REPLAY_MODE && (
        <div className="rounded-md bg-blue-50 border border-blue-200 px-4 py-2 text-sm text-blue-800">
          <span className="font-semibold">Replay mode:</span> recorded run from
          the live system.
        </div>
      )}

      {/* Breadcrumb */}
      <nav className="text-xs text-gray-400 flex items-center gap-1">
        <Link href="/" className="hover:underline">
          Home
        </Link>
        <span>/</span>
        <span className="text-gray-600 font-medium truncate max-w-[200px]">{id}</span>
      </nav>

      <h1 className="text-xl font-bold text-gray-900">Case {id.slice(0, 8)}…</h1>

      {/* Progress stepper */}
      <div className="card">
        <div className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-4">
          Analysis progress
        </div>
        <ProgressStepper status={status} />

        {sseError && (
          <div className="mt-4 rounded-md bg-red-50 border border-red-200 px-3 py-2 text-sm text-red-700">
            {sseError}
          </div>
        )}

        {!isTerminal && !sseError && (
          <div className="mt-4 flex items-center gap-2 text-sm text-gray-500">
            <span className="inline-block h-2 w-2 rounded-full bg-blue-400 animate-pulse" />
            {REPLAY_MODE ? "Replaying pipeline…" : "Pipeline running…"}
          </div>
        )}

        {isTerminal && (
          <div className="mt-5 flex flex-col gap-2 sm:flex-row">
            {isFailed ? (
              <div className="rounded-md bg-amber-50 border border-amber-200 px-3 py-2 text-sm text-amber-800 flex-1">
                One or more appeal sentences could not be fully grounded.
                Please review and edit the draft before approving.
              </div>
            ) : isApproved ? (
              <div className="rounded-md bg-green-50 border border-green-200 px-3 py-2 text-sm text-green-800 flex-1">
                Appeal approved and ready to send.
              </div>
            ) : null}
            <Link
              href={`/cases/${id}/draft`}
              className="btn-primary self-start"
            >
              {isFailed ? "Review & Edit Draft" : "Review Draft"}
            </Link>
            <Link
              href={`/cases/${id}/trace`}
              className="btn-secondary self-start"
            >
              View trace
            </Link>
          </div>
        )}
      </div>

      {/* Explanation card */}
      {summary?.explanation && (
        <div className="card">
          <div className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-2">
            Denial explanation
          </div>
          <p className="text-sm text-gray-700 leading-relaxed">
            {summary.explanation}
          </p>
        </div>
      )}

      {/* Deadlines card */}
      {summary?.appeal_plan && (
        <div className="card">
          <div className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-3">
            Appeal deadlines
          </div>
          <dl className="grid grid-cols-1 gap-2 sm:grid-cols-2 text-sm">
            <div>
              <dt className="text-gray-500">Internal appeal deadline</dt>
              <dd className="font-semibold text-gray-900">
                {summary.appeal_plan.internal_appeal_deadline}
              </dd>
            </div>
            {summary.appeal_plan.external_review_available &&
              summary.appeal_plan.external_review_deadline && (
                <div>
                  <dt className="text-gray-500">External review deadline</dt>
                  <dd className="font-semibold text-gray-900">
                    {summary.appeal_plan.external_review_deadline}
                  </dd>
                </div>
              )}
            <div>
              <dt className="text-gray-500">Expedited eligible</dt>
              <dd className="font-semibold text-gray-900">
                {summary.appeal_plan.expedited_eligible ? "Yes" : "No"}
              </dd>
            </div>
          </dl>
        </div>
      )}

      {/* Evidence checklist */}
      {summary?.evidence_checklist && summary.evidence_checklist.items.length > 0 && (
        <div className="card">
          <div className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-3">
            Evidence checklist
          </div>
          <ul className="space-y-3">
            {summary.evidence_checklist.items.map((item) => (
              <li key={item.item_id} className="flex items-start gap-3">
                <span
                  className={`mt-0.5 inline-block h-2 w-2 shrink-0 rounded-full ${
                    item.priority === "high"
                      ? "bg-red-500"
                      : item.priority === "medium"
                      ? "bg-amber-500"
                      : "bg-gray-400"
                  }`}
                />
                <div>
                  <div className="text-sm font-medium text-gray-900">
                    {item.description}
                  </div>
                  <div className="text-xs text-gray-500 mt-0.5">
                    {item.rationale}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Success gauge */}
      {summary?.success_estimate && (
        <SuccessGauge
          probability={summary.success_estimate.probability}
          explanation={summary.success_estimate.explanation}
        />
      )}
    </div>
  );
}
