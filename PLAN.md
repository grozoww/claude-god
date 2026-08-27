# claude-god — build plan

A local system that runs dev tasks end to end with multiple Claude agents:
spec → plan → implement (parallel lanes) → gates → critic loop → done.
A Python orchestrator drives everything through `claude -p` (headless, subscription auth).
A web dashboard starts tasks, shows status, approves specs, sends work back.
All state lives on disk in task folders — agents are stateless workers.

## Locked decisions

| Area | Choice |
|---|---|
| Orchestrator + API | Python 3.12+, `uv`, FastAPI, one process |
| Claude access | `claude-agent-sdk` (spawns the `claude` CLI, uses subscription login); raw `claude -p` subprocess as fallback, isolated in one module |
| Dashboard UI | React + Vite + TypeScript, builds into `server/static/` |
| Prompts | Claude Code plugin: skills + agent defs, shared across projects |
| State | Task folders (`.agents/tasks/<id>/`) + `events.jsonl`; no database |
| Live updates | SSE (server → browser stream) |

## Principles (from the design)

1. Files are the state; every agent run is disposable and resumable.
2. Gates (build/test commands) run before any review agent — cheap checks first.
3. Criteria lock after round 1; `max_rounds` (default 3) then escalate to human.
4. Interface contract frozen before parallel lanes start; lanes own disjoint paths in worktrees.
5. Human feedback = another critic finding, same JSON shape, same loop.
6. Context routing: each agent gets only its slice (planner: project.md; lane: CLAUDE.md + lane doc + plan slice; critic: spec + diff + invariants).

## Repo layout

```
claude-god/
  server/          # FastAPI app: state machine, queue, claude runner, SSE, static UI
  ui/              # Vite + React → server/static
  plugin/          # Claude Code plugin: skills/, agents/, commands/
  examples/toy/    # tiny app used to test pipelines end to end
  PLAN.md
```

## Milestones

Each milestone has acceptance criteria (AC) — we eat our own dog food.

### M0 — Skeleton (~0.5 day)
Repo layout, `uv` project, FastAPI hello, Vite scaffold, plugin manifest, toy example app.
- AC: `uv run god serve` starts; `pnpm build` in ui/ lands in server/static; `claude --version` detected and logged.

### M1 — Task state core (~1 day)
Task folder schema (spec.md, plan.md, state.json, events.jsonl, reviews/, feedback/, lanes/).
State machine (phases + legal transitions) as code, JSON Schemas for state.json and the critic verdict.
CLI: `god new "<desc>" --repo <path>`, `god list`, `god show <id>`.
- AC: create/inspect a task; illegal transition rejected with a clear error; events append and tail correctly; transitions unit-tested.

### M2 — Claude runner (~1–2 days)
`PhaseRunner`: run one phase = one claude session (prompt, cwd, model, allowed tools).
Captures session_id, usage, structured result. Supports resume, timeout, cancel.
Queue with `max_parallel`; park task + retry when a rate-limit window is hit.
Usage per phase appended to events.jsonl.
Permissions: `--permission-mode acceptEdits` + explicit allowed-tools list. Never skip-permissions.
- AC: two phases run concurrently on the toy repo; usage numbers land in events; kill the orchestrator mid-run, restart, task resumes from last finished phase.

### M3 — Single-lane pipeline + plugin v0 (~2–3 days)  ← proves the whole idea
Skills: task-spec, task-plan, task-lane, task-review. Verdict JSON enforced by schema.
Gates runner (shell commands from config). Full loop: spec → approve (CLI) → plan →
implement → gates → critic → revise (criteria locked, max 3 rounds) → done/escalate.
- AC: "add endpoint X with tests" on the toy repo goes from request to a green diff
  passing gates + critic with zero hand-edits.
- AC: a run with a planted bug gets a `revise` verdict citing the criterion, and passes in round 2.
- AC: a hopeless task escalates after round 3 instead of looping.

### M4 — Dashboard v1 (~2 days)
Repo registry (explicit list, not home-dir globbing). REST + SSE.
Task table; detail view: criteria checklist with per-round pass/fail, live log, usage.
Buttons: start task, approve spec, redo with note, cancel.
- AC: the entire M3 flow is doable from the browser without touching the terminal.

### M5 — Parallel lanes (~2–3 days)
Plan phase freezes `contract/`. Worktree per lane; path-ownership check blocks
cross-lane edits. Integrator agent merges, runs full gate suite, fixes small breaks.
Lane status in the dashboard.
- AC: a BE+FE task on a toy monorepo runs both lanes concurrently, no cross-lane
  file touches, merged branch green through gates + critic.

### M6 — Project onboarding (~1 day)
`god init`: onboarding agent explores a repo, drafts `.agents/project.md`,
`lanes/*.md`, and a proposed `config.json`; user edits and approves.
`god refresh-context` re-runs it as a diff. Context routing implemented per the table.
- AC: `god init` on one of my real repos produces docs I keep with ≤10 min of edits.

### M7 — Hardening (ongoing)
Per-task token budgets, usage rollup view, notifications on escalation/done,
packaging (`uv tool install`, plugin install docs).

## Order rationale

M3 is the bet — if the critic loop converges on the toy repo, everything else is
plumbing. Dashboard waits until the CLI flow works (cheaper to debug). Parallelism
only after single-lane is boring and solid.

## Risks → guards

- Cost blowups → gates before agents, budgets, haiku for glue phases, max_rounds.
- Critic never satisfied → criteria lock + escalation.
- Lane merge conflicts → frozen contract + disjoint paths + integrator.
- Context rot → refresh-context + planner verifies claims before building on them.
- SDK/auth quirks → runner isolated behind one interface; raw subprocess fallback.

## Explicitly not doing now

Multi-user/auth, cloud hosting, GitHub/Jira integration, Windows support.

## Rough total

~9–12 focused days to the end of M6.
