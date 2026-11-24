import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";
import { ToastContainer } from "@/components/ui/Toast";
import { API_URL } from "@/lib/api";

export const metadata: Metadata = {
  title: "ClaimBack — Health Insurance Appeal Assistant",
  description:
    "Understand and appeal your denied health insurance claim. Synthetic data demo — not medical or legal advice.",
};

// Best-effort backend warm-up to reduce Render cold-start latency (SPEC §16).
// Fire-and-forget: the Promise is intentionally not awaited so it never delays
// page rendering or throws an unhandled rejection visible to users.
function warmUpBackend() {
  fetch(`${API_URL}/health`, { cache: "no-store" }).catch(() => undefined);
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // Warm up on every server render (SSR warm-up, not per-request in production)
  warmUpBackend();

  return (
    <html lang="en">
      <body className="flex min-h-screen flex-col text-sm sm:text-base">
        {/* ── Disclaimer banner (SPEC §12) ──────────────────────────────── */}
        <div className="bg-amber-50 border-b border-amber-200 px-4 py-2 text-center text-xs text-amber-800 font-medium">
          Demo with synthetic data. Not medical or legal advice.
        </div>

        {/* ── Navigation ────────────────────────────────────────────────── */}
        <header className="sticky top-0 z-40 border-b border-gray-200 bg-white">
          <div className="mx-auto flex max-w-5xl items-center gap-1 px-4 py-3 sm:gap-4">
            <Link
              href="/"
              className="mr-auto text-base font-bold text-blue-700 tracking-tight"
            >
              ClaimBack
            </Link>
            <nav className="flex items-center gap-1 sm:gap-3 overflow-x-auto">
              <NavLink href="/cases/new">New Case</NavLink>
              <NavLink href="/chat" fullReload>Chat</NavLink>
              <NavLink href="/evals">Evals</NavLink>
              <NavLink href="/architecture">Architecture</NavLink>
            </nav>
          </div>
        </header>

        {/* ── Page content ──────────────────────────────────────────────── */}
        <main className="flex-1">{children}</main>

        {/* ── Toast notifications ───────────────────────────────────────── */}
        <ToastContainer />

        {/* ── Footer ───────────────────────────────────────────────────── */}
        <footer className="border-t border-gray-100 py-4 text-center text-xs text-gray-400">
          ClaimBack · Synthetic demo · No real PHI ·{" "}
          <a
            href="https://github.com/ibm"
            className="underline hover:text-gray-600"
          >
            IBM Bob
          </a>
        </footer>
      </body>
    </html>
  );
}

function NavLink({
  href,
  children,
  fullReload = false,
}: {
  href: string;
  children: React.ReactNode;
  // The Orchestrate chat script may load only once per page, so /chat uses a
  // normal link (full page load) instead of client-side navigation.
  fullReload?: boolean;
}) {
  const Tag = fullReload ? "a" : Link;
  return (
    <Tag
      href={href}
      className="whitespace-nowrap rounded px-2 py-1 text-sm text-gray-600 hover:bg-gray-100 hover:text-gray-900 transition-colors"
    >
      {children}
    </Tag>
  );
}
