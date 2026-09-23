"""Wave 6 — first-class/guarded coverage for the passthrough-only CLI families.

Before this wave, 24 Hermes top-level families were reachable only through the
raw ``qubit agent <args>`` passthrough (which runs the vendored fork outside
Qubit policy) or fell through to argparse ``invalid choice``. This wave gives
each one a typed route:

* read-only/diagnostic subcommands forward into the vendored fork verbatim;
* mutating/credentialed subcommands become ``needs-confirm`` and are never
  invoked until Qubit's ``--confirm`` passes the shared dispatch guard;
* families Qubit owns natively (or that are interactive/credentialed with no
  local read-only surface) return a typed refusal naming the Qubit-native
  equivalent — never a raw forward;
* OpenClaw-only families (not reachable through the Hermes fork at all) return
  a typed native-equivalent / operator-only / missing envelope and are never
  routed through ``qubit agent``.

Every case executes the real production entrypoint :func:`voicebridge.cli.main`
and asserts the fork is not reached unless a classified route allows it.
"""
from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge import cli as CLI  # noqa: E402
from voicebridge import cli_qubit_forward as HF  # noqa: E402
from voicebridge import qubit_runtime  # noqa: E402

# The 24 families that were passthrough-only before this wave.
# `chat` graduated in a W4 follow-up from a typed refusal to a first-class
# classified forward to the vendored interactive chat
# (`cli_qubit_forward.chat_route`, covered by `tests/test_cli_chat_native9u.py`),
# so it is no longer asserted as a refused family here.
HERMES_FAMILIES = (
    "curator", "dashboard", "fallback", "import", "insights",
    "journey", "learning", "login", "logout", "migrate", "monitoring",
    "pets", "resume", "secrets", "security", "send", "serve", "skin",
    "slack", "uninstall", "whatsapp", "whatsapp-cloud",
)

# Owner Decision #11: `update` graduated from a typed Wave-6 refusal to a
# native Qubit parser family (`qubit update --check|--apply|--rollback`). It is
# tracked separately so it is still covered without being treated as a refusal.
NATIVE_FAMILY_UPDATE = "update"

# Families added to FAMILY_FORWARD this wave (read-only/confirm classified).
NEW_FAMILIES = (
    "curator", "fallback", "import", "insights", "journey", "learning",
    "migrate", "monitoring", "pets", "secrets", "security", "skin", "slack",
)

READONLY_FORWARDS = (
    (["curator", "status"], ["curator", "status"]),
    (["curator", "ledger"], ["curator", "ledger"]),
    (["curator", "usage"], ["curator", "usage"]),
    (["curator", "list-archived"], ["curator", "list-archived"]),
    (["curator", "list-unmanaged"], ["curator", "list-unmanaged"]),
    (["security", "audit"], ["security", "audit"]),
    (["security"], ["security"]),
    (["skin", "list"], ["skin", "list"]),
    (["fallback", "list"], ["fallback", "list"]),
    (["fallback", "ls"], ["fallback", "ls"]),
    (["monitoring", "status"], ["monitoring", "status"]),
    (["monitoring"], ["monitoring"]),
    (["insights"], ["insights"]),
    (["journey", "list"], ["journey", "list"]),
    (["learning", "list"], ["learning", "list"]),
    (["pets", "list"], ["pets", "list"]),
    (["pets", "doctor"], ["pets", "doctor"]),
    (["pets", "show"], ["pets", "show"]),
    (["migrate"], ["migrate"]),
)

CONFIRM_FORWARDS = (
    (["curator", "run"], ["curator", "run"]),
    (["curator", "prune"], ["curator", "prune"]),
    (["curator", "rollback"], ["curator", "rollback"]),
    (["journey", "delete", "node-1"], ["journey", "delete", "node-1"]),
    (["learning", "edit", "node-1"], ["learning", "edit", "node-1"]),
    (["pets", "install", "boba"], ["pets", "install", "boba"]),
    (["pets", "select", "boba"], ["pets", "select", "boba"]),
    (["secrets", "bitwarden"], ["secrets", "bitwarden"]),
    (["skin", "use", "dark"], ["skin", "use", "dark"]),
    (["skin", "set", "accent", "#ffffff"], ["skin", "set", "accent", "#ffffff"]),
    (["slack", "manifest"], ["slack", "manifest"]),
    (["fallback", "add"], ["fallback", "add"]),
    (["fallback", "clear"], ["fallback", "clear"]),
    (["migrate", "xai"], ["migrate", "xai"]),
    (["import", "/tmp/backup.zip"], ["import", "/tmp/backup.zip"]),
)

NATIVE_REFUSED = (
    ("dashboard", "QUBIT_NATIVE_DASHBOARD"),
    ("login", "QUBIT_NATIVE_LOGIN"),
    ("logout", "QUBIT_NATIVE_LOGOUT"),
    ("resume", "QUBIT_NATIVE_RESUME"),
    ("send", "QUBIT_NATIVE_SEND"),
    ("serve", "QUBIT_NATIVE_SERVE"),
    ("uninstall", "QUBIT_NATIVE_UNINSTALL"),
    ("whatsapp", "QUBIT_NATIVE_WHATSAPP"),
    ("whatsapp-cloud", "QUBIT_NATIVE_WHATSAPP_CLOUD"),
)

OPENCLAW_FAMILIES = {
    "agents": HF.OPENCLAW_NATIVE_EQUIVALENT,
    "audit": HF.OPENCLAW_NATIVE_EQUIVALENT,
    "devices": HF.OPENCLAW_NATIVE_EQUIVALENT,
    "nodes": HF.OPENCLAW_NATIVE_EQUIVALENT,
    "onboard": HF.OPENCLAW_NATIVE_EQUIVALENT,
    "sandbox": HF.OPENCLAW_OPERATOR_ONLY,
    "system": HF.OPENCLAW_NATIVE_EQUIVALENT,
    "terminal": HF.OPENCLAW_NATIVE_EQUIVALENT,
    "worktrees": HF.OPENCLAW_NATIVE_EQUIVALENT,
}

# The mutating side of each new family: none of these may be classed read-only.
MUTATING_SUBS = {
    "curator": {"run", "pause", "resume", "pin", "unpin", "adopt", "restore",
                "archive", "prune", "backup", "rollback", "purge"},
    "journey": {"delete", "edit"},
    "learning": {"delete", "edit"},
    "pets": {"install", "select", "off", "scale", "remove"},
    "secrets": {"bitwarden", "bw", "onepassword", "op", "1password"},
    "skin": {"use", "set"},
    "slack": {"manifest"},
    "fallback": {"add", "remove", "rm", "clear"},
    "migrate": {"xai"},
    "import": set(),  # wildcard confirm (ANY) covers every positional
}


# The wave6 fork-only families plus the wave1 native families that route_family
# consults together (route_family reads both tables).
MERGED_FAMILY_FORWARD = {**HF.FAMILY_FORWARD, **HF.FAMILY_FORWARD_WAVE6}


def _capture(monkeypatch):
    calls = []

    def _fake_run(argv=None):
        calls.append(list(argv or []))
        return 0

    monkeypatch.setattr(qubit_runtime, "run", _fake_run)
    return calls


def _top_level_native() -> set:
    parser = CLI.build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    return set()


# ---------------------------------------------------------------------------
# Every family is intercepted before argparse (no "invalid choice", no bypass).
# ---------------------------------------------------------------------------
def test_every_target_family_is_intercepted_not_argparsed(monkeypatch):
    calls = _capture(monkeypatch)
    for family in HERMES_FAMILIES:
        assert HF.route([family]) is not None, family
        assert HF.route([family, "__no_such_subcommand__"]) is not None, family
        err = io.StringIO()
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                rc = CLI.main([family, "__no_such_subcommand__"])
        except SystemExit as exc:  # pragma: no cover - regression guard
            pytest.fail(f"qubit {family} fell through to argparse: rc={exc.code}")
        assert rc in (0, 3), (family, rc)
        assert "invalid choice" not in err.getvalue(), (family, err.getvalue())
    # A bogus subcommand never runs the fork for any target family.
    assert calls == [], calls


# ---------------------------------------------------------------------------
# Read-only routes -> fork argv verbatim.
# ---------------------------------------------------------------------------
def test_readonly_routes_forward_fork_argv(monkeypatch):
    calls = _capture(monkeypatch)
    for qargv, fork_argv in READONLY_FORWARDS:
        calls.clear()
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = CLI.main(list(qargv))
        assert rc == 0, (qargv, rc, buf.getvalue()[:200])
        assert calls == [fork_argv], (qargv, calls)


def test_readonly_routes_are_classified_in_family_forward():
    for family, (_fork, readonly, confirm) in MERGED_FAMILY_FORWARD.items():
        if family not in NEW_FAMILIES:
            continue
        for sub in readonly:
            if sub == "":
                continue
            route = HF.route([family, sub])
            assert route is not None and route.kind == "forward", (family, sub)
        for sub in confirm:
            if sub == HF.ANY:
                continue
            route = HF.route([family, sub])
            assert route is not None and route.kind == "needs-confirm", \
                (family, sub)


# ---------------------------------------------------------------------------
# Mutating/credentialed routes -> typed needs-confirm unless --confirm.
# ---------------------------------------------------------------------------
def test_confirm_routes_require_confirm_then_forward(monkeypatch):
    calls = _capture(monkeypatch)
    for qargv, fork_argv in CONFIRM_FORWARDS:
        calls.clear()
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = CLI.main(list(qargv))
        out = buf.getvalue()
        assert rc == 3, (qargv, rc, out[:200])
        assert calls == [], (qargv, calls)
        assert json.loads(out)["code"] == "CONFIRM_REQUIRED", (qargv, out[:200])
        calls.clear()
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = CLI.main([*qargv, "--confirm"])
        assert rc == 0, (qargv, rc, buf.getvalue()[:200])
        assert calls == [fork_argv], (qargv, calls)
        assert "--confirm" not in calls[0]


def test_representative_routes_with_hermetic_tmp_config(tmp_path, monkeypatch):
    """A read-only forward and a confirm-gated mutation, on a tmp config/state."""
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME",
                "XDG_CACHE_HOME"):
        d = tmp_path / var.lower()
        d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(d))
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("QUBIT_HOME", str(home / ".hermes"))
    cfg = tmp_path / "voice.yaml"
    cfg.write_text("logging:\n  jsonl: false\n")
    calls = _capture(monkeypatch)

    with redirect_stdout(io.StringIO()):
        rc = CLI.main(["curator", "status", "--config", str(cfg)])
    assert rc == 0 and calls == [["curator", "status"]], calls

    calls.clear()
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI.main(["pets", "install", "boba", "--config", str(cfg)])
    assert rc == 3, (rc, buf.getvalue()[:200])
    assert json.loads(buf.getvalue())["code"] == "CONFIRM_REQUIRED"
    assert calls == [], calls

    calls.clear()
    with redirect_stdout(io.StringIO()):
        rc = CLI.main(["pets", "install", "boba", "--confirm",
                       "--config", str(cfg)])
    assert rc == 0 and calls == [["pets", "install", "boba"]], calls


def test_no_mutating_subcommand_is_classified_readonly():
    for family, subs in MUTATING_SUBS.items():
        _fork, readonly, confirm = MERGED_FAMILY_FORWARD[family]
        assert not (subs & readonly), (family, subs & readonly)
        assert subs <= confirm or HF.ANY in confirm, (family, subs - confirm)


# ---------------------------------------------------------------------------
# Qubit-native / interactive families -> typed refusal, never a raw forward.
# ---------------------------------------------------------------------------
def test_native_families_are_typed_refused_and_never_run_the_fork(monkeypatch):
    run_called = []
    monkeypatch.setattr(qubit_runtime, "run",
                        lambda argv=None: run_called.append(list(argv or [])) or 0)
    for family, code in NATIVE_REFUSED:
        route = HF.route([family])
        assert route is not None and route.kind == "refused", family
        assert route.code == code, (family, route.code)
        assert route.argv == [], (family, route.argv)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = CLI.main([family])
        assert rc == 3, (family, rc)
        envelope = json.loads(buf.getvalue())
        assert envelope["code"] == code, (family, envelope)
        assert envelope["state"] == "refused", (family, envelope)
    assert run_called == [], run_called


# ---------------------------------------------------------------------------
# Owner Decision #11: `update` is a native parser family, never a refusal and
# never fork-forwarded.
# ---------------------------------------------------------------------------
def test_update_is_native_not_refused_or_forwarded(monkeypatch, tmp_path):
    calls = _capture(monkeypatch)
    # Not intercepted by the forward/refuse layer at all: it parses natively.
    assert HF.route(["update"]) is None
    assert HF.route(["update", "--check"]) is None
    assert NATIVE_FAMILY_UPDATE in _top_level_native()
    assert NATIVE_FAMILY_UPDATE not in HF.NATIVE_REFUSALS

    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME",
                "XDG_CACHE_HOME"):
        d = tmp_path / var.lower()
        d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(d))
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("QUBIT_HOME", str(home / "qubit-runtime"))
    cfg = tmp_path / "voice.yaml"
    cfg.write_text("logging:\n  jsonl: false\n")

    buf = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(err):
        rc = CLI.main(["update", "--check", "--config", str(cfg)])
    assert rc in (0, 2), (rc, buf.getvalue()[:200])   # read-only check
    assert calls == [], calls                          # never the vendored fork
    assert "invalid choice" not in err.getvalue()
    assert "Qubit update" in buf.getvalue()


# ---------------------------------------------------------------------------
# OpenClaw-only families -> typed envelope; never routed through `qubit agent`.
# ---------------------------------------------------------------------------
def test_openclaw_families_are_typed_refused_not_agent_routed(monkeypatch):
    run_called = []
    monkeypatch.setattr(qubit_runtime, "run",
                        lambda argv=None: run_called.append(list(argv or [])) or 0)
    for family, code in OPENCLAW_FAMILIES.items():
        route = HF.route([family])
        assert route is not None and route.kind == "refused", family
        assert route.code == code, (family, route.code)
        assert route.code in (HF.OPENCLAW_NATIVE_EQUIVALENT,
                              HF.OPENCLAW_OPERATOR_ONLY,
                              HF.OPENCLAW_MISSING), (family, route.code)
        # The refusal names a real Qubit-native counterpart / prerequisite.
        assert route.detail, family
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = CLI.main([family])
        assert rc == 3, (family, rc)
        assert json.loads(buf.getvalue())["code"] == code, family
    assert run_called == [], run_called


# ---------------------------------------------------------------------------
# Invariants.
# ---------------------------------------------------------------------------
def test_new_families_do_not_shadow_native_qvb_parser_families():
    native = _top_level_native()
    new = set(NEW_FAMILIES) | set(HF.NATIVE_REFUSALS) | set(HF.OPENCLAW_FAMILIES)
    assert not (new & native), sorted(new & native)


def test_openclaw_families_are_not_hermes_fork_families():
    """OpenClaw families must not be forwarded into the Hermes fork table."""
    for family in OPENCLAW_FAMILIES:
        assert family not in MERGED_FAMILY_FORWARD, family
        assert not any(fam == family for fam, _sub in HF.FORWARD_TABLE), family
        assert not any(fam == family for fam, _sub in HF.CONFIRM_TABLE), family
        assert HF.route([family]).code not in ("", "CONFIRM_REQUIRED"), family


def test_new_family_subcommands_exist_in_vendored_parser():
    """Every literal readonly/confirm subcommand is a real pinned fork sub.

    ``ANY`` and the bare-family ``""`` sentinel are the only wildcards.
    """
    script = (
        "import json,sys,argparse;"
        f"sys.path.insert(0,{str(REPO_ROOT / 'qubit_runtime')!r});"
        "import qubit_cli.main as m;"
        "p,sub=m._build_cli_parser();"
        "out={f:None for f in sub.choices};"
        "[(out.__setitem__(f, sorted(a.choices.keys()))) "
        " for f,sp in sub.choices.items() for a in sp._actions "
        " if isinstance(a,argparse._SubParsersAction)];"
        "print(json.dumps(out))"
    )
    r = subprocess.run([sys.executable, "-c", script], cwd=str(REPO_ROOT),
                       capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr[-800:]
    tree = json.loads(r.stdout.strip().splitlines()[-1])
    for family in NEW_FAMILIES:
        fork_family, readonly, confirm = MERGED_FAMILY_FORWARD[family]
        assert fork_family in tree, (family, fork_family)
        subs = set(tree[fork_family] or [])
        for sub in readonly | confirm:
            if sub in ("", HF.ANY):
                continue
            assert sub in subs, (family, fork_family, sub)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
