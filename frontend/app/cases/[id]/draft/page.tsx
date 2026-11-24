"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  getDraft,
  patchDraft,
  approveCase,
  REPLAY_MODE,
  type DraftResponse,
} from "@/lib/api";
import { Tooltip } from "@/components/ui/Tooltip";
import DraftMarkdown from "@/components/DraftMarkdown";
import { toast } from "@/components/ui/Toast";
import { cn } from "@/lib/utils";

interface PageProps {
  params: { id: string };
}

const isReplay = REPLAY_MODE;

// ── Page component ────────────────────────────────────────────────────────────

export default function DraftPage({ params }: PageProps) {
  const { id } = params;
  const router = useRouter();

  const [draft, setDraft] = useState<DraftResponse | null>(null);
  const [editedBody, setEditedBody] = useState("");
  const [isDirty, setIsDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [approving, setApproving] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadDraft = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const d = await getDraft(id);
      setDraft(d);
      setEditedBody(d.body_markdown);
      setIsDirty(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load draft");
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    loadDraft();
  }, [loadDraft]);

  async function handleSaveAndReverify() {
    if (!isDirty) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await patchDraft(id, editedBody);
      setDraft(updated);
      setEditedBody(updated.body_markdown);
      setIsDirty(false);
      toast({ title: "Draft saved and re-verified" });
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Save failed — please try again"
      );
    } finally {
      setSaving(false);
    }
  }

  async function handleApprove() {
    if (!draft?.grounding_report?.passed) return;
    setApproving(true);
    setError(null);
    try {
      await approveCase(id, "demo");
      toast({ title: "Appeal approved!", description: "Status set to READY_TO_SEND." });
      router.push(`/cases/${id}`);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Approval failed — please try again"
      );
      setApproving(false);
    }
  }

  const groundingPassed = draft?.grounding_report?.passed ?? false;
  const flaggedSpans = draft?.grounding_report?.flagged_spans ?? [];
  const citations = draft?.citations ?? {};

  if (loading) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-10 text-sm text-gray-500">
        Loading draft…
      </div>
    );
  }

  if (error && !draft) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-10">
        <div className="rounded-md bg-red-50 border border-red-200 px-3 py-2 text-sm text-red-700">
          {error}
        </div>
        <Link href={`/cases/${id}`} className="mt-4 inline-block btn-secondary">
          Back to case
        </Link>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl px-4 py-8 space-y-6">
      {/* Breadcrumb */}
      <nav className="text-xs text-gray-400 flex items-center gap-1">
        <Link href="/" className="hover:underline">Home</Link>
        <span>/</span>
        <Link href={`/cases/${id}`} className="hover:underline">Case {id.slice(0, 8)}…</Link>
        <span>/</span>
        <span className="text-gray-600 font-medium">Draft</span>
      </nav>

      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-bold text-gray-900">Appeal draft</h1>
          {draft && (
            <p className="text-xs text-gray-400 mt-0.5">
              Version {draft.version} ·{" "}
              {groundingPassed ? (
                <span className="text-green-600 font-medium">
                  Groundedness ✓
                </span>
              ) : (
                <span className="text-amber-600 font-medium">
                  Groundedness issues ({flaggedSpans.length})
                </span>
              )}
            </p>
          )}
        </div>
        {isReplay && (
          <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-500">
            Replay mode — read only
          </span>
        )}
      </div>

      {/* Grounding warning */}
      {flaggedSpans.length > 0 && (
        <div className="rounded-md bg-amber-50 border border-amber-200 px-3 py-3 text-sm text-amber-800">
          <strong>{flaggedSpans.length} sentence(s)</strong> were flagged by
          Granite Guardian as insufficiently grounded. They are highlighted in
          the draft below. Edit the text and click &ldquo;Save &amp; Re-verify&rdquo; to
          resolve.
        </div>
      )}

      {/* Rendered draft (read view) */}
      {draft && !isDirty && (
        <div className="card text-sm text-gray-800 leading-relaxed font-serif">
          <DraftMarkdown
            body={draft.body_markdown}
            citations={citations}
            flaggedSpans={flaggedSpans}
          />
        </div>
      )}

      {/* Edit area */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <label
            htmlFor="draft-editor"
            className="block text-sm font-medium text-gray-700"
          >
            Edit draft
          </label>
          {isDirty && (
            <span className="text-xs text-amber-600 font-medium">
              Unsaved changes
            </span>
          )}
        </div>
        <textarea
          id="draft-editor"
          value={editedBody}
          onChange={(e) => {
            setEditedBody(e.target.value);
            setIsDirty(e.target.value !== (draft?.body_markdown ?? ""));
          }}
          rows={18}
          disabled={isReplay || saving || approving}
          className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500 resize-y disabled:bg-gray-50 disabled:text-gray-500"
        />
      </div>

      {error && (
        <div className="rounded-md bg-red-50 border border-red-200 px-3 py-2 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Action buttons */}
      <div className="flex flex-wrap gap-3">
        <button
          onClick={handleSaveAndReverify}
          disabled={!isDirty || saving || isReplay || approving}
          className="btn-primary"
          title={isReplay ? "Replay mode — editing disabled" : undefined}
        >
          {saving ? "Verifying…" : "Save & Re-verify"}
        </button>

        <Tooltip
          content={
            isReplay
              ? "Replay mode — approval disabled"
              : !groundingPassed
              ? "Cannot approve until all groundedness issues are resolved"
              : "Approve this appeal draft"
          }
        >
          <button
            onClick={handleApprove}
            disabled={!groundingPassed || approving || isReplay || saving}
            className={cn(
              "btn-primary",
              (!groundingPassed || isReplay) &&
                "opacity-50 cursor-not-allowed"
            )}
          >
            {approving ? "Approving…" : "Approve"}
          </button>
        </Tooltip>

        <Link href={`/cases/${id}`} className="btn-secondary">
          Back to case
        </Link>

        <Link href={`/cases/${id}/trace`} className="btn-secondary">
          View trace
        </Link>
      </div>

      {/* Citation legend */}
      {Object.keys(citations).length > 0 && (
        <div className="card">
          <div className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-3">
            Sources ({Object.keys(citations).length})
          </div>
          <ul className="space-y-2">
            {Object.entries(citations).map(([key, src]) => (
              <li key={key} className="text-xs flex gap-2">
                <span className="shrink-0 rounded bg-blue-100 px-1 font-mono text-blue-700">
                  [{key}]
                </span>
                <span className="text-gray-700">
                  <span className="font-medium">{src.section}</span> —{" "}
                  {src.text.slice(0, 120)}
                  {src.text.length > 120 ? "…" : ""}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
