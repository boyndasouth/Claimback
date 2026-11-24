"""X-API-Key authentication dependency for the /tools router (§7.2, §15).

The shared secret is stored in the ``TOOLS_API_KEY`` environment variable and
also configured as a connection credential in watsonx Orchestrate.
"""
from __future__ import annotations

import os

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str | None = Security(_API_KEY_HEADER)) -> str:
    """FastAPI dependency — raises HTTP 401 if the key is missing or wrong.

    Returns
    -------
    str
        The validated key (rarely needed by endpoints, but useful for logging).
    """
    expected = os.environ.get("TOOLS_API_KEY", "")
    if not expected:
        # If the env var is not set at all, reject all requests to avoid
        # accidentally running an open endpoint in production.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="TOOLS_API_KEY is not configured on this server",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    if api_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return api_key
