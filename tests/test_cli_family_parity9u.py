"""Packet 9U — CLI family parity for the families the reconciliation report
listed as "reachable only via the generic ``qubit agent <args>`` passthrough".

Re-checked against the live worktree (``voicebridge/cli_qubit_forward.py`` +
``voicebridge/cli.py``), the premise is stale: Wave 6 already intercepts every
one of these families *before* argparse and gives it a classified route, so
none falls through to the raw ``qubit agent`` passthrough or to argparse
``invalid choice``. Each has one of exactly three dispositions:

* ``forward``       — read-only/diagnostic subcommands run in the vendored fork
                      verbatim (classified, not a generic bypass);
* ``needs-confirm`` — mutating/credentialed subcommands require Qubit's
                      ``--confirm`` and the shared dispatch guard;
* ``refused``       — a typed envelope naming the Qubit-native counterpart
                      (Qubit-owned or OpenClaw-only families).

The contract forbids closure on a typed refusal with **no working allowed
counterpart**. This module locks in that every refusal names a counterpart that
is a real top-level ``qubit`` command, and that every forwarded family has a
canonical Qubit-native surface (or an explicit fork-only reason).

Owner Decision #11 amended one row: ``update`` is now a **native Qubit parser
family** (``qubit update --check|--apply|--rollback``) rather than a typed
refusal, so it is asserted in ``NATIVE_FAMILIES`` and excluded from the
refused/forwarded parametrizations — never weakened, just re-classified.

A later W4 follow-up graduates ``chat`` the same way: ``QUBIT_NATIVE_CHAT`` is
gone, and ``qubit chat`` is now a first-class **classified forward** to the
vendored interactive chat (the real working counterpart the old refusal only
named). It is tracked in ``FORWARDED_FAMILIES`` and covered by
``tests/test_cli_chat_native9u.py`` — not a refusal, not a raw passthrough.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge import cli as CLI  # noqa: E402
from voicebridge import cli_qubit_forward as HF  # noqa: E402
from voicebridge import qubit_runtime  # noqa: E402

# Exactly the families named by the reconciliation report / packet brief.
RECONCILIATION_HERMES = (
    "chat", "curator", "dashboard", "fallback", "import", "insights",
    "journey", "learning", "login", "logout", "migrate", "monitoring",
    "pets", "resume", "secrets", "security", "send", "serve", "skin",
    "slack", "uninstall", "update", "whatsapp",
)
RECONCILIATION_OPENCLAW = (
    "agents", "audit", "devices", "nodes", "onboard", "sandbox", "system",
    "terminal", "worktrees",
)

# family -> (refusal code, the exact canonical command named in the refusal,
#            the top-level family that command resolves to)
NATIVE_REFUSALS = {
    "dashboard": ("QUBIT_NATIVE_DASHBOARD", "qubit webui", "webui"),
    "login": ("QUBIT_NATIVE_LOGIN", "qubit auth login", "auth"),
    "logout": ("QUBIT_NATIVE_LOGOUT", "qubit auth logout", "auth"),
    "resume": ("QUBIT_NATIVE_RESUME", "qubit power enable", "power"),
    "send": ("QUBIT_NATIVE_SEND", "qubit msg send", "msg"),
    "serve": ("QUBIT_NATIVE_SERVE", "qubit gateway serve", "gateway"),
    "uninstall": ("QUBIT_NATIVE_UNINSTALL", "qubit package uninstall",
                  "package"),
    "whatsapp": ("QUBIT_NATIVE_WHATSAPP", "qubit channels", "channels"),
    "whatsapp-cloud": ("QUBIT_NATIVE_WHATSAPP_CLOUD", "qubit channels",
                       "channels"),
}

# Owner Decision #11: `update` graduated from a typed refusal to a native Qubit
# parser family (`qubit update --check|--apply|--rollback`). It is no longer
# intercepted by `cli_qubit_forward` and is asserted as a native family below.
NATIVE_FAMILIES = {"update": "update"}

# W4 follow-up: `chat` graduated from a typed refusal to a first-class
# classified forward to the vendored interactive chat
# (`cli_qubit_forward.chat_route`). It is intercepted before argparse (so not
# a passthrough/`invalid choice`) and asserted in
# `test_chat_is_a_classified_forward` below.
FORWARDED_FAMILIES = {"chat"}

# family -> canonical Qubit-native family, or None when the surface is
# fork-only (no Qubit-native equivalent) and the classified forward is the
# whole disposition.
FORWARD_FAMILIES = {
    "curator": "lessons",
    "fallback": "frontier",
    "import": "import-state",
    "insights": "sessions",
    "journey": "lessons",
    "learning": "lessons",
    "migrate": "product",
    "monitoring": "monitor",
    "pets": None,          # fork-only cosmetic surface
    "secrets": "config",
    "security": "verify-release",
    "skin": "theme",
    "slack": "channels",
}

OPENCLAW_FAMILIES = {
    "agents": "agent",
    "audit": "receipts",
    "devices": "device",
    "nodes": "node",
    "onboard": "setup",
    "sandbox": "runtime",
    "system": "status",
    "terminal": "tui",
    "worktrees": "worktree",
}

_OPENCLAW_CODES = (HF.OPENCLAW_NATIVE_EQUIVALENT, HF.OPENCLAW_OPERATOR_ONLY,
                   HF.OPENCLAW_MISSING)


def _top_level() -> set:
    parser = CLI.build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    return set()


def _help_ok(family: str) -> bool:
    buf = io.StringIO()
    with pytest.raises(SystemExit) as exc:
        with redirect_stdout(buf):
            CLI.main([family, "--help"])
    return exc.value.code == 0 and family in buf.getvalue()


# ---------------------------------------------------------------------------
# No named family is a generic `qubit agent` passthrough.
# ---------------------------------------------------------------------------
def test_reconciliation_family_lists_are_covered():
    named = set(RECONCILIATION_HERMES) | set(RECONCILIATION_OPENCLAW)
    covered = set(NATIVE_REFUSALS) | set(FORWARD_FAMILIES) | \
        set(OPENCLAW_FAMILIES) | set(NATIVE_FAMILIES) | set(FORWARDED_FAMILIES)
    assert named <= covered, sorted(named - covered)


@pytest.mark.parametrize(
    "family", sorted((set(RECONCILIATION_HERMES)
                      | set(RECONCILIATION_OPENCLAW)) - set(NATIVE_FAMILIES)
                     - set(FORWARDED_FAMILIES)))
def test_family_is_intercepted_before_argparse(family):
    """``HF.route`` resolves the family to a classified route, never None.

    ``None`` would mean the call falls through to the raw ``qubit agent``
    passthrough or argparse ``invalid choice`` — the exact gap this packet
    closes.
    """
    assert HF.route([family]) is not None, family
    assert HF.route([family, "__no_such_subcommand__"]) is not None, family
    assert HF.route([family]).kind in ("forward", "needs-confirm", "refused")


@pytest.mark.parametrize(
    "family", sorted((set(RECONCILIATION_HERMES)
                      | set(RECONCILIATION_OPENCLAW)) - set(NATIVE_FAMILIES)
                     - set(FORWARDED_FAMILIES)))
def test_family_never_falls_through_to_agent_passthrough(family, monkeypatch):
    calls = []
    monkeypatch.setattr(qubit_runtime, "run",
                        lambda argv=None: calls.append(list(argv or [])) or 0)
    err = io.StringIO()
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            rc = CLI.main([family, "__no_such_subcommand__"])
    except SystemExit as exc:  # pragma: no cover - regression guard
        pytest.fail(f"qubit {family} fell through to argparse: rc={exc.code}")
    assert rc in (0, 3), (family, rc)
    assert "invalid choice" not in err.getvalue(), (family, err.getvalue())
    assert calls == [], (family, calls)


# ---------------------------------------------------------------------------
# Owner Decision #11: native families (no longer refusals) are real Qubit
# parser families — route() must return None and argparse must own the family.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("family", sorted(NATIVE_FAMILIES))
def test_native_family_is_a_real_qvb_parser_family(family):
    assert HF.route([family]) is None, family
    assert HF.route([family, "--check"]) is None, family
    assert family in _top_level(), family
    assert _help_ok(family), family


# ---------------------------------------------------------------------------
# W4 follow-up: `chat` (no longer a refusal) is a first-class classified
# forward to the vendored interactive chat — never a raw passthrough.
# ---------------------------------------------------------------------------
def test_chat_is_a_classified_forward(monkeypatch):
    assert "chat" not in HF.NATIVE_REFUSALS
    for argv in (["chat"], ["chat", "-q", "hi"], ["chat", "--help"],
                 ["chat", "--resume", "latest"]):
        route = HF.route(argv)
        assert route is not None and route.kind == "forward", (argv, route)
        assert route.argv == argv, (argv, route.argv)
    # A bogus subcommand is intercepted too (no argparse "invalid choice").
    assert HF.route(["chat", "__no_such_subcommand__"]) is not None

    calls = []
    monkeypatch.setattr(qubit_runtime, "run",
                        lambda argv=None: calls.append(list(argv or [])) or 0)
    with redirect_stdout(io.StringIO()):
        rc = CLI.main(["chat", "-q", "hi"])
    assert rc == 0 and calls == [["chat", "-q", "hi"]], calls


# ---------------------------------------------------------------------------
# Every refusal names a working, real canonical Qubit command.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("family", sorted(NATIVE_REFUSALS))
def test_native_refusal_names_a_real_counterpart(family):
    code, command, counterpart = NATIVE_REFUSALS[family]
    route = HF.route([family])
    assert route is not None and route.kind == "refused", (family, route)
    assert route.code == code, (family, route.code)
    assert command in (route.detail or ""), (family, route.detail)
    assert counterpart in _top_level(), (family, counterpart)
    assert _help_ok(counterpart), (family, counterpart)


def test_native_refusals_match_forward_module_table():
    for family, (code, _cmd, _counterpart) in NATIVE_REFUSALS.items():
        assert family in HF.NATIVE_REFUSALS, family
        assert HF.NATIVE_REFUSALS[family][0] == code, family


# ---------------------------------------------------------------------------
# Forwarded families have a canonical Qubit-native surface (or a reason).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("family", sorted(FORWARD_FAMILIES))
def test_forward_family_has_a_canonical_surface(family):
    counterpart = FORWARD_FAMILIES[family]
    if counterpart is None:
        # Fork-only: assert the disposition is still classified, never raw.
        route = HF.route([family])
        assert route is not None and route.kind in (
            "forward", "needs-confirm", "refused"), (family, route)
        return
    assert counterpart in _top_level(), (family, counterpart)
    assert _help_ok(counterpart), (family, counterpart)


# ---------------------------------------------------------------------------
# OpenClaw-only families map to a real Qubit-native counterpart.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("family", sorted(OPENCLAW_FAMILIES))
def test_openclaw_family_names_a_real_counterpart(family):
    route = HF.route([family])
    assert route is not None and route.kind == "refused", (family, route)
    assert route.code in _OPENCLAW_CODES, (family, route.code)
    assert route.detail, family
    counterpart = OPENCLAW_FAMILIES[family]
    assert counterpart in _top_level(), (family, counterpart)
    assert _help_ok(counterpart), (family, counterpart)


# ---------------------------------------------------------------------------
# No named family shadows a native Qubit parser family.
# ---------------------------------------------------------------------------
def test_named_families_do_not_shadow_native_parser_families():
    native = _top_level()
    named = (set(RECONCILIATION_HERMES) | set(RECONCILIATION_OPENCLAW)) \
        - set(NATIVE_FAMILIES)
    assert not (named & native), sorted(named & native)


# ---------------------------------------------------------------------------
# Whole-surface invariant: no vendored-fork family is a generic passthrough.
# ---------------------------------------------------------------------------
def test_no_fork_family_is_an_unclassified_passthrough():
    """Every top-level family the vendored fork exposes is either a native
    Qubit parser family or intercepted by a classified ``route`` — none is
    reachable only through the generic ``qubit agent <args>`` passthrough.
    """
    import subprocess

    script = (
        "import json,sys,argparse;"
        f"sys.path.insert(0,{str(REPO_ROOT / 'qubit_runtime')!r});"
        "import qubit_cli.main as m;"
        "p,sub=m._build_cli_parser();"
        "print(json.dumps(sorted(sub.choices.keys())))"
    )
    r = subprocess.run([sys.executable, "-c", script], cwd=str(REPO_ROOT),
                       capture_output=True, text=True, timeout=280)
    assert r.returncode == 0, r.stderr[-800:]
    fork_families = json.loads(r.stdout.strip().splitlines()[-1])
    assert fork_families, "no fork families discovered"
    native = _top_level()
    unclassified = [f for f in fork_families
                    if f not in native and HF.route([f]) is None]
    assert not unclassified, unclassified


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
