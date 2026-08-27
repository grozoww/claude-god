---
description: Run a task end to end through the claude-god pipeline (spec, plan, implement, gates, critic loop).
argument-hint: <what to build>
allowed-tools: Bash(ccgod:*), Read, Glob, Grep
---

Run this task through the claude-god pipeline: **$ARGUMENTS**

Steps:

1. Check the orchestrator is set up: `ccgod doctor`. If the repo is not onboarded
   (no `.agents/project.md`), tell the user to run `ccgod init .` first and stop.
2. Create and run the task:

   ```
   ccgod new "$ARGUMENTS" --repo .
   ```

3. The pipeline stops at spec approval. Show the user the spec
   (`ccgod spec <task-id>`) and ask whether to approve. Do not approve on their behalf.
4. On approval run `ccgod approve <task-id>` and report progress as it streams.
5. If the task escalates, show the last verdict's findings and ask the user what to
   change. Turn their answer into `ccgod redo <task-id> "<note>"`.

Never edit the repo yourself while the pipeline is running — the lane agents own
those files.
