"""Prompt assembly and context routing.

Rules for each role live in `plugin/skills/<name>/SKILL.md` — one source of
truth, usable both here (embedded into headless prompts) and interactively once
the plugin is installed in Claude Code.

Context routing is deliberate; nobody gets everything:
    spec/plan  -> project.md + repo exploration
    lane dev   -> CLAUDE.md (auto) + its lane doc + its criteria + findings
    critic     -> spec + diff + invariants only, never the implementer's context
"""

from __future__ import annotations

import os
from pathlib import Path

from .config import ProjectConfig
from .models import Criterion, Finding, TaskState
from .specparse import criteria_block

MAX_DOC_CHARS = 12000


def plugin_dir() -> Path:
    env = os.environ.get("GOD_PLUGIN_DIR")
    if env:
        return Path(env)
    # server/god/prompts.py -> repo root -> plugin/
    return Path(__file__).resolve().parents[2] / "plugin"


def load_skill(name: str) -> str:
    p = plugin_dir() / "skills" / name / "SKILL.md"
    if not p.exists():
        return ""
    text = p.read_text()
    # Strip YAML frontmatter; the body is what the agent needs.
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            text = parts[2]
    return text.strip()


def _clip(text: str | None, limit: int = MAX_DOC_CHARS) -> str:
    if not text:
        return ""
    return text if len(text) <= limit else text[:limit] + "\n\n[... truncated ...]"


def _section(title: str, body: str | None) -> str:
    body = (body or "").strip()
    return f"\n## {title}\n\n{body}\n" if body else ""


def project_context(repo: Path, sections: str = "all") -> str:
    """Read `.agents/project.md`, optionally only its invariants."""
    p = repo / ".agents" / "project.md"
    if not p.exists():
        return ""
    text = p.read_text()
    if sections == "invariants":
        lines, keep, out = text.splitlines(), False, []
        for line in lines:
            if line.startswith("#"):
                keep = "invariant" in line.lower() or "glossary" in line.lower()
            if keep:
                out.append(line)
        return "\n".join(out).strip() or ""
    return _clip(text)


def findings_block(findings: list[Finding]) -> str:
    if not findings:
        return ""
    out = []
    for i, f in enumerate(findings, 1):
        loc = ""
        if f.file:
            loc = f" ({f.file}{':' + str(f.line) if f.line else ''})"
        head = f"{i}. [{f.source}]"
        if f.criterion:
            head += f" {f.criterion}"
        out.append(
            f"{head}{loc}\n   Problem: {f.problem}\n   Required change: {f.required_change}"
        )
    return "\n\n".join(out)


# --------------------------------------------------------------------------
# Phase prompts
# --------------------------------------------------------------------------

def spec_prompt(
    state: TaskState,
    task_dir: Path,
    repo: Path,
    cfg: ProjectConfig,
    revise_note: str | None = None,
) -> str:
    lane_names = cfg.lane_names or ["main"]
    if len(lane_names) == 1:
        lane_rule = (
            f"- This repo has ONE lane (`{lane_names[0]}`). Do NOT add `lane:` to any "
            "criterion."
        )
    else:
        lane_rule = (
            f"- Valid lanes are exactly: {', '.join(f'`{n}`' for n in lane_names)}. "
            "A `lane:` attribute must use one of those names, or be omitted. "
            "Never invent a lane name."
        )
    return f"""{load_skill("task-spec")}

# Your task

Turn this request into a spec at `{task_dir / "spec.md"}`.

## Request

Title: {state.title}

{state.description}

{_section("Project context (.agents/project.md)", project_context(repo))}
{_section("Human revision note", revise_note)}

## Rules for this run

- Explore the repo first. Ground every claim in files you actually read.
- Write ONLY `{task_dir / "spec.md"}`. Change no source files.
- Every acceptance criterion needs a check. Prefer `cmd` checks that can run today
  in this repo; use `review` only when no command can decide it.
{lane_rule}
- If the request is too vague to attach checks to, say so in a `## Open questions`
  section and still write your best spec.

When you are done, reply with a two-line summary only.
"""


def plan_prompt(state: TaskState, task_dir: Path, repo: Path, cfg: ProjectConfig) -> str:
    lanes = "\n".join(
        f"- `{name}`: {lc.description or 'lane'} | paths: {', '.join(lc.paths) or '(unset)'}"
        for name, lc in cfg.lanes.items()
    )
    contract_note = (
        f"The interface contract lives in `{task_dir / 'contract'}`. Write the frozen "
        "shapes (types, OpenAPI, schemas) there BEFORE lanes start. Lanes read it and "
        "may not change it."
        if len(cfg.lanes) > 1
        else "Single lane: no separate contract needed unless the spec implies an API surface."
    )
    return f"""{load_skill("task-plan")}

# Your task

Write the implementation plan for this task.

## Spec

{_clip((task_dir / "spec.md").read_text() if (task_dir / "spec.md").exists() else "")}

{_section("Project context (.agents/project.md)", project_context(repo))}

## Lanes configured for this repo

{lanes or "- `main`: whole repo"}

{contract_note}

## Write these files

1. `{task_dir / "plan.md"}` — ordered steps, risks, and which lane owns what.
2. `{task_dir / "lanes"}/<lane>.md` for EACH lane you use — a self-contained brief:
   what that lane builds, which files it owns, which criteria it must satisfy,
   which contract files it must obey. A lane agent sees only its own brief.
3. Contract files under `{task_dir / "contract"}/` when more than one lane is used.

Change no source files in the repo. Planning only.

Reply with a two-line summary only.
"""


def implement_prompt(
    state: TaskState,
    task_dir: Path,
    lane: str,
    criteria: list[Criterion],
    findings: list[Finding],
    cfg: ProjectConfig,
    worktree: Path,
) -> str:
    lane_doc = (task_dir / "lanes" / f"{lane}.md")
    lane_cfg = cfg.lanes.get(lane)
    paths = ", ".join(lane_cfg.paths) if lane_cfg and lane_cfg.paths else "(whole repo)"
    contract = task_dir / "contract"
    contract_note = (
        f"- Read `{contract}/` and treat it as READ-ONLY. If you believe the contract is "
        "wrong, stop and say so in your reply instead of editing it."
        if contract.exists() and any(contract.iterdir())
        else ""
    )
    round_note = (
        f"""
## This is revision round {state.round}

Fix exactly the findings below. Do not refactor anything they do not touch.

{findings_block(findings)}
"""
        if findings
        else ""
    )
    return f"""{load_skill("task-lane")}

# Your task

You are the `{lane}` lane. Implement your part in this worktree: `{worktree}`

## Files you own

{paths}

Do not edit files outside your paths — another agent owns them and your edits
would be lost in the merge.

{contract_note}

## Read first

- `{task_dir / "spec.md"}` — the contract you are judged against
- `{task_dir / "plan.md"}` — the overall plan
- `{lane_doc}` — your lane brief

## Criteria you must satisfy

{criteria_block(criteria) if criteria else "(see spec.md — all criteria)"}
{round_note}

## Rules

- Write real code and real tests. No placeholders, no TODO stubs.
- Never weaken, skip or delete a test to make a gate pass.
- Run the relevant checks yourself before you finish.
- Do not commit; the orchestrator commits for you.

Reply with a short summary of what you changed and anything you could not do.
"""


def integrate_prompt(state: TaskState, task_dir: Path, repo: Path, merge_report: str) -> str:
    return f"""# Your task

Lane branches were merged into `{state.branch}` in `{repo}`. Make the merged tree
coherent and green.

## Merge report

```
{_clip(merge_report, 6000)}
```

## Read

- `{task_dir / "spec.md"}`
- `{task_dir / "plan.md"}`

## Rules

- Resolve any conflict markers, fix cross-lane breakage (imports, renamed symbols,
  type mismatches between the lanes).
- Keep both lanes' intent. Never delete one lane's work to make the other compile.
- Do not add features beyond what the spec asks for.
- Do not commit; the orchestrator commits.

Reply with a short summary of what you had to fix.
"""


def review_prompt(
    state: TaskState,
    task_dir: Path,
    repo: Path,
    diff: str,
    gate_summary: str,
    verdict_path: Path,
) -> str:
    lock_note = (
        """
## Criteria are LOCKED

This is not round 1. Judge ONLY the acceptance criteria listed above. You may not
raise new concerns, style preferences, or refactors — they are out of scope now.
If every criterion is met, the verdict is `pass`, even if you would have built it
differently.
"""
        if state.criteria_locked
        else ""
    )
    return f"""{load_skill("task-review")}

# Your task

Decide whether this change meets its spec. Your default stance is to REFUTE: try
to find the case where it fails. If you cannot, it passes.

## Spec

{_clip((task_dir / "spec.md").read_text() if (task_dir / "spec.md").exists() else "")}

## Acceptance criteria

{criteria_block(state.criteria)}
{lock_note}
{_section("Project invariants", project_context(repo, sections="invariants"))}

## Gate results (already run — objective, do not re-litigate)

{gate_summary}

## The change

```diff
{diff}
```

## Output

Write your verdict as JSON to `{verdict_path}` — nothing else, no other files.

```json
{{
  "verdict": "pass" | "revise",
  "round": {state.round},
  "summary": "one paragraph",
  "criteria_status": {{ "AC-1": "pass", "AC-2": "fail" }},
  "findings": [
    {{
      "criterion": "AC-2",
      "file": "src/orders.ts",
      "line": 88,
      "problem": "what is actually wrong",
      "required_change": "the specific change that would fix it",
      "lane": "be"
    }}
  ]
}}
```

Rules:
- `pass` requires every non-`human` criterion to be `pass`.
- Every finding MUST name the criterion it violates. No criterion, no finding.
- Findings must be concrete and actionable. "Consider improving error handling" is
  not a finding; "AC-4 requires a 409 on duplicate id, handler returns 500" is.
- You may read files with Read/Grep to verify. You may not modify anything.

Reply with just the verdict word and one sentence.
"""


def onboard_prompt(repo: Path, lanes_hint: str = "") -> str:
    agents = repo / ".agents"
    return f"""{load_skill("project-onboard")}

# Your task

Bootstrap agent context for this repo: `{repo}`

Explore it properly, then write:

1. `{agents / "project.md"}` — what this product IS (not how to code in it):
   purpose, domain glossary, architecture map with real paths, and an
   `## Invariants` section of rules that must never break.
2. `{agents / "lanes"}/<lane>.md` for each lane you propose — stack, patterns,
   where things live, two or three exemplar files worth imitating.
3. `{agents / "config.json"}` — lanes with `paths` and `gates`, plus repo-wide
   `gates`, `base_branch`, and `setup_cmd` if the repo needs an install step.

Config shape:

```json
{{
  "name": "my-app",
  "base_branch": "main",
  "setup_cmd": null,
  "gates": ["<command that validates the whole repo>"],
  "lanes": {{
    "be": {{ "paths": ["api/**"], "gates": ["<test cmd>"], "description": "..." }}
  }},
  "max_rounds": 3
}}
```

Rules:
- Gates must be commands that actually exist in this repo today. Verify by reading
  package.json / Makefile / pyproject.toml — do not invent scripts.
- If the repo is single-stack, propose ONE lane named `main`. Do not invent
  frontend/backend lanes that the code does not have.
- Lane paths must not overlap. Overlapping paths break parallel work.
- Do not modify any source file.

{lanes_hint}

Reply with a short summary of the lanes and gates you chose.
"""
