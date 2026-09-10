"""Packet 8T-F — REJECTED matrix rows stay rejected (regression).

Each test proves a rejected upstream capability cannot silently re-enter:
no code, dependency, entry point, or fetch path exists for it, and the
typed refusal fires where a user could request it.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

NETWORK_IMPORTS = {"requests", "urllib.request", "urllib3", "httpx",
                   "aiohttp", "socket", "websocket"}


def _imports_of(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
    return found


def test_rejected_cloud_memory_local_only(tmp_path, monkeypatch):
    """H-cloudmem: memory never leaves the machine (no network imports in
    the memory store; SQLite lives under the Qubit state dir)."""
    for mod in ("store.py", "semantic.py", "__init__.py"):
        p = REPO_ROOT / "voicebridge" / "memory" / mod
        if p.exists():
            assert not (_imports_of(p) & NETWORK_IMPORTS), (mod,
                _imports_of(p) & NETWORK_IMPORTS)
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    cfg = C.Config.load(tmp_path / "voice.yaml")
    from voicebridge.memory.store import MemoryStore

    store = MemoryStore(Path(cfg.state_dir()) / "memory" / "memory.db")
    try:
        assert str(store._path).startswith(str(cfg.state_dir()))
    finally:
        try:
            store.close()
        except Exception:
            pass


def test_rejected_desktop_shell_absent():
    """H-desktop: no Electron shell, no desktop actuators — probes only."""
    text = (REPO_ROOT / "voicebridge" / "computer_use.py").read_text()
    for token in ("pyautogui", "pynput", "xdotool", "wmctrl", "electron",
                  "def click", "def type_text", "def keypress"):
        assert token not in text.lower(), token
    from voicebridge import computer_use as CU

    assert CU.status()["control"] == "DISABLED"
    assert CU.refuse_install()["code"] == "COMPUTER_USE_NO_DRIVER"


def test_rejected_cloud_browser_absent():
    """H-cloudbrowser: no third-party browser service; loopback CDP probe
    only, personal profiles never touched."""
    text = (REPO_ROOT / "voicebridge" / "browser.py").read_text()
    for token in ("camofox", "cloud", "upload", "remote_debug", "s3",
                  "presigned"):
        assert token not in text.lower(), token
    from voicebridge import browser as BW

    refused = BW.refuse_close_profile("Default")
    assert refused["code"] == "BROWSER_CLOSE_REFUSED"


def test_rejected_installer_qubit_owned():
    """H-installer: packaging/install identity is Qubit; the Hermes
    installer is never invoked (provision script installs the pinned
    runtime into Qubit-owned XDG space, never a system install)."""
    from voicebridge import foundation as F

    assert F.INSTALL_IDENTITY == "qubit-voicebridge"
    install_sh = (REPO_ROOT / "scripts" / "install.sh").read_text()
    assert "hermes" not in install_sh.lower() or \
        "does NOT touch" in install_sh or "do not" in install_sh.lower(), \
        "installer must not drive a Hermes install"
    prov = (REPO_ROOT / "scripts" / "install_hermes_runtime.sh").read_text()
    assert "operator" in prov.lower() and "never" in prov.lower()
    from voicebridge.runtime import provision as P

    assert P.HERMES_COMMIT == "2237be355906fbe6065ce1815711eee52b2d646e"


def test_rejected_clawhub_remote_refused(tmp_path, monkeypatch):
    """O-clawhub: remote plugin identifiers are never fetched — CLI,
    dashboard, and voice all typed-refuse with BLOCKED_REMOTE_PLUGIN."""
    from voicebridge import plugins as PLUG

    for spec in ("clawhub://skill", "https://example.com/skill.zip"):
        verdict = PLUG.classify_install_source(spec)
        assert verdict["kind"] == "remote", (spec, verdict)
        refused = PLUG.refuse_remote(verdict["identifier"])
        assert refused["code"] == "BLOCKED_REMOTE_PLUGIN", refused
    # A schemeless string is a local path, never fetched: missing files
    # refuse without network access.
    verdict = PLUG.classify_install_source("github.com/someone/skills")
    assert verdict["kind"] == "missing-file", verdict
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    cfg = C.Config.load(tmp_path / "voice.yaml")
    from voicebridge import dashboard_ops as OPS

    out = OPS.run_op(cfg, "plugins_remote",
                     {"identifier": "github.com/someone/skills"})
    assert out["receipt"]["result"]["code"] == "BLOCKED_REMOTE_PLUGIN"
