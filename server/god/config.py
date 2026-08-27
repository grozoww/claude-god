"""Global config (~/.god) and per-project config (<repo>/.agents/config.json)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, Field

AGENTS_DIR = ".agents"
TASKS_DIR = "tasks"


def god_home() -> Path:
    """Where the repo registry and server-wide settings live."""
    return Path(os.environ.get("GOD_HOME", Path.home() / ".god"))


class LaneConfig(BaseModel):
    """A lane is a set of paths plus the gates that guard them.

    The persona ("backend dev") is flavour text; the paths are what actually
    prevents two parallel agents from stepping on each other.
    """

    paths: list[str] = Field(default_factory=list)
    gates: list[str] = Field(default_factory=list)
    description: str = ""
    doc: str | None = None  # defaults to .agents/lanes/<name>.md


class ProjectConfig(BaseModel):
    """`<repo>/.agents/config.json`."""

    name: str = ""
    lanes: dict[str, LaneConfig] = Field(default_factory=dict)
    gates: list[str] = Field(default_factory=list)  # repo-wide gates, run after integrate
    contract_dir: str | None = None
    max_rounds: int = 3
    base_branch: str = "main"
    branch_prefix: str = "god/"
    auto_approve_spec: bool = False
    budget_usd: float | None = None
    models: dict[str, str] = Field(
        default_factory=lambda: {
            "spec": "sonnet",
            "plan": "sonnet",
            "implement": "sonnet",
            "integrate": "sonnet",
            "review": "sonnet",
            "onboard": "sonnet",
        }
    )
    setup_cmd: str | None = None  # run in a fresh worktree (e.g. "pnpm install")

    @property
    def lane_names(self) -> list[str]:
        return list(self.lanes.keys())


DEFAULT_PROJECT_CONFIG = ProjectConfig(
    lanes={"main": LaneConfig(paths=["**"], description="Whole repo (single lane)")}
)


def project_config_path(repo: Path) -> Path:
    return Path(repo) / AGENTS_DIR / "config.json"


def load_project_config(repo: str | Path) -> ProjectConfig:
    path = project_config_path(Path(repo))
    if not path.exists():
        return DEFAULT_PROJECT_CONFIG.model_copy(deep=True)
    return ProjectConfig.model_validate(json.loads(path.read_text()))


def save_project_config(repo: str | Path, cfg: ProjectConfig) -> Path:
    path = project_config_path(Path(repo))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg.model_dump(exclude_none=True), indent=2) + "\n")
    return path


class Repo(BaseModel):
    name: str
    path: str


class ServerConfig(BaseModel):
    """`~/.god/config.json` — the explicit repo registry plus runtime limits."""

    repos: list[Repo] = Field(default_factory=list)
    max_parallel: int = 2
    runner: str = "cli"          # "cli" | "sdk"
    claude_bin: str = "claude"
    phase_timeout_s: int = 3600
    host: str = "127.0.0.1"
    port: int = 8787

    @classmethod
    def load(cls) -> "ServerConfig":
        path = god_home() / "config.json"
        if not path.exists():
            return cls()
        return cls.model_validate(json.loads(path.read_text()))

    def save(self) -> Path:
        path = god_home() / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.model_dump(), indent=2) + "\n")
        return path

    def resolve_repo(self, name_or_path: str) -> Path:
        for r in self.repos:
            if r.name == name_or_path:
                return Path(r.path).expanduser().resolve()
        p = Path(name_or_path).expanduser().resolve()
        if p.exists():
            return p
        raise FileNotFoundError(f"unknown repo: {name_or_path}")
