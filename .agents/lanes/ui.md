# Lane: ui

The React dashboard: task list, repo picker, and the task detail view (spec,
plan, criteria, verdicts, live diff, live event log). Everything under `ui/`.
Vite builds it straight into `server/god/static/`, which `server/god/api.py`
serves — in real use this is one process, but the lane only ever edits `ui/`.

## Stack

- React 18 + TypeScript 5.7 (`strict: true`, `noUnusedLocals`,
  `noUnusedParameters` — see `ui/tsconfig.json`), built with Vite 6.
- No state-management library: plain `useState`/`useEffect`, lifted into `App.tsx`.
- No CSS framework: hand-written `ui/src/styles.css`, plain class names.
- No component library — small shared primitives live in one file
  (`components/bits.tsx`): `Pill`, `Dot`, `Modal`, `Diff`, plus `cost`/`tokens`/
  `ago` formatters.
- Live updates via `EventSource` (SSE) against `/api/stream`, not polling —
  though `App.tsx` also does a 15s interval refresh as a backstop for anything
  the stream missed.

## Patterns

- **All server calls go through `ui/src/api.ts`**'s `api` object or its
  `subscribe()` helper — no ad hoc `fetch()` elsewhere.
- **`ui/src/types.ts` hand-mirrors `server/god/models.py`.** If a Pydantic field
  changes shape, update `types.ts` in the same change — nothing generates it.
- Errors from the API are surfaced as plain strings in local `err` state next to
  the action that caused them (see `Repos` in `App.tsx`, or `TaskDetail.tsx`),
  not a global toast/notification system.
- Components take data + callbacks as props; there is no context/store — `App.tsx`
  owns `tasks`/`repos`/`selected` and passes down what's needed.

## Where things live

- App shell, task list, repo management modal: `ui/src/App.tsx`
- New-task form: `ui/src/components/NewTask.tsx`
- Task detail (criteria, verdict, diff, events): `ui/src/components/TaskDetail.tsx`
- Shared small UI primitives: `ui/src/components/bits.tsx`
- API client + SSE subscribe: `ui/src/api.ts`
- Types mirroring the server schema: `ui/src/types.ts`
- Global styles: `ui/src/styles.css`
- Entry point: `ui/src/main.tsx`

## Exemplar files

- `ui/src/api.ts` — the pattern for adding a new endpoint call: one arrow
  function on the `api` object, typed with the matching interface from
  `types.ts`, going through the shared `j<T>()`/`post()` helpers.
- `ui/src/components/TaskDetail.tsx` — the biggest component; shows how a
  task-detail-shaped view is built from the `TaskDetail` type plus local UI
  state (which tab, redo note draft, etc).
- `ui/src/types.ts` — how a server model becomes a TS interface: same field
  names, `snake_case` preserved (no reshaping to `camelCase`).

## Gate

`cd ui && npm run typecheck` (i.e. `tsc -b --noEmit`) — verified: exits 0.
There is no UI test suite (`ui/package.json` has no `test` script); typecheck is
the only automated check for this lane today.
