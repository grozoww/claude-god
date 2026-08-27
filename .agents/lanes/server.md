# Lane: server

The Python orchestrator: state machine, task store, Claude runner, gates, FastAPI
app, and the `god` CLI. Everything under `server/`, plus the root `pyproject.toml`
and `uv.lock` that define its dependencies and test config.

## Stack

- Python 3.11+, managed by `uv` (`uv run ...`, `uv.lock`). Never assume a bare
  `python`/`pytest` on PATH — go through `uv run`.
- FastAPI + Pydantic v2 for the API and every on-disk schema (`state.json`,
  `reviews/round-N.json` are just `model_dump_json()`/`model_validate_json()`).
- Typer + Rich for the CLI (`god`).
- `asyncio` throughout — phases, gates, and git calls are all `async def`.
- Tests: pytest + pytest-asyncio (`asyncio_mode = "auto"` in `pyproject.toml`,
  so async test functions need no decorator).

## Patterns

- **All task-folder I/O goes through `TaskStore`** (`server/god/store.py`). Don't
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

- Schema: `server/god/models.py`
- Legal phase transitions: `server/god/state_machine.py`
- Config (server-wide + per-project): `server/god/config.py`
- Task-folder persistence: `server/god/store.py`
- The pipeline itself: `server/god/pipeline.py`
- Claude subprocess/SDK backends: `server/god/runner.py`
- Shell-command gates: `server/god/gates.py`
- Git/worktree plumbing: `server/god/worktree.py`
- Prompt assembly + context routing: `server/god/prompts.py`
- Spec → criteria parsing: `server/god/specparse.py`
- SSE pub/sub: `server/god/bus.py`
- HTTP API: `server/god/api.py`
- CLI ↔ API shared operations: `server/god/service.py`
- CLI entry point: `server/god/cli.py`
- Tests: `server/tests/`

## Exemplar files

- `server/god/pipeline.py` — the canonical shape for a phase: prompt → `_call`
  → validate output → mutate `state` → `_set_phase`. Read `phase_gates` and
  `phase_review` for how gate failures and critic verdicts both turn into the
  same `Finding` list feeding `revise`.
- `server/god/models.py` — how a new field or enum should look: plain
  `pydantic.BaseModel`, `Field(default_factory=...)` for anything mutable,
  short docstring only when the "why" isn't obvious from the name.
- `server/tests/test_pipeline.py` — how to test a phase: build a synthetic tmp
  git repo, drive `Pipeline` against it, assert on the resulting `TaskState`
  and files written into the task folder — no mocking of git or the file layer.

## Gate

`uv run pytest server/tests -q` — verified: 42 passed.
