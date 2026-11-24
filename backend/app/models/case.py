"""Case status, agent interface, and AgentResult (§5, §6)."""
from __future__ import annotations

from enum import StrEnum, auto
from typing import Generic, Protocol, TypeVar

from pydantic import BaseModel

from .base import CaseContext, SourceRef

T = TypeVar("T")
I_co = TypeVar("I_co", contravariant=True)
O_co = TypeVar("O_co", covariant=True)


class CaseStatus(StrEnum):
    CREATED = auto()
    EXTRACTING = auto()
    ANALYZING = auto()
    DRAFTING = auto()
    VERIFYING = auto()
    NEEDS_HUMAN_REVIEW = auto()
    AWAITING_APPROVAL = auto()
    READY_TO_SEND = auto()
    FOLLOW_UP = auto()
    EXTERNAL_REVIEW = auto()
    CLOSED = auto()


class AgentResult(BaseModel, Generic[T]):
    output: T
    confidence: float  # 0..1
    sources: list[SourceRef]
    trace_id: str
    tokens_in: int
    tokens_out: int
    latency_ms: int


class Agent(Protocol[I_co, O_co]):  # type: ignore[misc]
    name: str

    async def run(
        self,
        input: I_co,
        ctx: CaseContext,
    ) -> AgentResult[O_co]: ...
