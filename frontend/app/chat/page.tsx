"use client";

import { useEffect, useRef, useState } from "react";
import { API_URL } from "@/lib/api";

/**
 * Embedded watsonx Orchestrate web chat.
 *
 * Orchestrate embeds via a script, not an iframe: the page sets
 * window.wxOConfiguration, then loads `${hostURL}/wxochat/wxoLoader.js?embed=true`
 * and calls wxoLoader.init(). Values come from the snippet printed by
 *   orchestrate channels webchat embed --agent-name=claimback_supervisor
 *
 * Security: when Orchestrate "Embed Security" is on, the chat needs a JWT
 * signed with our private key. That key lives only on the backend, which
 * issues short-lived tokens at POST /api/chat/token; the chat asks for a
 * fresh one via the `authTokenNeeded` event when it expires.
 */

const cfg = {
  orchestrationID: process.env.NEXT_PUBLIC_WXO_ORCHESTRATION_ID,
  hostURL: process.env.NEXT_PUBLIC_WXO_HOST_URL,
  crn: process.env.NEXT_PUBLIC_WXO_CRN,
  deploymentPlatform: process.env.NEXT_PUBLIC_WXO_DEPLOYMENT_PLATFORM,
  agentId: process.env.NEXT_PUBLIC_WXO_AGENT_ID,
  agentEnvironmentId: process.env.NEXT_PUBLIC_WXO_AGENT_ENVIRONMENT_ID,
};

const isConfigured = Boolean(cfg.orchestrationID && cfg.hostURL && cfg.agentId);

type TokenResponse = { security: boolean; token?: string | null; expires_in?: number | null };

type WxoInstance = { on: (event: string, handler: (e: { authToken?: string }) => unknown) => void };

declare global {
  interface Window {
    wxOConfiguration?: Record<string, unknown>;
    wxoLoader?: { init: () => void };
    __wxoLoaded?: boolean;
  }
}

async function fetchChatToken(): Promise<TokenResponse> {
  const res = await fetch(`${API_URL}/api/chat/token`, { method: "POST" });
  if (!res.ok) throw new Error(`Token request failed (${res.status})`);
  return res.json();
}

export default function ChatPage() {
  const containerRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // The loader may run only once per page (React dev mode runs effects twice).
    if (!isConfigured || window.__wxoLoaded) return;
    window.__wxoLoaded = true;
    let cancelled = false;

    (async () => {
      let token: string | undefined;
      let security = false;
      try {
        const t = await fetchChatToken();
        security = t.security;
        token = t.token ?? undefined;
      } catch (e) {
        // Backend unreachable: still try without a token (works only if
        // Embed Security is off), but tell the user if it then fails.
        console.warn("Chat token unavailable:", e);
      }
      if (cancelled) return;

      window.wxOConfiguration = {
        orchestrationID: cfg.orchestrationID,
        hostURL: cfg.hostURL,
        rootElementID: "wxo-chat-root",
        showLauncher: false,
        ...(cfg.crn
          ? { crn: cfg.crn, deploymentPlatform: cfg.deploymentPlatform || "ibmcloud" }
          : {}),
        ...(token ? { token } : {}),
        layout: {
          form: "custom",
          customElement: containerRef.current,
          showOrchestrateHeader: true,
        },
        header: { showAiDisclaimer: true },
        chatOptions: {
          agentId: cfg.agentId,
          ...(cfg.agentEnvironmentId ? { agentEnvironmentId: cfg.agentEnvironmentId } : {}),
          onLoad: (instance: WxoInstance) => {
            if (!security) return;
            // Orchestrate asks for a new token when the current one expires.
            instance.on("authTokenNeeded", async (event) => {
              const fresh = await fetchChatToken();
              if (fresh.token) event.authToken = fresh.token;
            });
          },
        },
      };

      const script = document.createElement("script");
      script.src = `${cfg.hostURL}/wxochat/wxoLoader.js?embed=true`;
      script.addEventListener("load", () => window.wxoLoader?.init());
      script.addEventListener("error", () =>
        setError("Could not load the Orchestrate chat script. Check NEXT_PUBLIC_WXO_HOST_URL.")
      );
      document.body.appendChild(script);
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="flex flex-col" style={{ height: "calc(100vh - 120px)" }}>
      <div className="bg-blue-50 border-b border-blue-100 px-4 py-2 text-xs text-blue-700">
        This is a demo assistant using synthetic data. Not medical or legal
        advice. The Orchestrate supervisor will never submit an appeal without
        your explicit approval.
      </div>

      {isConfigured ? (
        <>
          {error && (
            <div className="mx-4 mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
              {error}
            </div>
          )}
          <div id="wxo-chat-root" ref={containerRef} className="flex-1 w-full" />
        </>
      ) : (
        <div className="flex flex-1 items-center justify-center px-4">
          <div className="max-w-md text-center card">
            <div className="text-4xl mb-4">💬</div>
            <h2 className="text-lg font-semibold text-gray-900 mb-2">Chat not configured</h2>
            <p className="text-sm text-gray-600 mb-4">
              Run{" "}
              <code className="rounded bg-gray-100 px-1 font-mono text-xs">
                orchestrate channels webchat embed --agent-name=claimback_supervisor
              </code>{" "}
              and copy its values into the{" "}
              <code className="rounded bg-gray-100 px-1 font-mono text-xs">NEXT_PUBLIC_WXO_*</code>{" "}
              variables in <code className="rounded bg-gray-100 px-1 font-mono text-xs">frontend/.env</code>.
            </p>
            <p className="text-xs text-gray-400">See the README section “Embedded Orchestrate chat”.</p>
          </div>
        </div>
      )}
    </div>
  );
}
