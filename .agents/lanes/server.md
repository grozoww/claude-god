# Lane: server

The Python orchestrator: state machine, task store, Claude runner, gates, FastAPI
app, and the `ccgod` CLI. Everything under `server/`, plus the root `pyproject.toml`
and `uv.lock` that define its dependencies and test config.

## Stack

- Python 3.11+, managed by `uv` (`uv run ...`, `uv.lock`). Never assume a bare
  `python`/`pytest` on PATH — go through `uv run`.
- FastAPI + Pydantic v2 for the API and every on-disk schema (`state.json`,
  `reviews/round-N.json` are just `model_dump_json()`/`model_validate_json()`).
- Typer + Rich for the CLI (`ccgod`).
- `asyncio` throughout — phases, gates, and git calls are all `async def`.
- Tests: pytest + pytest-asyncio (`asyncio_mode = "auto"` in `pyproject.toml`,
  so async test functions need no decorator).

## Patterns

- **All task-folder I/O goes through `TaskStore`** (`server/ccgod/store.py`). Don't
  read/write `state.json` or `spec.md` etc. directly elsewhere — add a method to
  `TaskStore` instead.
- **Every phase transition goes through `state_machine.check()`** before
  `state.phase` is mutated (see `Pipeline._set_phase`). Don't set `.phase`
  directly.
- **Pydantic models are the schema**, not just DTOs — `models.py` docstring:
  "Everything the orchestrator knows lives on disk." Add a field there first,
  then thread it through.
- **One `Pipeline.phase_*` method per pipeline phase**, each following the same
  shape: build a prompt, `await self._call(...)`, validate the phase's expected
  output file exists, update `state`, call `self._set_phase(...)`.
- Writes via `_atomic_write` (temp file + `os.replace`) so a crash never leaves
  a half-written `state.json`.
- Two `Runner` backends behind one `Protocol` (`runner.py`) — `CliRunner` is the
  default and always available; `SdkRunner` is optional and falls back silently
  to `CliRunner` if `claude-agent-sdk` isn't installed.

## Where things live

- Schema: `server/ccgod/models.py`
- Legal phase transitions: `server/ccgod/state_machine.py`
- Config (server-wide + per-project): `server/ccgod/config.py`
- Task-folder persistence: `server/ccgod/store.py`
- The pipeline itself: `server/ccgod/pipeline.py`
- Claude subprocess/SDK backends: `server/ccgod/runner.py`
- Shell-command gates: `server/ccgod/gates.py`
- Git/worktree plumbing: `server/ccgod/worktree.py`
- Prompt assembly + context routing: `server/ccgod/prompts.py`
- Spec → criteria parsing: `server/ccgod/specparse.py`
- SSE pub/sub: `server/ccgod/bus.py`
- HTTP API: `server/ccgod/api.py`
- CLI ↔ API shared operations: `server/ccgod/service.py`
- CLI entry point: `server/ccgod/cli.py`
- Tests: `server/tests/`

## Exemplar files

- `server/ccgod/pipeline.py` — the canonical shape for a phase: prompt → `_call`
  → validate output → mutate `state` → `_set_phase`. Read `phase_gates` and
  `phase_review` for how gate failures and critic verdicts both turn into the
  same `Finding` list feeding `revise`.
- `server/ccgod/models.py` — how a new field or enum should look: plain
  `pydantic.BaseModel`, `Field(default_factory=...)` for anything mutable,
  short docstring only when the "why" isn't obvious from the name.
- `server/tests/test_pipeline.py` — how to test a phase: build a synthetic tmp
  git repo, drive `Pipeline` against it, assert on the resulting `TaskState`
  and files written into the task folder — no mocking of git or the file layer.

## Gate

`uv run pytest server/tests -q` — verified: 42 passed.
