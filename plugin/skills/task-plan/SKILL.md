---
name: task-plan
description: Turn an approved spec into an implementation plan, split work into lanes that can run in parallel, and freeze the interface contract between them. Use after a spec is approved in the claude-god pipeline.
---

# Planning a task

Your job is to make the work *parallelisable and unambiguous*. Two agents will
implement from your plan without being able to talk to each other.

## The contract rule

This is the single most important thing you do.

When more than one lane will work at once, they will each invent their own idea of
the shared interface unless you decide it first. So:

**Write the interface contract into `contract/` before any lane starts.** Types,
OpenAPI, JSON schemas, event shapes, function signatures — whatever the lanes share.
Lanes read it and may not change it. A lane that needs a contract change must stop
and say so.

Concretely: if the backend will return `Order`, the exact `Order` shape is written
by you, in `contract/`, now — not discovered by the frontend later.

## Lane split

- A lane is **a set of file paths plus its gates**. The persona is flavour text.
- Lane paths must not overlap. Overlap is how parallel work corrupts itself.
- If the work is genuinely sequential, use one lane. Fake parallelism costs more
  than it saves.
- Every acceptance criterion should map to exactly one lane.

## Required output

1. **`plan.md`**

```markdown
# Plan: <title>

## Shape

Two or three sentences: the approach you chose and the one you rejected, briefly.

## Contract

What you froze in `contract/` and why. Skip if single-lane.

## Lanes

### <lane-name>
- owns: <paths>
- criteria: AC-1, AC-3
- steps: ordered, concrete

## Risks

What could go wrong at integration time and what makes it not happen.
```

2. **`lanes/<lane>.md` for each lane** — a self-contained brief. The lane agent
   sees *only this file*, the spec, and the contract. It must be able to work from
   that alone:

```markdown
# Lane: <name>

## You build

Plain description.

## Files you own

Explicit paths.

## Criteria you must satisfy

- AC-1: ...

## Contract you must obey

Which files in `contract/`, and the two or three things that matter most in them.

## How this repo does it

Patterns and exemplar files worth imitating, with real paths.
```

3. **`contract/*`** — the frozen shapes, when multi-lane.

## Rules

- Planning only. Do not modify source files.
- Do not restate the spec. The lane agent reads the spec too.
- Prefer the boring approach that matches the repo's existing patterns.
