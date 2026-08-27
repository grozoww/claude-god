"""Legal phase transitions.

The pipeline is a state machine in code, not model judgment. Anything that is
not listed here cannot happen; attempts raise and are logged.
"""

from __future__ import annotations

from .models import Phase

# phase -> phases it may move to
TRANSITIONS: dict[Phase, set[Phase]] = {
    Phase.NEW: {Phase.SPEC, Phase.CANCELLED},
    Phase.SPEC: {Phase.AWAITING_SPEC_APPROVAL, Phase.PLAN, Phase.FAILED, Phase.CANCELLED},
    Phase.AWAITING_SPEC_APPROVAL: {Phase.PLAN, Phase.SPEC, Phase.CANCELLED},
    Phase.PLAN: {Phase.IMPLEMENT, Phase.FAILED, Phase.CANCELLED},
    Phase.IMPLEMENT: {Phase.INTEGRATE, Phase.GATES, Phase.FAILED, Phase.CANCELLED},
    Phase.INTEGRATE: {Phase.GATES, Phase.FAILED, Phase.CANCELLED},
    Phase.GATES: {Phase.REVIEW, Phase.REVISE, Phase.FAILED, Phase.CANCELLED},
    Phase.REVIEW: {Phase.DONE, Phase.REVISE, Phase.ESCALATED, Phase.FAILED, Phase.CANCELLED},
    Phase.REVISE: {Phase.IMPLEMENT, Phase.ESCALATED, Phase.CANCELLED},
    # Terminal-ish phases a human can reopen.
    Phase.ESCALATED: {Phase.REVISE, Phase.IMPLEMENT, Phase.SPEC, Phase.DONE, Phase.CANCELLED},
    Phase.DONE: {Phase.REVISE, Phase.CANCELLED},
    Phase.FAILED: {Phase.SPEC, Phase.IMPLEMENT, Phase.REVISE, Phase.CANCELLED},
    Phase.CANCELLED: set(),
}


class IllegalTransition(RuntimeError):
    def __init__(self, src: Phase, dst: Phase) -> None:
        allowed = ", ".join(sorted(p.value for p in TRANSITIONS.get(src, set()))) or "(none)"
        super().__init__(
            f"illegal transition {src.value} -> {dst.value}; allowed from {src.value}: {allowed}"
        )
        self.src, self.dst = src, dst


def can(src: Phase, dst: Phase) -> bool:
    return dst in TRANSITIONS.get(src, set())


def check(src: Phase, dst: Phase) -> None:
    if not can(src, dst):
        raise IllegalTransition(src, dst)
