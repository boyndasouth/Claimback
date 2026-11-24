"""In-memory sliding-window rate limiter (Day 7, §12).

Provides a single function ``check_rate_limit`` that tracks call counts per IP
using a deque of monotonic timestamps.  No external dependencies — stdlib only.

Limitations (acceptable for a demo):
- State is per-process and resets on restart.
- Not safe for multi-worker deployments; use Redis or a database counter there.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Final

# ── Storage ───────────────────────────────────────────────────────────────────

# ip -> deque of monotonic timestamps of recent calls
_buckets: defaultdict[str, deque[float]] = defaultdict(deque)

# ── Public API ────────────────────────────────────────────────────────────────

_DEFAULT_LIMIT: Final[int] = 20
_DEFAULT_WINDOW: Final[int] = 3600  # 1 hour in seconds


def check_rate_limit(
    ip: str,
    limit: int = _DEFAULT_LIMIT,
    window_seconds: int = _DEFAULT_WINDOW,
) -> bool:
    """Return True if *ip* is within the rate limit, False if exceeded.

    Evicts timestamps older than *window_seconds* before checking, so the
    window is a true sliding window (not a fixed bucket).

    Parameters
    ----------
    ip:
        Client IP address string.
    limit:
        Maximum number of allowed calls within the window.
    window_seconds:
        Length of the sliding window in seconds.
    """
    now = time.monotonic()
    cutoff = now - window_seconds
    bucket = _buckets[ip]

    # Evict stale timestamps from the left of the deque
    while bucket and bucket[0] < cutoff:
        bucket.popleft()

    if len(bucket) >= limit:
        return False

    bucket.append(now)
    return True


def retry_after_seconds(
    ip: str,
    window_seconds: int = _DEFAULT_WINDOW,
) -> int:
    """Return the number of seconds until the oldest call in the window expires.

    Call this only after ``check_rate_limit`` returns False to populate the
    ``Retry-After`` response header.
    """
    bucket = _buckets.get(ip)
    if not bucket:
        return 0
    now = time.monotonic()
    oldest = bucket[0]
    remaining = int(oldest + window_seconds - now) + 1
    return max(remaining, 1)
