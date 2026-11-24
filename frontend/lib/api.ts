/**
 * Typed API client for all /api/* endpoints (SPEC §7.1).
 *
 * All functions read NEXT_PUBLIC_API_URL at call time so they work in both
 * server and client components without special treatment.
 *
 * When NEXT_PUBLIC_REPLAY_MODE=true the read functions (getCase, getCaseTrace,
 * getCaseSummary, getDraft) delegate to the replay fixture endpoint so the demo
 * works without watsonx credentials (SPEC §13).
 */

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export const REPLAY_MODE =
  process.env.NEXT_PUBLIC_REPLAY_MODE === "true";

// ── Shared types (mirroring §6 Pydantic models) ───────────────────────────────

export interface CaseStateResponse {
  case_id: string;
  status: string;
  category: string | null;
  success_prob: number | null;
  draft: string | null;
  draft_version: number | null;
  grounding_passed: boolean | null;
}

export interface SourceRef {
  clause_id: string;
  doc_id: string;
  section: string;
  text: string;
}

export interface FlaggedSpan {
  span: string;
  reason: string;
}

export interface GroundingReport {
  passed: boolean;
  flagged_spans: FlaggedSpan[];
  groundedness_score: number;
}

export interface DraftResponse {
  case_id: string;
  version: number;
  body_markdown: string;
  citations: Record<string, SourceRef>;
  grounding_report: GroundingReport | null;
}

export interface AppealPlan {
  internal_appeal_deadline: string;
  external_review_available: boolean;
  external_review_deadline: string | null;
  expedited_eligible: boolean;
  rule_sources: SourceRef[];
}

export interface EvidenceItem {
  item_id: string;
  description: string;
  rationale: string;
  priority: "high" | "medium" | "low";
}

export interface EvidenceChecklist {
  items: EvidenceItem[];
}

export interface CodeIssue {
  issue_type: string;
  severity: "low" | "medium" | "high";
  cpt_code: string | null;
  icd10_code: string | null;
  message: string;
  source: SourceRef;
}

export interface SuccessEstimate {
  probability: number;
  top_factors: [string, number][];
  explanation: string;
}

export interface CaseSummary {
  explanation: string | null;
  appeal_plan: AppealPlan | null;
  evidence_checklist: EvidenceChecklist | null;
  code_issues: CodeIssue[];
  success_estimate: SuccessEstimate | null;
}

export interface TraceAgent {
  agent: string;
  input_hash: string;
  tokens_in: number;
  tokens_out: number;
  latency_ms: number;
  cost_usd: number | null;
  trace_id: string;
}

export interface TraceResponse {
  case_id: string;
  agents: TraceAgent[];
}

// ── Replay fixture types (§13) ────────────────────────────────────────────────

export interface ReplayEvent {
  elapsed_ms: number;
  type: string;
  status?: string;
  success_prob?: number | null;
  flagged_count?: number;
}

export interface ReplayFixture {
  case: CaseStateResponse;
  trace: TraceResponse;
  summary: CaseSummary;
  draft: DraftResponse | null;
  events: ReplayEvent[];
}

export interface ReplayListResponse {
  cases: string[];
}

export interface EvalsMetrics {
  [key: string]: number;
}

export interface EvalsResults {
  timestamp: string;
  n_cases: number;
  n_ok?: number;
  metrics: EvalsMetrics;
  targets: EvalsMetrics;
  passed: Record<string, boolean>;
  case_results?: unknown[];
}

export interface EvalsLatestResponse {
  file?: string;
  results?: EvalsResults;
  error?: string;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

async function apiFetch<T>(
  path: string,
  init?: RequestInit,
  options?: { revalidate?: number | false }
): Promise<T> {
  const url = `${API_URL}${path}`;
  const res = await fetch(url, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    ...(options?.revalidate !== undefined
      ? { next: { revalidate: options.revalidate } }
      : {}),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API ${path} → ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

// ── Case endpoints ────────────────────────────────────────────────────────────

export async function createCase(
  userId: string,
  notes = ""
): Promise<{ case_id: string; status: string }> {
  return apiFetch("/api/cases", {
    method: "POST",
    body: JSON.stringify({ user_id: userId, notes }),
  });
}

export async function runPipeline(
  caseId: string,
  denialText = ""
): Promise<{ status: string; case_id: string }> {
  return apiFetch(`/api/cases/${caseId}/run`, {
    method: "POST",
    body: JSON.stringify({ denial_text: denialText }),
  });
}

/** Returns an EventSource for the SSE stream — only usable in browser. */
export function getCaseEvents(caseId: string): EventSource {
  return new EventSource(`${API_URL}/api/cases/${caseId}/events`);
}

export async function getCase(caseId: string): Promise<CaseStateResponse> {
  if (REPLAY_MODE) {
    const fixture = await getReplayTrace(caseId);
    return fixture.case;
  }
  return apiFetch(`/api/cases/${caseId}`);
}

export async function getCaseSummary(caseId: string): Promise<CaseSummary> {
  if (REPLAY_MODE) {
    const fixture = await getReplayTrace(caseId);
    return fixture.summary;
  }
  return apiFetch(`/api/cases/${caseId}/summary`);
}

export async function getDraft(caseId: string): Promise<DraftResponse> {
  if (REPLAY_MODE) {
    const fixture = await getReplayTrace(caseId);
    if (!fixture.draft) throw new Error("No draft in replay fixture");
    return fixture.draft;
  }
  return apiFetch(`/api/cases/${caseId}/draft`);
}

export async function patchDraft(
  caseId: string,
  bodyMarkdown: string
): Promise<DraftResponse> {
  return apiFetch(`/api/cases/${caseId}/draft`, {
    method: "PATCH",
    body: JSON.stringify({ body_markdown: bodyMarkdown }),
  });
}

export async function approveCase(
  caseId: string,
  approvedBy: string
): Promise<{ status: string; case_id: string }> {
  return apiFetch(`/api/cases/${caseId}/approve`, {
    method: "POST",
    body: JSON.stringify({ approved_by: approvedBy }),
  });
}

export async function getCaseTrace(caseId: string): Promise<TraceResponse> {
  if (REPLAY_MODE) {
    const fixture = await getReplayTrace(caseId);
    return fixture.trace;
  }
  return apiFetch(`/api/cases/${caseId}/trace`);
}

export async function getEvalsLatest(): Promise<EvalsLatestResponse> {
  return apiFetch(`/api/evals/latest`, undefined, { revalidate: 60 });
}

// ── Replay endpoints (§13, §7.1) ──────────────────────────────────────────────

export async function getReplayTrace(caseId: string): Promise<ReplayFixture> {
  return apiFetch(`/api/replay/${caseId}`);
}

export async function listReplayCases(): Promise<ReplayListResponse> {
  return apiFetch(`/api/replay/`);
}
