"""Tests for the /tools router — X-API-Key auth and endpoint shapes (§7.2).

Day-4 additions:
- case_create/case_status/case_get_explanation wired to a real (in-memory SQLite)
  database via a patched get_session dependency.
- case_run_pipeline tested with a mocked background task.
- case_schedule_followup tested for reminder insertion and ISO-8601 validation.
"""
from __future__ import annotations

import uuid
from typing import AsyncGenerator
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import StaticPool


# ── Minimal test-only SQLite schema ──────────────────────────────────────────
# The full ORM uses Postgres-specific types (pgvector, tsvector, JSONB) that
# SQLite cannot create. We define just the tables the tools router touches:
# `cases` and `reminders`. We import the ORM classes for type hints but create
# the tables from this lightweight metadata.

class _TestBase(DeclarativeBase):
    pass


class _TestCase(_TestBase):
    __tablename__ = "cases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(256), nullable=False)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    success_prob: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Mirrors the production `server_default=func.now()` column.
    # SQLite does not execute server_default expressions; allow NULL so the
    # INSERT (which omits this column) succeeds without raising NOT NULL.
    created_at: Mapped[str | None] = mapped_column(Text, nullable=True)


class _TestReminder(_TestBase):
    __tablename__ = "reminders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    due_at: Mapped[str] = mapped_column(Text, nullable=False)   # stored as ISO string
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class _TestAgentRun(_TestBase):
    """Minimal agent_runs table required by services.case_summary.get_case_summary."""

    __tablename__ = "agent_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    agent: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # JSON stored as text in SQLite
    output: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")


# ── Test database setup ───────────────────────────────────────────────────────

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture()
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Per-test in-memory SQLite database with minimal tables."""
    engine = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(_TestBase.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture()
def tools_app(monkeypatch, db_session):
    """Return the FastAPI app with TOOLS_API_KEY set and DB overridden."""
    monkeypatch.setenv("TOOLS_API_KEY", "supersecret")
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)

    from app.main import app
    from db import get_session

    async def override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = override_get_session
    yield app
    app.dependency_overrides.clear()


@pytest.fixture()
async def client(tools_app):
    async with AsyncClient(
        transport=ASGITransport(app=tools_app), base_url="http://test"
    ) as ac:
        yield ac


_KEY = {"X-API-Key": "supersecret"}


# ── Auth tests ────────────────────────────────────────────────────────────────


class TestApiKeyAuth:
    async def test_missing_key_returns_401(self, client):
        resp = await client.post("/tools/case_create", json={"user_id": "u1"})
        assert resp.status_code == 401

    async def test_wrong_key_returns_401(self, client):
        resp = await client.post(
            "/tools/case_create",
            json={"user_id": "u1"},
            headers={"X-API-Key": "wrong-key"},
        )
        assert resp.status_code == 401

    async def test_correct_key_returns_200(self, client):
        resp = await client.post(
            "/tools/case_create",
            json={"user_id": "u1"},
            headers=_KEY,
        )
        assert resp.status_code == 200

    async def test_all_endpoints_require_key(self, client):
        """Every tools endpoint (except /openapi.json) must reject missing keys."""
        endpoints: list[tuple[str, str, dict | None]] = [
            ("POST", "/tools/case_create", {"user_id": "u1"}),
            ("GET",  "/tools/case_status", None),
            ("POST", "/tools/case_run_pipeline", {"case_id": "c1"}),
            ("GET",  "/tools/case_get_explanation", None),
            ("GET",  "/tools/case_get_draft", None),
            ("POST", "/tools/case_approve", {"case_id": "c1", "approved_by": "user"}),
            ("POST", "/tools/case_schedule_followup", {"case_id": "c1", "due_at": "2025-01-01T00:00:00", "kind": "internal_appeal"}),
        ]
        for method, path, body in endpoints:
            if method == "GET":
                resp = await client.get(path, params={"case_id": "c1"})
            else:
                resp = await client.post(path, json=body)
            assert resp.status_code == 401, f"{method} {path} should return 401 without key"


# ── case_create ───────────────────────────────────────────────────────────────


class TestCaseCreate:
    async def test_returns_case_id_and_created_status(self, client):
        resp = await client.post(
            "/tools/case_create",
            json={"user_id": "u1", "notes": "MRI denial"},
            headers=_KEY,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "case_id" in body
        assert body["status"] == "created"
        # case_id must be a valid UUID
        uuid.UUID(body["case_id"])

    async def test_two_creates_return_different_ids(self, client):
        r1 = await client.post("/tools/case_create", json={"user_id": "u1"}, headers=_KEY)
        r2 = await client.post("/tools/case_create", json={"user_id": "u1"}, headers=_KEY)
        assert r1.json()["case_id"] != r2.json()["case_id"]


# ── case_status ───────────────────────────────────────────────────────────────


class TestCaseStatus:
    async def test_returns_status_for_existing_case(self, client):
        # First create a case
        cr = await client.post("/tools/case_create", json={"user_id": "u2"}, headers=_KEY)
        case_id = cr.json()["case_id"]

        resp = await client.get(
            "/tools/case_status",
            params={"case_id": case_id},
            headers=_KEY,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["case_id"] == case_id
        assert body["status"] == "created"
        assert "summary" in body

    async def test_returns_404_for_missing_case(self, client):
        resp = await client.get(
            "/tools/case_status",
            params={"case_id": str(uuid.uuid4())},
            headers=_KEY,
        )
        assert resp.status_code == 404


# ── case_get_explanation ──────────────────────────────────────────────────────


class TestCaseGetExplanation:
    async def test_returns_pipeline_not_complete_before_category_set(self, client):
        cr = await client.post("/tools/case_create", json={"user_id": "u3"}, headers=_KEY)
        case_id = cr.json()["case_id"]

        resp = await client.get(
            "/tools/case_get_explanation",
            params={"case_id": case_id},
            headers=_KEY,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["category"] is None
        assert "not" in body["explanation"].lower()

    async def test_returns_404_for_missing_case(self, client):
        resp = await client.get(
            "/tools/case_get_explanation",
            params={"case_id": str(uuid.uuid4())},
            headers=_KEY,
        )
        assert resp.status_code == 404

    async def test_returns_explanation_when_category_set(self, client, db_session):
        """Directly set category on the ORM object to simulate post-pipeline state."""
        from db.models import Case

        case = Case(id=str(uuid.uuid4()), user_id="u4", status="analyzing")
        db_session.add(case)
        await db_session.commit()

        case.category = "medical_necessity"
        await db_session.commit()

        resp = await client.get(
            "/tools/case_get_explanation",
            params={"case_id": case.id},
            headers=_KEY,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["category"] == "medical_necessity"
        assert "medically necessary" in body["explanation"].lower()


# ── case_run_pipeline ─────────────────────────────────────────────────────────


class TestCaseRunPipeline:
    async def test_returns_pipeline_started(self, client):
        cr = await client.post("/tools/case_create", json={"user_id": "u5"}, headers=_KEY)
        case_id = cr.json()["case_id"]

        async def _noop(*args, **kwargs):
            pass

        with patch("app.tools._run_pipeline_background", side_effect=_noop):
            resp = await client.post(
                "/tools/case_run_pipeline",
                json={"case_id": case_id, "denial_text": "denied for medical necessity"},
                headers=_KEY,
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "pipeline_started"
        assert body["case_id"] == case_id

    async def test_returns_404_for_missing_case(self, client):
        resp = await client.post(
            "/tools/case_run_pipeline",
            json={"case_id": str(uuid.uuid4())},
            headers=_KEY,
        )
        assert resp.status_code == 404


# ── case_schedule_followup ────────────────────────────────────────────────────


class TestCaseScheduleFollowup:
    async def test_creates_reminder(self, client):
        cr = await client.post("/tools/case_create", json={"user_id": "u6"}, headers=_KEY)
        case_id = cr.json()["case_id"]

        resp = await client.post(
            "/tools/case_schedule_followup",
            json={"case_id": case_id, "due_at": "2025-06-15T12:00:00", "kind": "internal_appeal"},
            headers=_KEY,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "reminder_created"
        assert body["case_id"] == case_id

    async def test_invalid_due_at_returns_422(self, client):
        cr = await client.post("/tools/case_create", json={"user_id": "u7"}, headers=_KEY)
        case_id = cr.json()["case_id"]

        resp = await client.post(
            "/tools/case_schedule_followup",
            json={"case_id": case_id, "due_at": "not-a-date", "kind": "internal_appeal"},
            headers=_KEY,
        )
        assert resp.status_code == 422

    async def test_returns_404_for_missing_case(self, client):
        resp = await client.post(
            "/tools/case_schedule_followup",
            json={"case_id": str(uuid.uuid4()), "due_at": "2025-01-01T00:00:00"},
            headers=_KEY,
        )
        assert resp.status_code == 404


# ── OpenAPI export ────────────────────────────────────────────────────────────


class TestToolsOpenApi:
    async def test_contains_tools_paths(self, client):
        resp = await client.get("/tools/openapi.json")
        assert resp.status_code == 200
        spec = resp.json()
        paths = spec.get("paths", {})
        assert "/tools/case_create" in paths
        assert "/tools/case_status" in paths
        assert "/tools/case_run_pipeline" in paths

    async def test_has_correct_operation_ids(self, client):
        resp = await client.get("/tools/openapi.json")
        spec = resp.json()
        operation_ids = {
            op.get("operationId")
            for path_item in spec.get("paths", {}).values()
            for op in path_item.values()
        }
        expected = {
            "case_create",
            "case_status",
            "case_run_pipeline",
            "case_get_explanation",
            "case_get_draft",
            "case_approve",
            "case_schedule_followup",
        }
        assert expected.issubset(operation_ids)
