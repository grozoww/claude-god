"""M1 acceptance: task folders, transitions, event log, spec parsing."""

import pytest
from ccgod import state_machine as sm
from ccgod.models import Criterion, Finding, Phase, TaskState, Usage, Verdict
from ccgod.specparse import parse_criteria, unverifiable
from ccgod.store import TaskStore, iter_tasks


def make_store(tmp_path, **kw) -> TaskStore:
    state = TaskState(title="t", repo=str(tmp_path), **kw)
    store = TaskStore(tmp_path, state.id)
    store.init_dirs()
    store.save(state)
    return store


def test_roundtrip_and_dirs(tmp_path):
    store = make_store(tmp_path)
    assert store.exists()
    loaded = store.load()
    assert loaded.title == "t"
    assert loaded.phase is Phase.NEW
    for sub in ("reviews", "feedback", "artifacts", "lanes", "contract"):
        assert (store.dir / sub).is_dir()


def test_legal_transitions():
    assert sm.can(Phase.NEW, Phase.SPEC)
    assert sm.can(Phase.GATES, Phase.REVISE)
    assert sm.can(Phase.REVIEW, Phase.DONE)
    assert sm.can(Phase.DONE, Phase.REVISE)          # a human can reopen
    assert sm.can(Phase.ESCALATED, Phase.REVISE)


def test_illegal_transition_is_explicit():
    with pytest.raises(sm.IllegalTransition) as exc:
        sm.check(Phase.NEW, Phase.DONE)
    msg = str(exc.value)
    assert "illegal transition new -> done" in msg
    assert "allowed from new" in msg     # the error tells you what was allowed
    assert sm.TRANSITIONS[Phase.CANCELLED] == set()


def test_events_append_and_tail(tmp_path):
    from ccgod.models import Event

    store = make_store(tmp_path)
    for i in range(5):
        store.append_event(Event(task_id=store.task_id, kind="log", message=f"m{i}"))
    assert len(store.read_events()) == 5
    assert [e.message for e in store.read_events(2)] == ["m3", "m4"]


def test_bad_event_line_does_not_break_the_log(tmp_path):
    from ccgod.models import Event

    store = make_store(tmp_path)
    store.append_event(Event(task_id=store.task_id, kind="log", message="good"))
    with store.events_path.open("a") as fh:
        fh.write("{not json\n")
    assert [e.message for e in store.read_events()] == ["good"]


def test_verdict_roundtrip(tmp_path):
    store = make_store(tmp_path)
    v = Verdict(
        verdict="revise", round=1, summary="nope",
        findings=[Finding(criterion="AC-1", problem="p", required_change="c")],
    )
    store.save_verdict(v)
    back = store.load_verdict(1)
    assert back and back.findings[0].criterion == "AC-1"
    assert store.load_verdict(2) is None


def test_feedback_index_increments(tmp_path):
    store = make_store(tmp_path)
    assert store.next_feedback_index() == 1
    store.write_text("feedback/1.md", "x")
    assert store.next_feedback_index() == 2


def test_iter_tasks(tmp_path):
    a, b = make_store(tmp_path), make_store(tmp_path)
    ids = {t.id for t in iter_tasks(tmp_path)}
    assert ids == {a.task_id, b.task_id}


def test_atomic_save_survives_reload(tmp_path):
    store = make_store(tmp_path)
    s = store.load()
    s.usage = Usage(input_tokens=10, output_tokens=2, cost_usd=0.5)
    s.criteria = [Criterion(id="AC-1", text="x")]
    store.save(s)
    again = store.load()
    assert again.usage.cost_usd == 0.5
    assert again.criteria[0].id == "AC-1"
    assert again.updated_at >= again.created_at


def test_usage_add():
    total = Usage(input_tokens=1, cost_usd=0.5).add(Usage(input_tokens=2, cost_usd=0.25))
    assert total.input_tokens == 3
    assert total.cost_usd == 0.75


SPEC = """# Spec: thing

## Acceptance criteria

- [ ] AC-1: returns 422 on bad payload
      check: cmd `pytest -k bad`
      lane: be
- [ ] AC-2: empty state matches design
      check: review
- [ ] AC-3: feels good
      check: human
- [ ] AC-4: has no command
      check: cmd

## Out of scope

- [ ] this is not a criterion
"""


def test_parse_criteria_shape():
    cs = parse_criteria(SPEC)
    assert [c.id for c in cs] == ["AC-1", "AC-2", "AC-3", "AC-4"]
    assert cs[0].check.cmd == "pytest -k bad"
    assert cs[0].lane == "be"
    assert cs[1].check.kind.value == "review"
    assert cs[2].check.kind.value == "human"


def test_unverifiable_criteria_are_flagged():
    bad = unverifiable(parse_criteria(SPEC))
    assert [c.id for c in bad] == ["AC-4"]


def test_parse_handles_no_criteria_section():
    assert parse_criteria("# Spec\n\nJust prose.\n") == []
