"""9U — OpenClaw worktrees-family handlers are real confined git delegates.

Two independent proofs:

1. static (AST) — every method this family registers is reached by the
   manifest's own behavior proof, and each typed ``SAFE_EQUIVALENT`` refusal
   actually emits its declared code;
2. runtime — the live handlers run in-process against a temp git repo
   registered as a Qubit project and their real behavior (confinement,
   confirmation gating, typed codes) is asserted directly.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    __import__("shutil").which("git") is None, reason="git not installed")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import openclaw_upgrade_manifest as OUM  # noqa: E402

FAMILY_MODULE = "voicebridge/gateway/openclaw_handlers/worktrees.py"

#: The typed SAFE_EQUIVALENT codes this family emits (method -> code).
SAFE_EQUIVALENT = {
    "worktrees.restore": "WORKTREES_RESTORE_LOCAL_READD",
    "worktrees.gc": "WORKTREES_GC_LOCAL_PRUNE",
}

#: The real IMPLEMENTED handlers this family adds.
IMPLEMENTED = (
    "worktrees.list", "worktrees.branches",
    "worktrees.create", "worktrees.remove",
)


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _repo(tmp_path, name="work"):
    root = tmp_path / name
    root.mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"],
                   check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"],
                   check=True)
    (root / "notes.md").write_text("hello\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "init"],
                   check=True)
    return root


def _project(cfg, folder):
    from voicebridge import projects as PJ

    created = PJ.create(cfg, "Work", [str(folder)],
                        requested_by="t", confirmed=True, use=True)
    assert created.get("state") == "OK", created


def _registry():
    from voicebridge.gateway import rpc as R
    return R.default_registry()


# ---------------------------------------------------------------- static proof
def test_registered_implementations_pass_manifest_behavior_proof():
    sources = OUM.registry_sources(REPO_ROOT / "voicebridge" / "gateway"
                                   / "rpc.py")
    methods, funcs, consts, origins = OUM._parse_rpc_handlers_multi(sources)
    registry = set(_registry().names())
    ours = {m for m, origin in origins.items()
            if origin.endswith("openclaw_handlers/worktrees.py")}
    assert ours == set(IMPLEMENTED) | set(SAFE_EQUIVALENT), sorted(ours)
    assert ours <= registry, f"unregistered: {ours - registry}"
    for method in sorted(ours):
        node = methods[method]
        if method in SAFE_EQUIVALENT:
            assert OUM._emits_code(node, funcs, SAFE_EQUIVALENT[method],
                                   consts), (
                f"{method} never emits its declared code "
                f"{SAFE_EQUIVALENT[method]!r}")
        else:
            assert OUM._is_non_trivial(node, funcs), (
                f"{method} is registered as an implementation but does no "
                "backend work")


def test_manifest_dispositions_match_family(tmp_path, monkeypatch):
    import json

    manifest = json.loads((REPO_ROOT / "docs" / "archive" / "2026-09-21"
                           / "QUBIT_OPENCLAW_UPGRADE_MANIFEST.json"
                           ).read_text())
    rows = manifest["pinned_surface_disposition"]["methods"]
    for method in IMPLEMENTED:
        assert rows[method]["status"] == "IMPLEMENTED", rows[method]
    for method, code in SAFE_EQUIVALENT.items():
        assert rows[method]["status"] == "SAFE_EQUIVALENT", rows[method]
        assert rows[method]["code"] == code, rows[method]


# --------------------------------------------------------------- runtime proof
def test_list_and_branches_report_real_repo(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    root = _repo(tmp_path)
    _project(cfg, root)
    reg = _registry()

    out = reg.handle(cfg, "worktrees.list", {})
    assert out["state"] == "OK" and out["root"] == str(root)
    assert any(t["isMain"] and t["path"] == str(root)
               for t in out["worktrees"]), out

    branches = reg.handle(cfg, "worktrees.branches", {})
    assert branches["state"] == "OK"
    names = {b["name"] for b in branches["branches"]}
    assert "master" in names or "main" in names, branches
    assert any(b["checkedOut"] for b in branches["branches"])


def test_create_requires_confirmation_and_is_confined(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    root = _repo(tmp_path)
    _project(cfg, root)
    reg = _registry()

    staged = reg.handle(cfg, "worktrees.create",
                        {"name": "wt1", "repoRoot": str(root)})
    assert staged["state"] == "CONFIRMATION_REQUIRED", staged
    assert not (root / ".worktrees").exists()

    made = reg.handle(cfg, "worktrees.create",
                      {"name": "wt1", "repoRoot": str(root),
                       "confirm": True})
    assert made["state"] == "OK", made
    target = root / ".worktrees" / "wt1"
    assert target.is_dir() and made["path"] == str(target)

    listed = reg.handle(cfg, "worktrees.list", {"repoRoot": str(root)})
    paths = {t["path"] for t in listed["worktrees"]}
    assert str(target) in paths, listed


def test_create_rejects_bad_and_escaping_names(tmp_path, monkeypatch):
    from voicebridge.gateway.gateway_ws import GatewayError

    cfg = _cfg(tmp_path, monkeypatch)
    root = _repo(tmp_path)
    _project(cfg, root)
    reg = _registry()

    with pytest.raises(GatewayError) as exc:
        reg.handle(cfg, "worktrees.create",
                   {"name": "../escape", "repoRoot": str(root),
                    "confirm": True})
    assert exc.value.code == "GATEWAY_BAD_PARAMS"


def test_remove_and_gc_and_restore_round_trip(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    root = _repo(tmp_path)
    _project(cfg, root)
    reg = _registry()

    made = reg.handle(cfg, "worktrees.create",
                      {"name": "wt2", "repoRoot": str(root),
                       "confirm": True})
    assert made["state"] == "OK", made

    staged = reg.handle(cfg, "worktrees.remove",
                        {"name": "wt2", "repoRoot": str(root)})
    assert staged["state"] == "CONFIRMATION_REQUIRED", staged

    removed = reg.handle(cfg, "worktrees.remove",
                         {"name": "wt2", "repoRoot": str(root),
                          "confirm": True})
    assert removed["state"] == "OK" and removed["removed"] is True, removed
    assert not (root / ".worktrees" / "wt2").exists()

    restored = reg.handle(cfg, "worktrees.restore",
                          {"name": "wt2", "branch": "wt2",
                           "repoRoot": str(root), "confirm": True})
    assert restored["state"] == "OK", restored
    assert restored["code"] == "WORKTREES_RESTORE_LOCAL_READD"
    assert (root / ".worktrees" / "wt2").is_dir()

    gc = reg.handle(cfg, "worktrees.gc", {"repoRoot": str(root)})
    assert gc["state"] == "OK"
    assert gc["code"] == "WORKTREES_GC_LOCAL_PRUNE"
