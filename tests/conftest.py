"""pytest configuration."""
from __future__ import annotations

import pytest


# Make all async tests run automatically with asyncio — equivalent to
# asyncio_mode = "auto" in pytest.ini (required for pytest-asyncio).
def pytest_collection_modifyitems(items):  # type: ignore[no-untyped-def]
    for item in items:
        if isinstance(item, pytest.Function) and item.get_closest_marker("asyncio") is None:
            if hasattr(item.obj, "__wrapped__") or getattr(item.obj, "_is_coroutine", None) is not None:
                item.add_marker(pytest.mark.asyncio)
