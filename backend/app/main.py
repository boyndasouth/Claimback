"""ClaimBack FastAPI application entry point."""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api import router as api_router
from app.tools import router as tools_router
from db import get_session

app = FastAPI(
    title="ClaimBack API",
    description="Multi-agent health-insurance appeal assistant (synthetic data only).",
    version="0.1.0",
)

# ── CORS ─────────────────────────────────────────────────────────────────────
_origins_raw = os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000")
_allowed_origins = [o.strip() for o in _origins_raw.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ──────────────────────────────────────────────────────────────────
app.include_router(api_router, prefix="/api")
app.include_router(tools_router, prefix="/tools")


# ── Health ───────────────────────────────────────────────────────────────────
@app.get("/health", tags=["ops"])
async def health() -> dict[str, str]:
    """Liveness + DB check.  Returns {"status": "ok", "db": "ok"} when healthy."""
    db_status = "error"
    try:
        async for session in get_session():
            await session.execute(text("SELECT 1"))
            db_status = "ok"
    except Exception:
        db_status = "error"
    return {"status": "ok", "db": db_status}
