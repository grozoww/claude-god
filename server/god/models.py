"""Data model for tasks, criteria, verdicts and events.

Everything the orchestrator knows lives on disk. These models are the schema
for `state.json`, `reviews/round-N.json` and `events.jsonl`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_task_id() -> str:
    return f"t-{utcnow():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"


class Phase(str, Enum):
    """Where a task is in the pipeline."""

    NEW = "new"
    SPEC = "spec"
    AWAITING_SPEC_APPROVAL = "awaiting_spec_approval"
    PLAN = "plan"
    IMPLEMENT = "implement"
    INTEGRATE = "integrate"
    GATES = "gates"
    REVIEW = "review"
    REVISE = "revise"
    DONE = "done"
    ESCALATED = "escalated"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_PHASES = {Phase.DONE, Phase.FAILED, Phase.CANCELLED}
#: Phases where the orchestrator stops and waits for a human.
HUMAN_PHASES = {Phase.AWAITING_SPEC_APPROVAL, Phase.ESCALATED}


class Status(str, Enum):
    IDLE = "idle"          # nothing running, may be advanced
    RUNNING = "running"    # a phase is executing right now
    BLOCKED = "blocked"    # waiting on a human, or parked on a rate limit
    DONE = "done"
    FAILED = "failed"


class CheckKind(str, Enum):
    CMD = "cmd"        # objective: a shell command must exit 0
    REVIEW = "review"  # subjective: the critic agent judges it
    HUMAN = "human"    # only a human can confirm it


class Check(BaseModel):
    kind: CheckKind = CheckKind.REVIEW
    cmd: str | None = None
    lane: str | None = None  # which lane owns running this gate


class CritStatus(str, Enum):
    UNKNOWN = "unknown"
    PASS = "pass"
    FAIL = "fail"


class Criterion(BaseModel):
    """One acceptance criterion. The contract the critic judges against."""

    id: str
    text: str
    check: Check = Field(default_factory=Check)
    lane: str | None = None
    status: CritStatus = CritStatus.UNKNOWN
    note: str | None = None


class Finding(BaseModel):
    """A required change. Emitted by the critic, by a failed gate, or by a human."""

    criterion: str | None = None
    file: str | None = None
    line: int | None = None
    problem: str
    required_change: str
    lane: str | None = None
    source: Literal["critic", "gate", "human"] = "critic"


class Verdict(BaseModel):
    verdict: Literal["pass", "revise"]
    round: int = 0
    summary: str = ""
    findings: list[Finding] = Field(default_factory=list)
    criteria_status: dict[str, CritStatus] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)


class GateResult(BaseModel):
    name: str
    cmd: str
    lane: str | None = None
    exit_code: int
    passed: bool
    duration_s: float
    output_tail: str = ""


class LaneState(BaseModel):
    name: str
    status: Status = Status.IDLE
    session_id: str | None = None
    worktree: str | None = None
    branch: str | None = None
    last_error: str | None = None


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: float = 0.0
    turns: int = 0
    duration_s: float = 0.0

    def add(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_creation_tokens=self.cache_creation_tokens + other.cache_creation_tokens,
            cost_usd=self.cost_usd + other.cost_usd,
            turns=self.turns + other.turns,
            duration_s=self.duration_s + other.duration_s,
        )


class TaskState(BaseModel):
    """`state.json` — the single source of truth for one task."""

    id: str = Field(default_factory=new_task_id)
    title: str
    description: str = ""
    repo: str
    phase: Phase = Phase.NEW
    status: Status = Status.IDLE

    round: int = 0
    max_rounds: int = 3
    criteria_locked: bool = False

    branch: str | None = None
    base_branch: str | None = None
    lanes: dict[str, LaneState] = Field(default_factory=dict)
    criteria: list[Criterion] = Field(default_factory=list)

    verdict: Verdict | None = None
    gate_results: list[GateResult] = Field(default_factory=list)
    pending_findings: list[Finding] = Field(default_factory=list)

    usage: Usage = Field(default_factory=Usage)
    budget_usd: float | None = None

    auto_approve_spec: bool = False
    error: str | None = None
    parked_until: datetime | None = None

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    def touch(self) -> None:
        self.updated_at = utcnow()


class Event(BaseModel):
    """One line of `events.jsonl`. Also pushed live to the dashboard."""

    ts: datetime = Field(default_factory=utcnow)
    task_id: str
    kind: str            # phase_start, phase_end, gate, verdict, log, error, state
    message: str = ""
    phase: Phase | None = None
    lane: str | None = None
    level: Literal["info", "warn", "error"] = "info"
    data: dict[str, Any] = Field(default_factory=dict)
