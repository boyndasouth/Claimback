import Link from "next/link";
import { getCaseTrace, type TraceAgent } from "@/lib/api";

interface PageProps {
  params: { id: string };
}

function formatMs(ms: number): string {
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${ms}ms`;
}

function formatCost(usd: number | null): string {
  if (usd === null) return "—";
  if (usd < 0.001) return "<$0.001";
  return `$${usd.toFixed(4)}`;
}

export default async function TracePage({ params }: PageProps) {
  const { id } = params;

  let agents: TraceAgent[] = [];
  let fetchError: string | null = null;

  try {
    const trace = await getCaseTrace(id);
    agents = trace.agents;
  } catch (err) {
    fetchError = err instanceof Error ? err.message : "Failed to load trace";
  }

  const totalTokensIn = agents.reduce((s, a) => s + a.tokens_in, 0);
  const totalTokensOut = agents.reduce((s, a) => s + a.tokens_out, 0);
  const totalCost = agents.reduce(
    (s, a) => (a.cost_usd !== null ? s + a.cost_usd : s),
    0
  );
  const totalLatency = agents.reduce((s, a) => s + a.latency_ms, 0);

  return (
    <div className="mx-auto max-w-5xl px-4 py-8 space-y-6">
      {/* Breadcrumb */}
      <nav className="text-xs text-gray-400 flex items-center gap-1">
        <Link href="/" className="hover:underline">Home</Link>
        <span>/</span>
        <Link href={`/cases/${id}`} className="hover:underline">Case {id.slice(0, 8)}…</Link>
        <span>/</span>
        <span className="text-gray-600 font-medium">Trace</span>
      </nav>

      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-xl font-bold text-gray-900">Agent trace timeline</h1>
        <Link href={`/cases/${id}`} className="btn-secondary text-sm">
          ← Back to case
        </Link>
      </div>

      {fetchError && (
        <div className="rounded-md bg-red-50 border border-red-200 px-3 py-2 text-sm text-red-700">
          {fetchError}
        </div>
      )}

      {agents.length === 0 && !fetchError && (
        <div className="card text-sm text-gray-500">
          No agent runs recorded yet. The pipeline may not have completed.
        </div>
      )}

      {agents.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-gray-200 shadow-sm">
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50 text-xs font-semibold uppercase tracking-wide text-gray-500">
              <tr>
                <th className="px-4 py-3 text-left">#</th>
                <th className="px-4 py-3 text-left">Agent</th>
                <th className="px-4 py-3 text-right">Tokens in</th>
                <th className="px-4 py-3 text-right">Tokens out</th>
                <th className="px-4 py-3 text-right">Latency</th>
                <th className="px-4 py-3 text-right">Cost</th>
                <th className="px-4 py-3 text-left">Trace ID</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 bg-white">
              {agents.map((agent, i) => (
                <tr key={agent.trace_id} className="hover:bg-gray-50">
                  <td className="px-4 py-3 text-gray-400 tabular-nums">
                    {i + 1}
                  </td>
                  <td className="px-4 py-3 font-medium text-gray-900">
                    {agent.agent}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-gray-700">
                    {agent.tokens_in.toLocaleString()}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-gray-700">
                    {agent.tokens_out.toLocaleString()}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-gray-700">
                    {formatMs(agent.latency_ms)}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-gray-700">
                    {formatCost(agent.cost_usd)}
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-400 font-mono truncate max-w-[140px]">
                    {agent.trace_id.slice(0, 12)}…
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot className="bg-gray-50 font-semibold text-xs">
              <tr>
                <td className="px-4 py-3 text-gray-500 uppercase tracking-wide" colSpan={2}>
                  Total
                </td>
                <td className="px-4 py-3 text-right tabular-nums text-gray-900">
                  {totalTokensIn.toLocaleString()}
                </td>
                <td className="px-4 py-3 text-right tabular-nums text-gray-900">
                  {totalTokensOut.toLocaleString()}
                </td>
                <td className="px-4 py-3 text-right tabular-nums text-gray-900">
                  {formatMs(totalLatency)}
                </td>
                <td className="px-4 py-3 text-right tabular-nums text-gray-900">
                  {formatCost(totalCost)}
                </td>
                <td />
              </tr>
            </tfoot>
          </table>
        </div>
      )}

      {/* Input hash details (expandable) */}
      {agents.length > 0 && (
        <details className="card text-xs">
          <summary className="cursor-pointer text-sm font-medium text-gray-700">
            Input hashes (idempotency keys)
          </summary>
          <ul className="mt-3 space-y-1 font-mono text-gray-500">
            {agents.map((a) => (
              <li key={a.trace_id}>
                <span className="font-semibold text-gray-700">{a.agent}</span>:{" "}
                {a.input_hash}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
