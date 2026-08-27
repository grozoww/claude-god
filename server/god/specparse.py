"""Parse acceptance criteria out of `spec.md`.

The spec is the contract and you edit it by hand before approving, so markdown
is the source of truth — not a parallel JSON file that would drift from it.

Expected shape:

    ## Acceptance criteria

    - [ ] AC-1: POST /orders returns 422 on a bad payload
          check: cmd `pytest -k bad_payload`
          lane: be
    - [ ] AC-2: empty cart state matches the design
          check: review
"""

from __future__ import annotations

import re

from .models import Check, CheckKind, Criterion

_ITEM = re.compile(r"^\s*[-*]\s*\[( |x|X)\]\s*(?:(AC-[\w.]+)\s*[:.)-]\s*)?(.+?)\s*$")
_ATTR = re.compile(r"^\s+(check|lane|note)\s*:\s*(.+?)\s*$", re.IGNORECASE)
_CMD = re.compile(r"^cmd\s*[`:]?\s*(.*?)\s*`?$", re.IGNORECASE)
_HEADING = re.compile(r"^#{1,6}\s+(.*)$")


def _parse_check(raw: str) -> Check:
    raw = raw.strip().strip("`").strip()
    low = raw.lower()
    if low.startswith("cmd"):
        m = _CMD.match(raw)
        cmd = (m.group(1) if m else "").strip().strip("`").strip()
        return Check(kind=CheckKind.CMD, cmd=cmd or None)
    if low.startswith("human"):
        return Check(kind=CheckKind.HUMAN)
    return Check(kind=CheckKind.REVIEW)


def parse_criteria(spec_md: str) -> list[Criterion]:
    """Pull criteria from the 'Acceptance criteria' section (or the whole doc)."""
    lines = spec_md.splitlines()

    # Narrow to the acceptance-criteria section when there is one.
    start, end = 0, len(lines)
    for i, line in enumerate(lines):
        h = _HEADING.match(line)
        if not h:
            continue
        title = h.group(1).strip().lower()
        if "acceptance" in title or title.startswith("criteria"):
            start = i + 1
            for j in range(i + 1, len(lines)):
                if _HEADING.match(lines[j]):
                    end = j
                    break
            break

    out: list[Criterion] = []
    current: Criterion | None = None
    auto = 0
    for line in lines[start:end]:
        m = _ITEM.match(line)
        if m:
            auto += 1
            cid = m.group(2) or f"AC-{auto}"
            text = m.group(3).strip()
            # `- [ ] AC-1: text` where the id was written inline without a match
            current = Criterion(id=cid, text=text)
            out.append(current)
            continue
        if current is None:
            continue
        a = _ATTR.match(line)
        if not a:
            # Continuation of the criterion text.
            if line.strip() and line.startswith((" ", "\t")):
                current.text = f"{current.text} {line.strip()}"
            continue
        key, val = a.group(1).lower(), a.group(2).strip()
        if key == "check":
            current.check = _parse_check(val)
        elif key == "lane":
            current.lane = val or None
        elif key == "note":
            current.note = val

    # De-duplicate ids so downstream lookups stay unambiguous.
    seen: dict[str, int] = {}
    for c in out:
        if c.id in seen:
            seen[c.id] += 1
            c.id = f"{c.id}.{seen[c.id]}"
        else:
            seen[c.id] = 0
    return out


def criteria_block(criteria: list[Criterion]) -> str:
    """Render criteria compactly for an agent prompt."""
    lines = []
    for c in criteria:
        check = c.check.kind.value
        if c.check.kind == CheckKind.CMD and c.check.cmd:
            check = f"cmd `{c.check.cmd}`"
        lane = f" [lane: {c.lane}]" if c.lane else ""
        lines.append(f"- {c.id}: {c.text}\n    check: {check}{lane}")
    return "\n".join(lines) if lines else "(no criteria parsed)"


def unverifiable(criteria: list[Criterion]) -> list[Criterion]:
    """Criteria with a cmd check that has no command — a spec-quality smell."""
    return [c for c in criteria if c.check.kind == CheckKind.CMD and not c.check.cmd]
