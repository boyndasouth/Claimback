"""Print the watsonx Orchestrate web-chat embed values as frontend .env lines.

Workaround for an ADK 2.20 bug: on AWS-hosted instances the login token's
`aud` claim is a *list* of CRNs, and `orchestrate channels webchat embed`
crashes ("'list' object has no attribute 'split'"). This reuses the ADK's own
controller and only fixes the CRN lookup (picks the `crn:...sub/...` entry).

Usage (after `orchestrate env activate <env>`):
    python scripts/wxo_embed_values.py [--agent claimback_supervisor] [--env draft|live]
"""
from __future__ import annotations

import argparse
from typing import Any

from ibm_watsonx_orchestrate.cli.commands.channels.webchat.channels_webchat_controller import (
    ChannelsWebchatController,
)


class _FixedController(ChannelsWebchatController):  # type: ignore[misc]
    def extract_tenant_id_from_crn(self, crn: Any) -> str:
        if isinstance(crn, list):
            matches = [c for c in crn if isinstance(c, str) and c.startswith("crn:") and "sub/" in c]
            if not matches:
                matches = [c for c in crn if isinstance(c, str) and c.startswith("crn:")]
            if not matches:
                raise ValueError(f"No CRN found in token audience: {crn}")
            crn = matches[0]
        result: str = super().extract_tenant_id_from_crn(crn)
        return result


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--agent", default="claimback_supervisor")
    p.add_argument("--env", default="draft", choices=["draft", "live"])
    a = p.parse_args()

    c = _FixedController(agent_name=a.agent, env=a.env)
    tenant_id = c.get_tenant_id()
    host_url = c.get_host_url()
    agent_id = c.get_agent_id(a.agent)
    env_id = c.get_environment_id(a.agent, a.env) if a.env == "live" else ""

    print("# Paste into frontend/.env (public IDs, not secrets)")
    print(f"NEXT_PUBLIC_WXO_ORCHESTRATION_ID={tenant_id}")
    print(f"NEXT_PUBLIC_WXO_HOST_URL={host_url}")
    print("NEXT_PUBLIC_WXO_CRN=")
    print(f"NEXT_PUBLIC_WXO_AGENT_ID={agent_id}")
    print(f"NEXT_PUBLIC_WXO_AGENT_ENVIRONMENT_ID={env_id}")


if __name__ == "__main__":
    main()
