"use client";

import { cn } from "@/lib/utils";

const STEPS = [
  { status: "EXTRACTING", label: "Extracting documents" },
  { status: "ANALYZING", label: "Analysing claim" },
  { status: "DRAFTING", label: "Writing appeal" },
  { status: "VERIFYING", label: "Verifying groundedness" },
] as const;

type TerminalStatus = "AWAITING_APPROVAL" | "NEEDS_HUMAN_REVIEW" | "READY_TO_SEND";

interface ProgressStepperProps {
  status: string;
}

function stepIndex(status: string): number {
  if (status === "CREATED") return -1;
  const idx = STEPS.findIndex((s) => s.status === status);
  if (idx !== -1) return idx;
  // Terminal statuses are after all steps
  return STEPS.length;
}

export default function ProgressStepper({ status }: ProgressStepperProps) {
  const currentIdx = stepIndex(status);
  const isTerminal = ["AWAITING_APPROVAL", "NEEDS_HUMAN_REVIEW", "READY_TO_SEND", "CLOSED"].includes(status);
  const isFailed = status === "NEEDS_HUMAN_REVIEW";

  return (
    <div className="w-full" aria-label="Pipeline progress">
      {/* Mobile: vertical list */}
      <ol className="flex flex-col gap-3 sm:hidden">
        {STEPS.map((step, i) => {
          const done = currentIdx > i || isTerminal;
          const active = currentIdx === i && !isTerminal;
          return (
            <li key={step.status} className="flex items-center gap-3">
              <StepDot done={done} active={active} failed={false} />
              <span
                className={cn(
                  "text-sm",
                  active && "font-semibold text-blue-700",
                  done && "text-gray-500",
                  !done && !active && "text-gray-400"
                )}
              >
                {step.label}
              </span>
              {active && (
                <span className="ml-auto animate-pulse text-xs text-blue-500">
                  Running…
                </span>
              )}
            </li>
          );
        })}
        <li className="flex items-center gap-3">
          <StepDot done={isTerminal} active={false} failed={isFailed} />
          <span
            className={cn(
              "text-sm",
              isTerminal && !isFailed && "font-semibold text-green-700",
              isTerminal && isFailed && "font-semibold text-amber-700",
              !isTerminal && "text-gray-400"
            )}
          >
            {isFailed
              ? "Needs human review"
              : status === "READY_TO_SEND"
              ? "Approved ✓"
              : "Awaiting approval"}
          </span>
        </li>
      </ol>

      {/* Desktop: horizontal */}
      <ol className="hidden sm:flex items-center gap-0">
        {[...STEPS, { status: "DONE", label: isTerminal && !isFailed ? "Awaiting approval" : isFailed ? "Needs review" : "Awaiting approval" }].map(
          (step, i) => {
            const done =
              i < STEPS.length
                ? currentIdx > i || isTerminal
                : status === "AWAITING_APPROVAL" || status === "READY_TO_SEND";
            const active =
              i < STEPS.length
                ? currentIdx === i && !isTerminal
                : false;
            const last = i === STEPS.length;
            return (
              <li key={step.status} className="flex flex-1 items-center">
                <div className="flex flex-col items-center gap-1 min-w-[70px]">
                  <StepDot done={done} active={active} failed={last && isFailed} />
                  <span
                    className={cn(
                      "text-center text-xs leading-tight",
                      active && "font-semibold text-blue-700",
                      done && !active && "text-gray-500",
                      !done && !active && "text-gray-400"
                    )}
                  >
                    {step.label}
                  </span>
                </div>
                {!last && (
                  <div
                    className={cn(
                      "flex-1 h-0.5 mb-5 mx-1",
                      currentIdx > i || isTerminal
                        ? "bg-blue-400"
                        : "bg-gray-200"
                    )}
                  />
                )}
              </li>
            );
          }
        )}
      </ol>
    </div>
  );
}

function StepDot({
  done,
  active,
  failed,
}: {
  done: boolean;
  active: boolean;
  failed: boolean;
}) {
  return (
    <div
      className={cn(
        "flex h-7 w-7 shrink-0 items-center justify-center rounded-full border-2 text-xs font-bold transition-colors",
        failed && "border-amber-400 bg-amber-50 text-amber-700",
        done && !failed && "border-blue-500 bg-blue-500 text-white",
        active && "border-blue-500 bg-white text-blue-600 animate-pulse",
        !done && !active && !failed && "border-gray-300 bg-white text-gray-400"
      )}
    >
      {done && !active && !failed ? "✓" : active ? "…" : failed ? "!" : ""}
    </div>
  );
}
