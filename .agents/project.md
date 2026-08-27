# claude-god

## Purpose

claude-god is a local orchestrator that runs a dev task end to end using multiple
headless Claude agents: spec → plan → implement (in parallel lanes) → integrate →
gates → critic review → done, with a bounded revise loop in between. It drives
everything through the `claude` CLI (`claude -p`, subscription auth, no API key),
one subprocess per phase, so orchestration itself is plain Python and costs zero
tokens. A React dashboard (built into `server/god/static/`) and a `god` CLI both
sit on top of the same `Service`/`Pipeline`, so a task can be run headless from a
terminal or watched live in a browser.

## Glossary

- **Task** — one unit of work. Lives at `<target-repo>/.agents/tasks/<task-id>/`
  and is driven through `Phase` states by the `Pipeline`.
- **Phase** — where a task is in the pipeline: `new → spec → awaiting_spec_approval
  → plan → implement → integrate → gates → review → (done | revise → implement …)
  → escalated | failed | cancelled`. See `server/god/state_machine.py`.
- **Lane** — a slice of the target repo (a set of path globs) owned by one
  implementing agent. Lanes run concurrently, each in its own `git worktree`, so
  two agents never edit the same files at once.
- **Gate** — a shell command that must exit 0. Objective, not judged by an agent.
  Gates run before the critic every round; the first failing gate stops the rest.
- **Criterion** (`AC-n`) — one acceptance-criterion line parsed out of `spec.md`'s
  "Acceptance criteria" section. Each has a `Check`: `cmd` (a gate), `review` (the
  critic judges it), or `human`.
- **Verdict** — the critic's JSON output for one review round: `pass` or `revise`,
  plus a list of findings. Written to `reviews/round-N.json`.
- **Finding** — one required change. Same shape whether it came from the critic,
  a failed gate, or a human's redo note — the implementer can't tell them apart.
- **Round** — one `revise → implement → gates → review` cycle. Capped by
  `max_rounds` (default 3); going over that escalates to a human instead of
  looping forever.
- **Contract** — the frozen interface written during `plan`, in `contract/`.
  Read-only for lane implementers; only the plan phase may write it.
- **Worktree** — a second checkout of the same repo via `git worktree add`, so a
  lane's agent has its own working directory and branch.
- **Park** — pausing a task (`Status.BLOCKED`, `parked_until` set) when the
  `claude` CLI reports a rate limit. A background watcher resumes it later; this
  is never treated as a task failure.
- **Escalate** — a task that hit `max_rounds` without a passing verdict, or was
  told by the plan/spec/review phase to stop. Waits for a human (`redo`/`approve`).
- **`.agents/`** — the per-project agent-context directory this onboarding writes:
  `project.md`, `lanes/*.md`, `config.json`, plus the generated `tasks/` folders.
- **`god`** — the CLI binary (`server/god/cli.py`, a Typer app): `init`, `new`,
  `list`, `show`, `run`, `approve`, `redo`, `cancel`, `usage`, `demo`, `serve`.

## Architecture map

Request flow: `god new "<desc>" -r <repo>` (CLI) or `POST /api/tasks` (API) →
`Service.create()` writes `state.json` (phase `new`) → `Pipeline.start()` schedules
an asyncio task → `Pipeline.run()` calls one `phase_*` method per step. Each
`phase_*` method builds a prompt (`prompts.py`) and hands it to `self._call()`,
which runs one `claude -p` subprocess via a `Runner`, accounts usage, and emits
`Event`s onto the `EventBus`, which the dashboard reads over SSE.

- `server/god/models.py` — the schema: `Phase`, `Status`, `Criterion`, `Finding`,
  `Verdict`, `TaskState`, `Event`. This is what `state.json` and
  `reviews/round-N.json` serialize to/from.
- `server/god/state_machine.py` — `TRANSITIONS`: legal phase moves as a plain
  dict, checked in code (`state_machine.check`), not left to model judgment.
- `server/god/config.py` — `ServerConfig` (`~/.god/config.json`, the repo
  registry, `max_parallel`, which `Runner` to use) and `ProjectConfig`
  (`<repo>/.agents/config.json`: lanes, gates, `base_branch`, `max_rounds`).
- `server/god/store.py` — `TaskStore`: every read/write of one task folder
  (`state.json`, `spec.md`, `plan.md`, `events.jsonl`, `reviews/`, `feedback/`,
  `artifacts/`, `contract/`, `lanes/*.md`). The only persistence layer — no DB.
- `server/god/pipeline.py` — `Pipeline`: one async method per phase
  (`phase_spec`, `phase_plan`, `phase_implement`, `phase_integrate`,
  `phase_gates`, `phase_review`, `phase_revise`), plus the `step()`/`run()`
  drivers that advance a task until it finishes or blocks on a human.
- `server/god/runner.py` — `PhaseRequest`/`PhaseResult`, and two backends behind
  one `Runner` protocol: `CliRunner` (subprocess `claude -p --output-format
  json`, default) and `SdkRunner` (`claude-agent-sdk`, optional).
- `server/god/gates.py` — `run_gates`: runs shell commands in order, stops at
  the first failure, turns failures into `Finding`s via `findings_from_gates`.
- `server/god/worktree.py` — git plumbing: branch/worktree create+remove, merge,
  diff. Every lane implements inside its own worktree.
- `server/god/prompts.py` — assembles each phase's prompt and does context
  routing: spec/plan get `project.md`; a lane dev gets its `CLAUDE.md` (auto,
  via `--add-dir`) + its lane doc + only its own criteria/findings; the critic
  gets the spec, the diff, and the invariants — never the implementer's context.
  It also loads `plugin/skills/<name>/SKILL.md` as the rules for each role.
- `server/god/specparse.py` — parses `- [ ] AC-n: ...` items out of `spec.md`
  into `Criterion` objects (with `check:`/`lane:`/`note:` sub-attributes).
- `server/god/bus.py` — `EventBus`: in-memory pub/sub with replay, feeds
  `GET /api/stream` (SSE).
- `server/god/api.py` — the FastAPI app: `/api/tasks`, `/api/repos`,
  `/api/onboard`, `/api/stream`, and serving the built dashboard from
  `server/god/static/`.
- `server/god/service.py` — `Service`: the operations shared by the CLI and the
  HTTP API (`create`, `start`, `approve_spec`, `redo`, `cancel`, `onboard`,
  `usage_summary`, `resume_parked`).
- `server/god/cli.py` — the `god` Typer CLI.
- `server/tests/` — pytest suite: `test_state.py` (transitions), `test_lanes.py`,
  `test_pipeline.py` (drives `Pipeline` against synthetic tmp repos),
  `test_api.py` (FastAPI `TestClient`).
- `ui/src/` — the React + TypeScript dashboard: `App.tsx` (task list, repo
  picker), `components/NewTask.tsx`, `components/TaskDetail.tsx` (criteria,
  verdict, diff, live event log), `api.ts` (fetch wrapper + SSE `subscribe()`),
  `types.ts` (hand-mirrors `server/god/models.py`). Vite builds this straight
  into `server/god/static/` (`ui/vite.config.ts`), so in real use it's one process.
- `plugin/` — the Claude Code plugin: `skills/{task-spec,task-plan,task-lane,
  task-review,project-onboard}/SKILL.md` are the actual rules each phase's agent
  follows (loaded at runtime by `prompts.py`); `agents/{planner,lane-dev,critic,
  integrator}.md` are the same roles as interactive subagent defs; `commands/
  task.md` is a slash command. This doc you're reading now exists because of
  `plugin/skills/project-onboard/SKILL.md`.
- `examples/toy/` — a tiny bundled repo (`store/` order model + pytest tests)
  that `god demo` copies, `git init`s, and registers, so a fresh install has
  something safe to run a full pipeline against without touching real code.

## Invariants

- All task state lives on disk under `<repo>/.agents/tasks/<id>/` — there is no
  database and no state that only exists in memory.
- A phase may only move to a phase listed for it in
  `state_machine.TRANSITIONS`; anything else raises `IllegalTransition`.
- Criteria lock (`state.criteria_locked = True`) the moment a task first enters
  `revise`. From then on the critic may only judge the existing criteria list,
  never add new ones mid-loop.
- The critic (`phase_review`) never gets `Edit` or `Bash` in its allowed tools —
  it can read, search, and write its own verdict file, but it can never modify
  the code it is judging.
- The `claude` CLI is always invoked with `--permission-mode acceptEdits` plus
  an explicit `--allowed-tools` list; `--dangerously-skip-permissions` is never
  used.
- Gates run before the critic, in order, and stop at the first failure — a
  failing gate produces a `Finding` and skips straight to `revise` without
  spending a critic turn.
- `max_rounds` (default 3, configurable per task/repo) always bounds the
  revise loop; exceeding it moves the task to `escalated`, never an unbounded
  retry.
- `.agents/tasks/` is kept out of the target repo's git history
  (`Service._ensure_gitignore` appends it to `.gitignore` on task creation) —
  task transcripts must never leak into the user's commits.

## Open questions

- None — the two stacks (`server/`, `ui/`) and their gates were verified
  directly against `pyproject.toml`, `ui/package.json`, and a live test run.
