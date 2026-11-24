"use client";

import { cn } from "@/lib/utils";

interface SuccessGaugeProps {
  probability: number; // 0..1
  explanation?: string;
}

export default function SuccessGauge({
  probability,
  explanation,
}: SuccessGaugeProps) {
  const pct = Math.round(probability * 100);
  const color =
    pct >= 70
      ? "text-green-600"
      : pct >= 40
      ? "text-amber-600"
      : "text-red-600";
  const barColor =
    pct >= 70 ? "bg-green-500" : pct >= 40 ? "bg-amber-500" : "bg-red-500";

  return (
    <div className="card">
      <div className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-3">
        Appeal success estimate
      </div>
      <div className="flex items-end gap-3 mb-2">
        <span className={cn("text-4xl font-bold tabular-nums", color)}>
          {pct}%
        </span>
        <span className="mb-1 text-sm text-gray-500">likelihood</span>
      </div>
      <div className="h-2 w-full rounded-full bg-gray-100 overflow-hidden">
        <div
          className={cn("h-2 rounded-full transition-all duration-700", barColor)}
          style={{ width: `${pct}%` }}
        />
      </div>
      {explanation && (
        <p className="mt-3 text-xs text-gray-500 leading-relaxed">{explanation}</p>
      )}
    </div>
  );
}
