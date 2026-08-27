"""Seed a demo repo with tasks in every interesting state.

Only for regenerating the README screenshots — it writes task folders directly
instead of running agents, so the dashboard can show `done`, `running`,
`escalated` and `awaiting approval` side by side without spending tokens or
waiting for four real runs.

    uv run python scripts/seed-screenshots.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from ccgod.config import LaneConfig, ProjectConfig, Repo, ServerConfig, save_project_config
from ccgod.models import (
    Check,
    CheckKind,
    Criterion,
    CritStatus,
    Event,
    Finding,
    GateResult,
    LaneState,
    Phase,
    Status,
    TaskState,
    Usage,
    Verdict,
)
from ccgod.store import TaskStore

DEMO = Path.home() / ".ccgod" / "demo" / "shop"
NOW = datetime.now(UTC)


def ago(**kw) -> datetime:
    return NOW - timedelta(**kw)


def make_repo() -> None:
    for d in ("api/orders", "api/search", "web/src/pages", "packages/contract"):
        (DEMO / d).mkdir(parents=True, exist_ok=True)
    (DEMO / "README.md").write_text("# shop\n\nDemo monorepo for claude-god screenshots.\n")
    (DEMO / "api/orders/refunds.py").write_text("# placeholder\n")
    (DEMO / "web/src/pages/OrderHistory.tsx").write_text("export default function OrderHistory() {}\n")
    save_project_config(DEMO, ProjectConfig(
        name="shop",
        base_branch="main",
        contract_dir="packages/contract",
        gates=["pnpm lint"],
        lanes={
            "be": LaneConfig(paths=["api/**"], gates=["pytest api -q", "mypy api"],
                             description="Python API"),
            "fe": LaneConfig(paths=["web/**"], gates=["pnpm test:web", "pnpm tsc -b"],
                             description="React storefront"),
        },
    ))
    if not (DEMO / ".git").exists():
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=DEMO, check=True)
        subprocess.run(["git", "add", "-A"], cwd=DEMO, check=True)
        subprocess.run(
            ["git", "-c", "user.name=god", "-c", "user.email=g@l", "commit", "-qm", "shop"],
            cwd=DEMO, check=True,
        )


def crit(cid, text, kind, cmd=None, lane=None, status=CritStatus.UNKNOWN) -> Criterion:
    return Criterion(id=cid, text=text, lane=lane, status=status,
                     check=Check(kind=kind, cmd=cmd))


def write(state: TaskState, *, spec="", plan="", events=(), verdicts=()) -> TaskStore:
    store = TaskStore(DEMO, state.id)
    shutil.rmtree(store.dir, ignore_errors=True)   # re-runnable: never append twice
    store.init_dirs()
    if spec:
        store.write_text("spec.md", spec)
    if plan:
        store.write_text("plan.md", plan)
    for v in verdicts:
        store.save_verdict(v)
    for e in events:
        store.append_event(e)
    store.save(state)
    # save() stamps updated_at; put the intended time back so the list reads well.
    raw = store.state_path.read_text().replace(
        f'"updated_at": "{TaskState.model_validate_json(store.state_path.read_text()).updated_at.isoformat()}"',
        f'"updated_at": "{state.updated_at.isoformat()}"',
    )
    store.state_path.write_text(raw)
    return store


def ev(tid, kind, msg, mins, *, lane=None, level="info", phase=None) -> Event:
    return Event(ts=ago(minutes=mins), task_id=tid, kind=kind, message=msg,
                 lane=lane, level=level, phase=phase)


# --------------------------------------------------------------------------
REFUND_SPEC = """# Spec: refund a captured order

## Goal

A support agent can refund a captured order in full or in part, and the customer
sees the refund on their order history within one page load.

## Context

`api/orders/orders.py` owns capture today; orders are immutable once `captured`
is true, so a refund is a new record rather than a mutation. `web/src/pages/
OrderHistory.tsx` already renders the line table and has an empty slot under the
totals row.

## Approach

Add a `Refund` record keyed by order id, exposed through `POST /orders/{id}/refunds`.
The order stays immutable; `total_refunded_cents` is derived, never stored.

## Acceptance criteria

- [ ] AC-1: POST /orders/{id}/refunds with an amount over the order total returns 422
      check: cmd `pytest api -q -k refund_over_total`
      lane: be
- [ ] AC-2: Two refunds that together exceed the total are rejected, not clamped
      check: cmd `pytest api -q -k refund_cumulative`
      lane: be
- [ ] AC-3: Refunding an uncaptured order returns 409
      check: cmd `pytest api -q -k refund_uncaptured`
      lane: be
- [ ] AC-4: The order record itself is never mutated by a refund
      check: cmd `pytest api -q -k order_immutable`
      lane: be
- [ ] AC-5: Order history shows each refund with its date and amount
      check: cmd `pnpm test:web -- OrderHistory`
      lane: fe
- [ ] AC-6: The refund row reads clearly when the order is fully refunded
      check: review
      lane: fe

## Out of scope

- Refunds to a different payment method than the original capture.
- Partial refunds of individual lines — this release refunds against the order total.

## Open questions

None. Assumed refunds are recorded in the capture currency only.
"""

t1 = TaskState(
    id="t-20260827-0914-refund",
    title="Refund a captured order, full or partial",
    description="Support needs to refund from the admin panel. Customer should see it in order history.",
    repo=str(DEMO),
    phase=Phase.AWAITING_SPEC_APPROVAL,
    status=Status.BLOCKED,
    created_at=ago(minutes=6), updated_at=ago(minutes=2),
    usage=Usage(input_tokens=41, output_tokens=3180, cache_read_tokens=284_115,
                cost_usd=0.31, turns=9, duration_s=54),
    criteria=[
        crit("AC-1", "POST /orders/{id}/refunds with an amount over the order total returns 422", CheckKind.CMD, "pytest api -q -k refund_over_total", "be"),
        crit("AC-2", "Two refunds that together exceed the total are rejected, not clamped", CheckKind.CMD, "pytest api -q -k refund_cumulative", "be"),
        crit("AC-3", "Refunding an uncaptured order returns 409", CheckKind.CMD, "pytest api -q -k refund_uncaptured", "be"),
        crit("AC-4", "The order record itself is never mutated by a refund", CheckKind.CMD, "pytest api -q -k order_immutable", "be"),
        crit("AC-5", "Order history shows each refund with its date and amount", CheckKind.CMD, "pnpm test:web -- OrderHistory", "fe"),
        crit("AC-6", "The refund row reads clearly when the order is fully refunded", CheckKind.REVIEW, None, "fe"),
    ],
)
write(t1, spec=REFUND_SPEC, events=[
    ev(t1.id, "state", "task created: Refund a captured order, full or partial", 6),
    ev(t1.id, "phase_start", "spec started", 6, phase=Phase.SPEC),
    ev(t1.id, "phase_end", "spec finished", 2, phase=Phase.SPEC),
    ev(t1.id, "log", "6 acceptance criteria parsed", 2, phase=Phase.SPEC),
    ev(t1.id, "state", "spec -> awaiting_spec_approval", 2, phase=Phase.AWAITING_SPEC_APPROVAL),
])

# --------------------------------------------------------------------------
t2 = TaskState(
    id="t-20260827-0857-history",
    title="Customer-facing order history page",
    description="",
    repo=str(DEMO),
    phase=Phase.IMPLEMENT, status=Status.RUNNING,
    branch="ccgod/t-20260827-0857-history", base_branch="main",
    created_at=ago(minutes=19), updated_at=ago(seconds=25),
    usage=Usage(input_tokens=96, output_tokens=14_820, cache_read_tokens=1_902_440,
                cost_usd=2.14, turns=41, duration_s=612),
    lanes={
        "be": LaneState(name="be", status=Status.DONE, session_id="0f2c…a11",
                        branch="ccgod/t-20260827-0857-history--be",
                        worktree=str(Path.home() / ".ccgod/worktrees/t-20260827-0857-history/be")),
        "fe": LaneState(name="fe", status=Status.RUNNING, session_id="7b41…c02",
                        branch="ccgod/t-20260827-0857-history--fe",
                        worktree=str(Path.home() / ".ccgod/worktrees/t-20260827-0857-history/fe")),
    },
    criteria=[
        crit("AC-1", "GET /orders returns the caller's orders, newest first, paginated at 20", CheckKind.CMD, "pytest api -q -k list_orders", "be", CritStatus.PASS),
        crit("AC-2", "A caller never sees another customer's order, even by guessing an id", CheckKind.CMD, "pytest api -q -k orders_isolation", "be", CritStatus.PASS),
        crit("AC-3", "The list endpoint responds in under 100ms for a customer with 500 orders", CheckKind.CMD, "pytest api -q -k orders_perf", "be"),
        crit("AC-4", "/account/orders renders the list with totals and status per order", CheckKind.CMD, "pnpm test:web -- OrderHistory", "fe"),
        crit("AC-5", "The empty state explains what will appear here rather than showing a blank table", CheckKind.REVIEW, None, "fe"),
    ],
)
write(t2, spec="# Spec: customer-facing order history\n", plan="""# Plan: order history

## Shape

Read-only list endpoint plus a page. Rejected: reusing the admin order search —
it joins supplier tables the storefront must never touch.

## Contract

`packages/contract/orders.ts` freezes `OrderSummary` and the pagination envelope.
Both lanes build against it; neither may change it.

## Lanes

### be
- owns: api/**
- criteria: AC-1, AC-2, AC-3
- steps: query by customer id with a covering index, then the paginated serialiser.

### fe
- owns: web/**
- criteria: AC-4, AC-5
- steps: route, data hook against the frozen contract, table, empty state.

## Risks

The perf criterion (AC-3) is the one that can fail late — the index has to land
in the same migration or the 500-order case falls back to a sequential scan.
""", events=[
    ev(t2.id, "state", "task created", 19),
    ev(t2.id, "phase_start", "spec started", 19, phase=Phase.SPEC),
    ev(t2.id, "phase_end", "spec finished", 17, phase=Phase.SPEC),
    ev(t2.id, "log", "5 acceptance criteria parsed", 17, phase=Phase.SPEC),
    ev(t2.id, "state", "spec -> awaiting_spec_approval", 17, phase=Phase.AWAITING_SPEC_APPROVAL),
    ev(t2.id, "state", "spec approved", 15, phase=Phase.PLAN),
    ev(t2.id, "phase_start", "plan started", 15, phase=Phase.PLAN),
    ev(t2.id, "phase_end", "plan finished", 12, phase=Phase.PLAN),
    ev(t2.id, "log", "contract frozen: packages/contract/orders.ts", 12, phase=Phase.PLAN),
    ev(t2.id, "log", "lanes: be, fe", 12, phase=Phase.PLAN),
    ev(t2.id, "state", "plan -> implement", 12, phase=Phase.IMPLEMENT),
    ev(t2.id, "log", "worktree ~/.ccgod/worktrees/t-.../be", 12, lane="be"),
    ev(t2.id, "log", "worktree ~/.ccgod/worktrees/t-.../fe", 12, lane="fe"),
    ev(t2.id, "phase_start", "implement started", 12, lane="be", phase=Phase.IMPLEMENT),
    ev(t2.id, "phase_start", "implement started", 12, lane="fe", phase=Phase.IMPLEMENT),
    ev(t2.id, "phase_end", "implement finished", 4, lane="be", phase=Phase.IMPLEMENT),
    ev(t2.id, "log", "committed 7 files", 4, lane="be", phase=Phase.IMPLEMENT),
    ev(t2.id, "log", "covering index added in migration 0041", 4, lane="be"),
    ev(t2.id, "log", "still working: OrderHistory table", 1, lane="fe"),
])

# --------------------------------------------------------------------------
t3_findings = [
    Finding(criterion="AC-2", file="api/search/limiter.py", line=64,
            problem="The limiter keys on the client IP alone, so every request behind the office NAT shares one bucket. AC-2 requires per-API-key limiting for authenticated callers.",
            required_change="Key the bucket on the API key when the request is authenticated, falling back to IP only for anonymous traffic.",
            lane="be", source="critic"),
    Finding(criterion="AC-4", file="api/search/limiter.py", line=112,
            problem="On limit the handler returns 429 with an empty body and no Retry-After. AC-4 requires the client to be told when it may retry.",
            required_change="Return Retry-After with the seconds remaining in the window, and a JSON body naming the limit that was hit.",
            lane="be", source="critic"),
    Finding(problem="Gate failed (exit 1): `pytest api -q`\n\n```\nFAILED api/tests/test_limiter.py::test_burst_allows_ten - assert 9 == 10\n1 failed, 84 passed in 3.10s\n```",
            required_change="Make `pytest api -q` exit 0 without weakening the tests or the check itself.",
            lane="be", source="gate"),
]
t3 = TaskState(
    id="t-20260827-0803-ratelimit",
    title="Rate-limit the public search endpoint",
    description="",
    repo=str(DEMO),
    phase=Phase.ESCALATED, status=Status.BLOCKED,
    round=4, max_rounds=3, criteria_locked=True,
    branch="ccgod/t-20260827-0803-ratelimit", base_branch="main",
    created_at=ago(minutes=73), updated_at=ago(minutes=11),
    usage=Usage(input_tokens=212, output_tokens=38_610, cache_read_tokens=6_441_002,
                cost_usd=7.82, turns=118, duration_s=2_930),
    lanes={"be": LaneState(name="be", status=Status.DONE, branch="ccgod/t-20260827-0803-ratelimit--be")},
    criteria=[
        crit("AC-1", "An anonymous caller is limited to 60 search requests per minute", CheckKind.CMD, "pytest api -q -k anon_limit", "be", CritStatus.PASS),
        crit("AC-2", "An authenticated caller is limited per API key, not per IP", CheckKind.CMD, "pytest api -q -k key_limit", "be", CritStatus.FAIL),
        crit("AC-3", "A burst of 10 within one second is allowed inside the minute budget", CheckKind.CMD, "pytest api -q -k burst", "be", CritStatus.FAIL),
        crit("AC-4", "A limited response carries Retry-After and names the limit that was hit", CheckKind.CMD, "pytest api -q -k retry_after", "be", CritStatus.FAIL),
        crit("AC-5", "Limiter state survives a process restart", CheckKind.REVIEW, None, "be", CritStatus.PASS),
    ],
    pending_findings=t3_findings,
    gate_results=[
        GateResult(name="pytest", cmd="pytest api -q", lane="be", exit_code=1, passed=False,
                   duration_s=3.4,
                   output_tail="FAILED api/tests/test_limiter.py::test_burst_allows_ten - assert 9 == 10\n1 failed, 84 passed in 3.10s"),
    ],
    verdict=Verdict(verdict="revise", round=3,
                    summary="The window arithmetic is off by one at the burst boundary, and the key selection still falls back to IP for authenticated callers. Three of five criteria fail. The same burst assertion has failed in every round, which suggests the token-bucket refill is being recomputed per request rather than per window.",
                    criteria_status={"AC-1": CritStatus.PASS, "AC-2": CritStatus.FAIL,
                                     "AC-3": CritStatus.FAIL, "AC-4": CritStatus.FAIL,
                                     "AC-5": CritStatus.PASS},
                    findings=t3_findings[:2], created_at=ago(minutes=11)),
)
write(t3, spec="# Spec: rate-limit public search\n", verdicts=[
    Verdict(verdict="revise", round=0, summary="No per-key limiting at all; only the anonymous path is implemented.", criteria_status={}, created_at=ago(minutes=60)),
    Verdict(verdict="revise", round=1, summary="Per-key limiting added but the burst window is computed from wall clock, so it drifts.", criteria_status={}, created_at=ago(minutes=44)),
    Verdict(verdict="revise", round=2, summary="Burst still off by one; Retry-After still missing.", criteria_status={}, created_at=ago(minutes=27)),
    t3.verdict,
], events=[
    ev(t3.id, "gate", "FAIL: pytest api -q", 13, lane="be", level="warn", phase=Phase.GATES),
    ev(t3.id, "verdict", "round 3: revise", 11, level="warn", phase=Phase.REVIEW),
    ev(t3.id, "log", "round 4 would exceed max_rounds=3", 11, level="warn", phase=Phase.REVISE),
    ev(t3.id, "state", "revise -> escalated", 11, phase=Phase.ESCALATED),
])

# --------------------------------------------------------------------------
t4 = TaskState(
    id="t-20260826-1740-dupsku",
    title="Reject duplicate SKUs in a single order",
    description="",
    repo=str(DEMO),
    phase=Phase.DONE, status=Status.DONE,
    round=1, max_rounds=3, criteria_locked=True,
    branch="ccgod/t-20260826-1740-dupsku", base_branch="main",
    created_at=ago(hours=17), updated_at=ago(hours=16, minutes=44),
    usage=Usage(input_tokens=64, output_tokens=9_140, cache_read_tokens=1_233_907,
                cost_usd=1.18, turns=31, duration_s=486),
    lanes={"be": LaneState(name="be", status=Status.DONE, branch="ccgod/t-20260826-1740-dupsku--be")},
    criteria=[
        crit("AC-1", "Two lines with the same sku raise ValidationError naming the sku", CheckKind.CMD, "pytest api -q -k duplicate_sku", "be", CritStatus.PASS),
        crit("AC-2", "The check runs before any line is parsed", CheckKind.CMD, "pytest api -q -k duplicate_sku_first", "be", CritStatus.PASS),
        crit("AC-3", "Case differences count as the same sku", CheckKind.CMD, "pytest api -q -k sku_case", "be", CritStatus.PASS),
        crit("AC-4", "All pre-existing tests still pass", CheckKind.CMD, "pytest api -q", "be", CritStatus.PASS),
    ],
    gate_results=[
        GateResult(name="pytest", cmd="pytest api -q", lane="be", exit_code=0, passed=True,
                   duration_s=3.1, output_tail="88 passed in 3.05s"),
        GateResult(name="mypy", cmd="mypy api", lane="be", exit_code=0, passed=True,
                   duration_s=6.8, output_tail="Success: no issues found in 34 source files"),
        GateResult(name="pnpm", cmd="pnpm lint", exit_code=0, passed=True,
                   duration_s=4.2, output_tail="✔ no problems"),
    ],
    verdict=Verdict(verdict="pass", round=1,
                    summary="Every criterion is met. The duplicate check is a set comparison over normalised skus that runs before line parsing, which is what AC-2 asks for, and the error names the offending sku. Round 0 was sent back because case-folding was missing; that is now covered by its own test.",
                    criteria_status={f"AC-{i}": CritStatus.PASS for i in range(1, 5)},
                    created_at=ago(hours=16, minutes=44)),
)
write(t4, spec="# Spec: reject duplicate SKUs\n", verdicts=[
    Verdict(verdict="revise", round=0,
            summary="Duplicate detection works but is case-sensitive, so 'ABC' and 'abc' both pass. AC-3 fails.",
            criteria_status={"AC-1": CritStatus.PASS, "AC-3": CritStatus.FAIL},
            findings=[Finding(criterion="AC-3", file="api/orders/orders.py", line=44,
                              problem="Skus are compared verbatim, so 'ABC-1' and 'abc-1' are treated as different lines.",
                              required_change="Normalise with casefold() before the duplicate check, and add a test for the mixed-case pair.",
                              lane="be")],
            created_at=ago(hours=16, minutes=56)),
    t4.verdict,
], events=[
    ev(t4.id, "verdict", "round 1: pass", 1004, phase=Phase.REVIEW),
    ev(t4.id, "state", "review -> done", 1004, phase=Phase.DONE),
])

# --------------------------------------------------------------------------
cfg = ServerConfig.load()
cfg.repos = [r for r in cfg.repos if r.name != "shop"]
cfg.repos.insert(0, Repo(name="shop", path=str(DEMO)))
cfg.save()

make_repo()
print(f"seeded 4 tasks in {DEMO}")
