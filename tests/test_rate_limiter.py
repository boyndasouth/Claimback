"""Unit tests for backend/services/rate_limiter.py (Day 7)."""
from __future__ import annotations

import itertools
import pathlib
import sys
import time

_BACKEND = str(pathlib.Path(__file__).resolve().parent.parent / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from services.rate_limiter import (
    _buckets,
    check_rate_limit,
    retry_after_seconds,
)

_ip_counter = itertools.count(1)


def _fresh_ip() -> str:
    """Return a unique fake IP so tests do not share bucket state."""
    n = next(_ip_counter)
    return f"10.{n // 65536 % 256}.{n // 256 % 256}.{n % 256}"


def test_allows_calls_within_limit() -> None:
    ip = _fresh_ip()
    for _ in range(5):
        assert check_rate_limit(ip, limit=5, window_seconds=60) is True


def test_blocks_on_limit_exceeded() -> None:
    ip = _fresh_ip()
    for _ in range(3):
        check_rate_limit(ip, limit=3, window_seconds=60)
    # 4th call must be blocked
    assert check_rate_limit(ip, limit=3, window_seconds=60) is False


def test_allows_again_after_window_expires() -> None:
    ip = _fresh_ip()
    # Fill the bucket with 2 calls timestamped in the past (> window ago)
    past = time.monotonic() - 61  # 61 s ago — outside a 60 s window
    _buckets[ip].append(past)
    _buckets[ip].append(past)

    # Now make 2 fresh calls — the old entries must have been evicted
    assert check_rate_limit(ip, limit=2, window_seconds=60) is True
    assert check_rate_limit(ip, limit=2, window_seconds=60) is True
    # 3rd call (within window) should be blocked
    assert check_rate_limit(ip, limit=2, window_seconds=60) is False


def test_retry_after_returns_positive_integer() -> None:
    ip = _fresh_ip()
    # Exhaust the limit
    for _ in range(3):
        check_rate_limit(ip, limit=3, window_seconds=60)
    secs = retry_after_seconds(ip, window_seconds=60)
    assert isinstance(secs, int)
    assert secs >= 1
    assert secs <= 61


def test_retry_after_no_bucket_returns_zero() -> None:
    ip = "192.0.2.99"  # never called
    _buckets.pop(ip, None)
    assert retry_after_seconds(ip) == 0


def test_different_ips_are_independent() -> None:
    ip_a = _fresh_ip()
    ip_b = _fresh_ip()
    # Exhaust ip_a
    for _ in range(2):
        check_rate_limit(ip_a, limit=2, window_seconds=60)
    # ip_a blocked
    assert check_rate_limit(ip_a, limit=2, window_seconds=60) is False
    # ip_b unaffected
    assert check_rate_limit(ip_b, limit=2, window_seconds=60) is True
