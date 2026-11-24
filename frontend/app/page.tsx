"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createCase, runPipeline } from "@/lib/api";

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

export default function HomePage() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleTrySample() {
    setLoading(true);
    setError(null);
    try {
      const { case_id } = await createCase("demo");
      await runPipeline(case_id, SAMPLE_DENIAL);
      router.push(`/cases/${case_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start case");
      setLoading(false);
    }
  }

  return (
    <div className="mx-auto max-w-3xl px-4 py-12">
      {/* Hero */}
      <div className="mb-10 text-center">
        <h1 className="text-3xl font-bold tracking-tight text-gray-900 sm:text-4xl">
          Fight your denied claim
        </h1>
        <p className="mt-4 text-lg text-gray-600">
          ClaimBack turns a denial letter into a cited appeal in under two
          minutes — with every factual claim grounded in your policy.
        </p>
        <div className="mt-8 flex flex-col items-center gap-3 sm:flex-row sm:justify-center">
          <button
            onClick={handleTrySample}
            disabled={loading}
            className="btn-primary text-base px-6 py-3"
          >
            {loading ? "Starting…" : "Try a sample case"}
          </button>
          <a href="/cases/new" className="btn-secondary text-base px-6 py-3">
            Upload your own
          </a>
        </div>
        {error && (
          <p className="mt-3 text-sm text-red-600">{error}</p>
        )}
      </div>

      {/* Stats */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3 mb-12">
        {[
          { stat: "19%", label: "of in-network claims denied (2023)*" },
          { stat: "<1%", label: "of denials are ever appealed*" },
          { stat: "~40%", label: "of appeals are overturned when filed*" },
        ].map(({ stat, label }) => (
          <div key={stat} className="card text-center">
            <div className="text-3xl font-bold text-blue-600">{stat}</div>
            <div className="mt-1 text-xs text-gray-500">{label}</div>
          </div>
        ))}
      </div>
      <p className="text-center text-xs text-gray-400">
        * KFF 2023 analysis of HealthCare.gov insurers. Re-verify before citing.
      </p>

      {/* How it works */}
      <div className="mt-12">
        <h2 className="text-xl font-semibold text-gray-900 mb-6">
          How it works
        </h2>
        <ol className="space-y-4">
          {[
            {
              n: "1",
              title: "Upload your documents",
              desc: "Denial letter, Explanation of Benefits (EOB), and your insurance policy PDF.",
            },
            {
              n: "2",
              title: "AI agents analyze your case",
              desc: "13 specialised agents extract, classify, retrieve policy clauses, validate billing codes, and calculate deadlines — all in parallel.",
            },
            {
              n: "3",
              title: "Review a grounded appeal draft",
              desc: "Every sentence links to a source citation. Granite Guardian verifies groundedness before you see it.",
            },
            {
              n: "4",
              title: "Approve and send",
              desc: "You review, edit if needed, and approve. ClaimBack never submits without your explicit sign-off.",
            },
          ].map(({ n, title, desc }) => (
            <li key={n} className="flex gap-4">
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-blue-100 text-sm font-bold text-blue-700">
                {n}
              </div>
              <div>
                <div className="font-medium text-gray-900">{title}</div>
                <div className="text-sm text-gray-600 mt-0.5">{desc}</div>
              </div>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
