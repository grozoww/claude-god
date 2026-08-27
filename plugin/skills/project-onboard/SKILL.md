---
name: project-onboard
description: Bootstrap agent context for a repo — write project.md, lane docs and config.json so the pipeline knows what the product is and how to verify it. Use when setting up claude-god on a new project, or refreshing stale project context.
---

# Onboarding a repo

You are writing what future agents will believe about this project. Wrong facts
here become wrong plans forever, so verify everything in a file before you write it.

## What each document is for

`CLAUDE.md` already tells agents **how to work in the repo**. Do not duplicate it.

**`project.md` tells them what the product IS.** That is the gap:

- **Purpose** — what it does, for whom, in three sentences.
- **Glossary** — the domain words this codebase uses and what they actually mean
  here. "Booking", "performance", "supplier" mean specific things; write those.
- **Architecture map** — the real components with real paths, and how a request
  flows through them. A planner uses this to decide where work goes.
- **Invariants** — rules that must never break. This section is fed to the critic
  on every review, so it must be short, true and consequential:
  "orders are immutable after capture", "the supplier id is never exposed to the
  client", "migrations are forward-only".

**`lanes/<name>.md`** — stack, patterns, where things live, and two or three
exemplar files worth imitating.

**`config.json`** — the mechanics: lane paths, gate commands, base branch.

## Gates are the part people get wrong

A gate is a shell command that decides pass/fail. It must:

- **Exist today.** Read `package.json`, `Makefile`, `pyproject.toml`, CI config.
  Never invent a script name. If there is no test command, say so and set `gates: []`
  rather than writing a command that will always fail.
- **Be fast enough to run every round.** A 20-minute e2e suite is not a gate.
- **Be ordered cheapest first** — typecheck before unit tests before integration.

## Lanes

- **Default to one lane named `main`.** Only split when the repo genuinely has
  separate stacks with separate test commands.
- Lane paths must not overlap. Overlap breaks parallel work.
- If you split, every lane needs its own gates.

## Rules

- Read the repo properly first: entry points, routes, models, tests, CI.
- Do not modify any source file. You write only inside `.agents/`.
- Prefer fewer, truer statements over a long document of plausible guesses.
- If something is genuinely unclear, write it as an open question in `project.md`
  rather than guessing.
