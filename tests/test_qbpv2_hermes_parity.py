"""W2 — pinned Hermes surface enumeration + active-backend reachability.

Complements the existing wave tests with the W2 specific closure:

* every pinned fork CLI family/subcommand reachable from a Qubit forward route
  actually exists in the vendored parser (including the FAMILY_FORWARD
  read-only/confirm subcommands, which earlier waves only checked at the
  family level);
* every implemented ``hermes_compat`` route names a real, callable handler, so
  the dispatcher can never answer ``NO_HANDLER`` for a declared surface;
* no two compat rows collide on the same (method, concrete path);
* the new chat transport rows are reached through the production dispatcher.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

_PARSER_SCRIPT = (
    "import json,sys,argparse;"
    f"sys.path.insert(0,{str(REPO_ROOT / 'qubit_hermes')!r});"
    "import hermes_cli.main as m;"
    "p,sub=m._build_cli_parser();"
    "out={f:None for f in sub.choices};"
    "[(out.__setitem__(f, sorted(a.choices.keys()))) "
    " for f,sp in sub.choices.items() for a in sp._actions "
    " if isinstance(a, argparse._SubParsersAction)];"
    "print(json.dumps(out))"
)


@pytest.fixture(scope="module")
def fork_tree():
    r = subprocess.run([sys.executable, "-c", _PARSER_SCRIPT],
                       cwd=str(REPO_ROOT), capture_output=True, text=True,
                       timeout=180)
    assert r.returncode == 0, r.stderr[-800:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_every_forward_target_exists_in_pinned_parser(fork_tree):
    from voicebridge import cli_hermes_forward as HF

    missing = []
    for pair, mapped in HF.FORWARD_TABLE.items():
        fam = mapped[0]
        if fam not in fork_tree:
            missing.append(("forward-family", pair, mapped))
        elif len(mapped) > 1 and mapped[1] not in (fork_tree[fam] or []):
            missing.append(("forward-sub", pair, mapped))
    assert missing == [], missing


def test_every_confirm_target_exists_in_pinned_parser(fork_tree):
    from voicebridge import cli_hermes_forward as HF

    missing = []
    for pair, mapped in HF.CONFIRM_TABLE.items():
        fam = mapped[0]
        if fam not in fork_tree:
            missing.append(("confirm-family", pair, mapped))
        elif len(mapped) > 1 and mapped[1] not in (fork_tree[fam] or []):
            missing.append(("confirm-sub", pair, mapped))
    assert missing == [], missing


def test_every_family_readonly_and_confirm_sub_exists(fork_tree):
    """FAMILY_FORWARD readonly/confirm subcommands must exist in the fork.

    ``ANY`` and the bare-family sentinel are the only wildcards: a literal
    subcommand that is not a real pinned parser subcommand is a dead forward.
    """
    from voicebridge import cli_hermes_forward as HF

    missing = []
    for family, (fork_family, readonly, confirm) in HF.FAMILY_FORWARD.items():
        if fork_family not in fork_tree:
            missing.append(("family", family, fork_family))
            continue
        subs = set(fork_tree[fork_family] or [])
        for sub in readonly | confirm:
            if sub in ("", HF.ANY):
                continue
            if sub not in subs:
                missing.append(("sub", family, fork_family, sub))
    assert missing == [], missing


def test_forward_pairs_never_shadow_native_qubit_subcommands():
    from voicebridge import cli_hermes_forward as HF
    from voicebridge.cli import build_parser

    families: dict = {}
    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            for fam, sp in action.choices.items():
                subs = set()
                for sub in sp._actions:
                    if isinstance(sub, argparse._SubParsersAction):
                        subs.update(sub.choices.keys())
                families[fam] = subs
    shadowed = [(fam, sub) for (fam, sub) in HF.FORWARD_TABLE
                if sub in families.get(fam, set())]
    assert shadowed == [], shadowed


def test_every_implemented_route_names_a_callable_handler():
    from voicebridge import hermes_compat as HC

    dead = []
    for route in HC.ROUTES:
        if route["disposition"] != "implemented":
            continue
        handler = HC.HANDLERS.get(route["handler"])
        if handler is None or not callable(handler):
            dead.append((route["method"], route["pattern"], route["handler"]))
    assert dead == [], dead


def test_every_gated_route_carries_a_typed_gate():
    from voicebridge import hermes_compat as HC

    bad = []
    for route in HC.ROUTES:
        if route["disposition"] != "gated":
            continue
        gate = route.get("gate") or {}
        if not (gate.get("code") and gate.get("owner")
                and (gate.get("counterpart") or gate.get("qubit_counterpart"))):
            bad.append((route["method"], route["pattern"], gate))
    assert bad == [], bad


def test_no_compat_route_shadows_another_on_a_concrete_path():
    """A concrete path must resolve to exactly one row per method.

    Two rows whose regexes both fullmatch the same concrete path would make the
    dispatcher order-dependent; the first would silently shadow the second.
    """
    from voicebridge import hermes_compat as HC

    def concretize(pattern: str) -> str:
        p = re.sub(r"\(\?P<[A-Za-z_][A-Za-z0-9_]*>", "", pattern)
        p = p.replace(".*", "x").replace(".*", "x")
        p = re.sub(r"\[\^/\]\*", "x", p)
        p = re.sub(r"\[\^/\]\+", "x", p)
        return p.replace(")", "")

    seen: dict = {}
    collisions = []
    for route in HC.ROUTES:
        key = (route["method"], concretize(route["pattern"]))
        if key in seen:
            collisions.append((key, seen[key], route["pattern"]))
        else:
            seen[key] = route["pattern"]
    # A collision is only real when the concrete path fullmatches both rows.
    real = []
    for (method, path), first, second in collisions:
        m1 = re.fullmatch(first, path)
        m2 = re.fullmatch(second, path)
        if m1 and m2:
            real.append((method, path, first, second))
    assert real == [], real


def test_chat_transport_routes_reach_handlers_via_dispatch(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    from voicebridge import config as C

    p = tmp_path / "v.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    cfg = C.Config.load(p)
    import voicebridge.local_llm as _llm

    monkeypatch.setattr(_llm, "chat_local",
                        lambda *a, **k: "parity reply")
    code, out = HC.dispatch(cfg, "POST", "/api/qubit/chat", {},
                            {"text": "hi", "session": "parity"})
    assert code == 200 and out["ok"] is True
    code, out = HC.dispatch(cfg, "GET",
                            "/api/qubit/chat/sessions/parity", {}, {})
    assert code == 200 and out["ok"] is True
    assert out["messages"][-1]["content"] == "parity reply"
