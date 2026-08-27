"""Folder picking and cloning."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from ccgod import browse
from ccgod.config import ServerConfig
from ccgod.service import Service


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def tree(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("CCGOD_HOME", str(tmp_path / "home"))
    root = tmp_path / "dev"
    (root / "plain").mkdir(parents=True)
    (root / "node_modules").mkdir()
    (root / ".hidden").mkdir()
    (root / "a-repo").mkdir()
    git("init", "-q", "-b", "main", cwd=root / "a-repo")
    (root / "onboarded").mkdir()
    git("init", "-q", "-b", "main", cwd=root / "onboarded")
    (root / "onboarded" / ".agents").mkdir()
    (root / "onboarded" / ".agents" / "project.md").write_text("# p\n")
    (root / "a-repo" / "file.txt").write_text("x")   # files are not listed
    return root


def test_lists_only_directories_and_skips_noise(tree):
    listing = browse.listdir(tree, [])
    names = [e.name for e in listing.entries]
    assert names == ["a-repo", "onboarded", "plain"]
    assert "node_modules" not in names     # in the skip list
    assert ".hidden" not in names          # hidden by default


def test_hidden_can_be_shown(tree):
    names = [e.name for e in browse.listdir(tree, [], show_hidden=True).entries]
    assert ".hidden" in names


def test_labels_git_registered_and_onboarded(tree):
    listing = browse.listdir(tree, [tree / "a-repo"])
    by = {e.name: e for e in listing.entries}
    assert by["a-repo"].is_git and by["a-repo"].is_registered
    assert by["onboarded"].is_git and by["onboarded"].is_onboarded
    assert not by["onboarded"].is_registered
    assert not by["plain"].is_git


def test_listing_describes_the_directory_itself(tree):
    listing = browse.listdir(tree / "onboarded", [])
    assert listing.is_git and listing.is_onboarded
    assert listing.parent == str(tree)


def test_bad_paths_raise_distinctly(tree):
    with pytest.raises(FileNotFoundError):
        browse.listdir(tree / "nope", [])
    with pytest.raises(NotADirectoryError):
        browse.listdir(tree / "a-repo" / "file.txt", [])


def test_roots_include_home_and_registered_parents(tree):
    entries = browse.roots([tree / "a-repo"])
    paths = [e.path for e in entries]
    assert str(Path.home()) in paths
    assert str(tree) in paths           # parent of the registered repo


# ---- clone -----------------------------------------------------------
@pytest.fixture
def origin(tmp_path) -> Path:
    src = tmp_path / "origin"
    src.mkdir()
    (src / "hello.txt").write_text("hi\n")
    git("init", "-q", "-b", "main", cwd=src)
    git("config", "user.email", "t@t", cwd=src)
    git("config", "user.name", "t", cwd=src)
    git("add", "-A", cwd=src)
    git("commit", "-qm", "init", cwd=src)
    return src


def svc(tmp_path) -> Service:
    return Service(ServerConfig(repos=[], clone_root=str(tmp_path / "cloned")))


async def test_clone_registers_the_repo(tmp_path, origin, monkeypatch):
    monkeypatch.setenv("CCGOD_HOME", str(tmp_path / "home"))
    s = svc(tmp_path)
    res = await s.clone(str(origin))
    assert Path(res["path"]).name == "origin"
    assert (Path(res["path"]) / "hello.txt").exists()
    assert res["onboarded"] is False
    assert "ccgod init" in res["next"]
    assert [r.name for r in s.cfg.repos] == ["origin"]


async def test_clone_into_an_existing_dir_nests_under_it(tmp_path, origin, monkeypatch):
    monkeypatch.setenv("CCGOD_HOME", str(tmp_path / "home"))
    target = tmp_path / "workspace"
    target.mkdir()
    res = await svc(tmp_path).clone(str(origin), into=str(target))
    assert Path(res["path"]) == target / "origin"


async def test_clone_refuses_an_existing_target(tmp_path, origin, monkeypatch):
    monkeypatch.setenv("CCGOD_HOME", str(tmp_path / "home"))
    s = svc(tmp_path)
    await s.clone(str(origin))
    with pytest.raises(FileExistsError):
        await s.clone(str(origin))


async def test_clone_rejects_junk_urls(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGOD_HOME", str(tmp_path / "home"))
    s = svc(tmp_path)
    with pytest.raises(ValueError):
        await s.clone("   ")
    with pytest.raises(ValueError):
        await s.clone("--upload-pack=evil")     # never let a URL become a git flag


async def test_clone_surfaces_git_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGOD_HOME", str(tmp_path / "home"))
    from ccgod.worktree import GitError

    with pytest.raises(GitError):
        await svc(tmp_path).clone(str(tmp_path / "does-not-exist"))


async def test_clone_takes_an_explicit_name(tmp_path, origin, monkeypatch):
    monkeypatch.setenv("CCGOD_HOME", str(tmp_path / "home"))
    s = svc(tmp_path)
    res = await s.clone(str(origin), name="mine")
    assert res["name"] == "mine"
    assert [r.name for r in s.cfg.repos] == ["mine"]
