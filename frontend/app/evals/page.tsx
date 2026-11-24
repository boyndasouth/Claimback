import { getEvalsLatest, type EvalsLatestResponse } from "@/lib/api";

// Revalidate every 60 seconds (server component)
export const revalidate = 60;

function PassBadge({ passed }: { passed: boolean }) {
  return passed ? (
    <span className="inline-flex items-center gap-1 rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-700">
      ✓ Pass
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 rounded-full bg-red-100 px-2 py-0.5 text-xs font-medium text-red-700">
      ✗ Fail
    </span>
  );
}

function formatMetricName(key: string): string {
  return key
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .replace("F1", "F1")
    .replace("At", "@")
    .replace("Auc", "AUC");
}

function formatValue(key: string, value: number | null | undefined): string {
  if (value === null || value === undefined) return "not measured";
  if (key === "pii_leakage_rate") return value.toFixed(3);
  if (key.includes("auc")) return value.toFixed(3);
  if (value <= 1 && value >= 0 && !key.includes("quality")) {
    return (value * 100).toFixed(1) + "%";
  }
  return value.toFixed(2);
}

export default async function EvalsPage() {
  let data: EvalsLatestResponse;
  try {
    data = await getEvalsLatest();
  } catch {
    data = { error: "Could not reach the backend" };
  }

  const results = data.results;

  if (data.error || !results) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-10">
        <h1 className="text-2xl font-bold text-gray-900 mb-6">Eval results</h1>
        <div className="card text-sm text-gray-500">
          <p className="font-medium text-gray-700 mb-1">No results available</p>
          <p className="text-gray-500">
            {data.error ??
              "Run the eval suite to generate results: make eval-day5"}
          </p>
        </div>
      </div>
    );
  }

  const { timestamp, n_cases, metrics, targets, passed } = results;
  const allPassed = Object.values(passed).every(Boolean);

  return (
    <div className="mx-auto max-w-3xl px-4 py-10 space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Eval results</h1>
        <div className="mt-1 flex flex-wrap items-center gap-3 text-sm text-gray-500">
          <span>Run: {new Date(timestamp).toLocaleString()}</span>
          <span>·</span>
          <span>{n_cases} cases</span>
          <span>·</span>
          {allPassed ? (
            <span className="font-semibold text-green-600">All metrics passing</span>
          ) : (
            <span className="font-semibold text-red-600">
              {Object.values(passed).filter((v) => !v).length} metric(s) failing
            </span>
          )}
          {data.file && (
            <>
              <span>·</span>
              <span className="font-mono text-xs">{data.file}</span>
            </>
          )}
        </div>
      </div>

      {/* Metrics table */}
      <div className="overflow-x-auto rounded-lg border border-gray-200 shadow-sm">
        <table className="min-w-full text-sm">
          <thead className="bg-gray-50 text-xs font-semibold uppercase tracking-wide text-gray-500">
            <tr>
              <th className="px-4 py-3 text-left">Metric</th>
              <th className="px-4 py-3 text-right">Measured</th>
              <th className="px-4 py-3 text-right">Target</th>
              <th className="px-4 py-3 text-center">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 bg-white">
            {Object.entries(metrics).map(([key, value]) => (
              <tr key={key} className="hover:bg-gray-50">
                <td className="px-4 py-3 font-medium text-gray-900">
                  {formatMetricName(key)}
                </td>
                <td className="px-4 py-3 text-right tabular-nums text-gray-700">
                  {formatValue(key, value)}
                </td>
                <td className="px-4 py-3 text-right tabular-nums text-gray-500">
                  {targets[key] !== undefined
                    ? formatValue(key, targets[key])
                    : "—"}
                </td>
                <td className="px-4 py-3 text-center">
                  {passed[key] !== undefined ? (
                    <PassBadge passed={passed[key]} />
                  ) : (
                    <span className="text-gray-400 text-xs">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="text-xs text-gray-400">
        Results from <code>{data.file}</code>. Run{" "}
        <code>python -m evals.run --subset 30</code> to refresh.
      </p>
    </div>
  );
}
