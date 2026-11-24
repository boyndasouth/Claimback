"""Robust extraction of a JSON value from LLM output.

Models wrap JSON in ```json fences, add prose before/after, and sometimes
include fenced blocks *inside* string values (e.g. a letter body). A
non-greedy fence regex stops at the first inner ``` and truncates the JSON.
Instead, decode from the first '{' / '[' with ``JSONDecoder.raw_decode``,
which respects string quoting and ignores trailing text.
"""
from __future__ import annotations

import json
import re

# strict=False accepts raw newlines/tabs inside strings, which models often emit.
_DECODER = json.JSONDecoder(strict=False)


def extract_json_block(text: str) -> str:
    """Return the first complete JSON object/array in *text* as a JSON string.

    The value is re-serialised, so callers can use strict ``json.loads`` or
    pydantic ``model_validate_json`` on it. Falls back to the stripped text if
    nothing decodes, so the caller's own parsing raises a useful error.
    """
    for start_char in ("{", "["):
        idx = text.find(start_char)
        while idx != -1:
            try:
                value, _ = _DECODER.raw_decode(text, idx)
                return json.dumps(value, ensure_ascii=False)
            except json.JSONDecodeError:
                idx = text.find(start_char, idx + 1)
    # Last resort: strip a single outer fence if present.
    m = re.match(r"^\s*```(?:json)?\s*([\s\S]*?)\s*```\s*$", text)
    return (m.group(1) if m else text).strip()
