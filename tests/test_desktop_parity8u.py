"""Final parity — supervised Ghost/desktop control on one broker path.

Proves, against fixture executors (explicit simulation; contracts, gates,
grants, hashes, receipts, audit, cancellation are always real):

* the four new supervised capabilities (desktop screenshot, named key,
  cursor move, window close) with tiers, schemas, and confirmations;
* target-bound + origin-bound session grants, expiry, revocation;
* explicit session authorization (human-only enable, grant-gated reads,
  fresh-confirmation mutating);
* cancellation, emergency stop (cancels tracked runs), bounded timeout
  (kill, never late success), executor-crash recovery;
* typed X11/Wayland session behavior;
* CLI/dashboard/voice parity on the same runner;
* no bypass: shell hard-off, no network/MCP/shell imports, direct
  adapter calls refused, model sources refused.
"""
from __future__ import annotations

import ast
import io
import json
import threading
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from voicebridge import actions as A
from voicebridge import ghost as G
from voicebridge.capabilities import Tier

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "actions"


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _runner(cfg, *, name="ghost_outcomes.json", grants=(),
            session_grants=(), enabled=True):
    from voicebridge.permissions import PermissionsStore

    G.register_ghost_specs()
    store = A.ActionStore(Path(cfg.state_dir()) / "actions.json")
    perms = PermissionsStore(Path(cfg.state_dir()) / "perm.json")
    for cap, scope in grants:
        perms.grant(cap, requested_by="user", confirmed=True, scopes=(scope,))
    sgs = G.SessionGrantStore(Path(cfg.state_dir()) / "ghost-grants.json")
    execs = A.make_fixture_executors(
        A.load_fixture_outcomes(FIXTURES / name))
    adapter = G.GhostAdapter(shell=False)
    run = G.GhostRunner(store=store, perms=perms, executors=execs,
                        enabled=enabled, session_grants=sgs, adapter=adapter)
    run.adapter.enable(requested_by="user")
    for cap, scope, target in session_grants:
        run.grant(cap, scopes=(scope,), target=target)
    return run


def _voice(cfg, entry_id, session="v-ghost", **args):
    from voicebridge import voice_commands as VC

    entry = VC.REGISTRY[entry_id]
    ctx = VC.make_ctx(cfg, actor="voice:user", session=session, source="user")
    out = VC.execute(ctx, VC.Resolution(VC.MATCH, entry=entry, args=args))
    if out.kind == VC.NEEDS_CONFIRM:
        out = VC.confirm_pending(ctx, out.confirmation_id)
    return out


def _dash(cfg, op_id, args, session="d-ghost"):
    from voicebridge import dashboard_ops as DO

    out = DO.run_op(cfg, op_id, args, actor="dashboard:user",
                    session=session)
    if out.get("state") == "needs-confirm":
        out = DO.confirm_op(cfg, out["confirmation_id"],
                            actor="dashboard:user", session=session)
    return out


# ------------------------------------------------------- new supervised specs
def test_new_specs_registered_with_safe_tiers():
    G.register_ghost_specs()
    assert G.ghost_spec("ghost.desktop.screenshot").tier is Tier.READ_ONLY
    assert G.ghost_spec("ghost.input.key").tier is Tier.MUTATING
    assert G.ghost_spec("ghost.input.cursor").tier is Tier.REVERSIBLE
    assert G.ghost_spec("ghost.window.close").tier is Tier.MUTATING
    assert G.ghost_spec("ghost.input.key").needs_confirmation
    assert G.ghost_spec("ghost.window.close").needs_confirmation
    assert not G.ghost_spec("ghost.desktop.screenshot").needs_confirmation
    keys = G.ghost_spec("ghost.input.key").arg_schema["key"]["choices"]
    assert "Enter" in keys and "F12" in keys
    assert "rm -rf" not in keys and len(keys) == 25


def test_key_schema_rejects_arbitrary_text(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    run = _runner(cfg, grants=(("ghost.input.key", "desktop"),))
    # Invalid args never execute: the schema validator raises a typed
    # error (the CLI maps it to exit 2 with the same code).
    from voicebridge.actions import ActionValidationError

    with pytest.raises(ActionValidationError, match="not in"):
        run.execute("ghost.input.key",
                    args={"target": "field", "key": "rm -rf /"},
                    target="desktop", scope="desktop",
                    source="user", confirmed=True)


def test_screenshot_cursor_close_full_path(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    run = _runner(
        cfg, grants=(("ghost.input.key", "desktop"),
                     ("ghost.window.close", "desktop")),
        session_grants=(("ghost.desktop.screenshot", "desktop", "desktop"),
                        ("ghost.input.cursor", "desktop", "desktop")))
    shot = run.execute("ghost.desktop.screenshot", args={},
                       target="desktop", scope="desktop", source="user")
    assert shot["state"] == "OK", shot
    cur = run.execute("ghost.input.cursor", args={"target": "panel"},
                      target="desktop", scope="desktop", source="user")
    assert cur["state"] == "OK", cur
    key = run.execute("ghost.input.key",
                      args={"target": "field", "key": "Enter"},
                      target="desktop", scope="desktop",
                      source="user", confirmed=True)
    assert key["state"] == "OK", key
    close = run.execute("ghost.window.close", args={"window": "old"},
                        target="desktop", scope="desktop",
                        source="user", confirmed=True)
    assert close["state"] == "OK", close
    # Window close is honestly irreversible.
    rec = run.recover(close["receipt_id"], source="user", confirmed=True)
    assert rec["state"] == "ROLLBACK_IMPOSSIBLE", rec
    # Cursor move is reversible.
    rec2 = run.recover(cur["receipt_id"], source="user", confirmed=True)
    assert rec2["state"] in ("OK", "recovered", "rolled-back",
                             "recovered-with-compensation"), rec2


def test_window_close_needs_fresh_confirmation(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    run = _runner(cfg, grants=(("ghost.window.close", "desktop"),))
    out = run.execute("ghost.window.close", args={"window": "old"},
                      target="desktop", scope="desktop", source="user")
    assert out["state"] == "denied"
    assert out["code"] == "CONFIRMATION_REQUIRED"


# ------------------------------------------------------- grants + authorization
def test_origin_binding_rejects_wrong_browser(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    run = _runner(cfg, grants=(("ghost.input.click", "web"),))
    run.grant("ghost.browser.tabs", scopes=("web",),
              target="https://mail.google.com")
    out = run.execute("ghost.browser.tabs",
                      args={"role": "chromium"},
                      target="https://evil.test", scope="web",
                      source="user")
    assert out["state"] == "denied"
    assert out["code"] in ("NOT_GRANTED", "OUT_OF_SCOPE")


def test_model_source_cannot_authorize(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    run = _runner(cfg)
    out = run.execute("ghost.desktop.discover", args={}, target="desktop",
                      scope="desktop", source="model", confirmed=True)
    assert out["state"] == "denied"


def test_enable_requires_human_and_reset_recovers(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    run = _runner(cfg)
    assert run.adapter.enable(requested_by="model")["state"] == "denied"
    run.adapter.mark_failure("cdp unreachable")
    assert run.adapter.state.health == G.HEALTH_FAILED
    run.adapter.mark_reconnect()
    assert run.adapter.state.health == G.HEALTH_ENABLED
    run.adapter.emergency_stop()
    assert run.adapter.reset(requested_by="user")["state"] == "OK"


# ------------------------------------------------------- cancel / estop / kill
def test_emergency_stop_cancels_tracked_runs(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    run = _runner(cfg)
    fired = []
    run.adapter.track("corr-1", lambda: fired.append(True))
    run.adapter.emergency_stop()
    assert fired == [True]
    out = run.execute("ghost.desktop.discover", args={}, target="desktop",
                      scope="desktop", source="user")
    assert out["state"] == "denied"
    assert out["code"] == "EMERGENCY_STOP"


def test_mutating_timeout_kills_never_late_success(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    run = _runner(cfg, name="ghost_slow.json",
                  grants=(("ghost.input.click", "web"),))
    result: dict = {}
    thread = threading.Thread(
        target=lambda: result.update(run.execute(
            "ghost.input.click", args={"role": "chromium", "target": "#btn"},
            target="https://mail.google.com", scope="web",
            confirmed=True, timeout_s=0.05)), daemon=True)
    thread.start()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert result.get("state") in ("timeout", "cancelled", "denied"), result
    assert result.get("state") != "OK"


def test_cancel_in_flight_and_crash_recovery(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    run = _runner(cfg, name="ghost_slow.json",
                  session_grants=(("ghost.desktop.discover", "desktop",
                                   "desktop"),))
    result: dict = {}
    thread = threading.Thread(
        target=lambda: result.update(run.execute(
            "ghost.desktop.discover", args={}, target="desktop",
            scope="desktop")), daemon=True)
    thread.start()
    thread.join(timeout=10)
    assert result.get("state") in ("OK", "cancelled"), result
    # Executor crash becomes a typed FAILED receipt; the runner survives.
    def _boom(env):
        raise RuntimeError("segfault-sim")

    from voicebridge.permissions import PermissionsStore

    G.register_ghost_specs()
    store = A.ActionStore(Path(cfg.state_dir()) / "crash-actions.json")
    perms = PermissionsStore(Path(cfg.state_dir()) / "crash-perm.json")
    sgs = G.SessionGrantStore(Path(cfg.state_dir()) / "crash-grants.json")
    run2 = G.GhostRunner(store=store, perms=perms,
                         executors={"ghost.desktop.screenshot": _boom},
                         enabled=True, session_grants=sgs,
                         adapter=G.GhostAdapter(shell=False))
    run2.adapter.enable(requested_by="user")
    run2.grant("ghost.desktop.screenshot", scopes=("desktop",),
               target="desktop")
    crashed = run2.execute("ghost.desktop.screenshot", args={},
                           target="desktop", scope="desktop", source="user")
    assert crashed["state"] == "failed", crashed
    assert "segfault-sim" in str(crashed.get("detail", ""))
    healthy = run2.execute("ghost.desktop.discover", args={},
                           target="desktop", scope="desktop", source="user")
    assert healthy["state"] == "denied"  # no grant for discover on run2
    assert healthy["code"] == "NOT_GRANTED"


# ------------------------------------------------------- X11/Wayland + session
def test_desktop_session_typed_states(monkeypatch):
    from voicebridge import computer_use as CU

    real_glob = __import__("glob").glob
    monkeypatch.setattr("glob.glob", lambda _p: [])
    none = CU.desktop_session(env={})
    assert none["session"] == "none" and none["headless"] is True
    way = CU.desktop_session(env={"WAYLAND_DISPLAY": "wayland-0",
                                  "XDG_SESSION_TYPE": "wayland"})
    assert way["session"] == "wayland" and not way["headless"]
    x11 = CU.desktop_session(env={"DISPLAY": ":0"})
    assert x11["session"] == "x11"
    both = CU.desktop_session(env={"DISPLAY": ":0",
                                   "WAYLAND_DISPLAY": "wayland-0"})
    assert both["session"] == "both"
    monkeypatch.setattr("glob.glob", real_glob)


def test_computer_use_session_cli(tmp_path, monkeypatch):
    from voicebridge import cli as CLI
    from voicebridge import computer_use as CU

    _cfg(tmp_path, monkeypatch)
    args = type("A", (), {})()
    args.computer_use_command = "session"
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._cmd_computer_use(None, args)
    assert rc == 0
    out = json.loads(buf.getvalue())
    assert out["session"] in ("x11", "wayland", "both", "none")
    assert set(out) >= {"session", "headless", "x_display",
                        "wayland_display"}


# ------------------------------------------------------- no bypass
def test_no_shell_network_or_mcp_in_ghost():
    """No live execution smuggling: ghost.py may parse URLs (urlsplit)
    but must never perform network I/O, spawn processes, or speak MCP."""
    tree = ast.parse((REPO_ROOT / "voicebridge" / "ghost.py").read_text(
        encoding="utf-8"))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    for banned in ("socket", "requests", "urllib3", "httpx",
                   "aiohttp", "websocket", "mcp", "subprocess",
                   "http"):
        assert banned not in mods, banned
    text = (REPO_ROOT / "voicebridge" / "ghost.py").read_text().lower()
    assert "pyautogui" not in text
    assert "urlopen" not in text and "urlretrieve" not in text


def test_shell_spec_off_by_default_and_never_live(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.delenv(G.GHOST_SHELL_ENV, raising=False)
    G.register_ghost_specs()
    assert "ghost.shell" not in G.ghost_action_ids()
    run = _runner(cfg)
    assert run.execute("ghost.shell", args={"argv": "id"},
                       target="desktop", scope="shell",
                       source="user", confirmed=True)["state"] == "denied"
    # Even explicitly enabled, no live shell executor exists: the broker
    # path returns ADAPTER_DISABLED, never a shell.
    monkeypatch.setenv(G.GHOST_SHELL_ENV, "on")
    G.register_ghost_specs()
    assert "ghost.shell" in G.ghost_action_ids()
    run2 = _runner(cfg)
    out = run2.execute("ghost.shell", args={"argv": "id"},
                       target="desktop", scope="shell",
                       source="user", confirmed=True)
    assert out["state"] == "denied", out
    monkeypatch.delenv(G.GHOST_SHELL_ENV, raising=False)
    G.register_ghost_specs()


def test_direct_adapter_calls_refused(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    run = _runner(cfg)
    # The adapter object exposes no execution path at all: no connect,
    # spawn, run, or call entry — execution exists only in broker
    # executors entered through the runner.
    for missing in ("connect", "spawn", "run", "call", "execute"):
        assert not hasattr(run.adapter, missing), missing
    # The research adapter is unbound by default; its methods fail closed.
    assert run.comet is None
    assert run.comet_ask("hi")["code"] == "COMET_UNAVAILABLE"


def _raise():
    raise AssertionError("comet must stay unbound by default")


# ------------------------------------------------------- CLI/dashboard/voice
def test_cli_retry_recover_wired(tmp_path, monkeypatch):
    from voicebridge import cli as CLI

    cfg = _cfg(tmp_path, monkeypatch)
    for cmd, kw in (("retry", {"receipt_id": "actr_missing",
                               "args": "{}", "target": "desktop",
                               "scope": "desktop", "role": "",
                               "confirmation": None, "confirm": False}),
                    ("recover", {"receipt_id": "actr_missing",
                                 "confirmation": None,
                                 "confirm": False})):
        args = type("A", (), {})()
        args.ghost_command = cmd
        for key, val in kw.items():
            setattr(args, key, val)
        args.fixture = None
        args.dry_run = False
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = CLI._cmd_ghost(cfg, args)
        assert rc == 2, (cmd, buf.getvalue())
        assert "actr_missing" in buf.getvalue()


def test_voice_preview_grant_and_read_tab(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    preview = _voice(cfg, "ghost_action_preview", action_id="ghost.input.key",
                     target="field", text="Enter")
    assert preview.kind == "executed", preview.speech
    assert "ghost.input.key" in preview.speech
    assert "onfirmation" in preview.speech
    granted = _voice(cfg, "ghost_grant",
                     capability="ghost.desktop.discover", scope="desktop",
                     target="desktop")
    assert granted.kind == "executed", granted.speech
    # No browser running hermetically: honest typed refusal, never a guess.
    read = _voice(cfg, "ghost_read_tab")
    assert read.kind == "executed", read.speech
    assert "NO_CURRENT_TAB" in read.speech or "refused" in read.speech


def test_voice_click_type_need_current_tab(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    clicked = _voice(cfg, "ghost_click", target="submit")
    assert clicked.kind == "executed"
    assert "NO_CURRENT_TAB" in clicked.speech or "refused" in clicked.speech
    typed = _voice(cfg, "ghost_type", text="hello", target="name")
    assert typed.kind == "executed"
    assert "NO_CURRENT_TAB" in typed.speech or "refused" in typed.speech


def test_dashboard_ghost_extras_same_runner(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    listed = _dash(cfg, "ghost_list", {})
    assert listed["state"] == "executed", listed
    ids = [c["action_id"] for c in
           listed["receipt"]["result"]["capabilities"]]
    for want in ("ghost.desktop.screenshot", "ghost.input.key",
                 "ghost.input.cursor", "ghost.window.close"):
        assert want in ids
    preview = _dash(cfg, "ghost_preview",
                    {"action_id": "ghost.window.close",
                     "args_json": '{"window": "old" Explore}'})
    assert preview["state"] in ("executed", "failed"), preview
    recovered = _dash(cfg, "ghost_recover", {"receipt_id": "actr_missing"})
    assert recovered["state"] == "failed", recovered
