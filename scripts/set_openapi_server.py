"""Write a copy of an OpenAPI spec with its `servers` URL replaced.

Used by orchestrate-import.ps1: Windows PowerShell 5.1 writes UTF-8 with a BOM
(which the Orchestrate ADK rejects) and mangles quotes in inline `python -c`.

Usage: python scripts/set_openapi_server.py <in.json> <server_url> <out.json>
"""
from __future__ import annotations

import json
import sys


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    src, url, dst = sys.argv[1:]
    with open(src, encoding="utf-8-sig") as fh:  # tolerate a BOM on input
        spec = json.load(fh)
    spec["servers"] = [{"url": url, "description": "ClaimBack backend"}]
    with open(dst, "w", encoding="utf-8") as fh:  # no BOM on output
        json.dump(spec, fh, indent=2, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
