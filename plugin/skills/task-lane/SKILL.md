---
name: task-lane
description: Implement one lane of a planned task inside its own worktree, obeying the frozen contract and file ownership. Use when acting as an implementer agent in the claude-god pipeline.
---

# Implementing a lane

You own a slice of one task. Other agents are working on other slices right now,
in other worktrees, and none of you can see each other.

## Hard boundaries

- **Only edit files inside your lane's paths.** Anything outside is another
  agent's; your edit there will be thrown away at merge and may break their work.
- **`contract/` is read-only.** If the contract is wrong, stop and say so in your
  reply. Do not "fix" it — the other lane is already building against it.
- **Do not commit.** The orchestrator commits your worktree for you.

## How to work

1. Read the spec, your lane brief, and the contract before touching anything.
2. Follow the repo's existing patterns. Match the surrounding code's naming,
   structure and error handling. The exemplar files in your lane brief are there
   to be imitated.
3. Write the tests that the acceptance criteria name. If a criterion says
   `check: cmd \`pytest -k foo\``, that test must exist and pass when you finish.
4. Run your checks yourself before you finish. Do not hand off work you have not run.

## Never do this

- Never weaken, skip, delete or `xfail` a test to make a gate pass. If a test is
  genuinely wrong, say so in your reply and leave it failing.
- Never leave `TODO`, stub bodies, `pass`, or "in a real implementation…" comments.
  Placeholder code fails review and costs another round.
- Never add features the spec did not ask for. Out of scope means out of scope.
- Never change config, CI or dependencies unless a criterion requires it.

## Revision rounds

When you are given findings, you are in a revision round. Then:

- Fix **exactly** the findings listed. Nothing else.
- Each finding names the criterion it violates. Your fix must make that criterion true.
- Do not refactor code the findings do not mention, however tempting.
- If a finding is wrong or impossible, say so plainly in your reply instead of
  making a change you do not believe in.

## Your reply

Short. What you changed, which criteria you believe are now satisfied, and
anything you could not do and why. The reply is read by a human, not parsed.
