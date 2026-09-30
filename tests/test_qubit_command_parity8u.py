"""Hermes command/page/route <-> Qubit CLI <-> voice <-> dashboard parity.

One machine-checked closure across the four surfaces, against the real
implementation (no mocks of the surfaces themselves):

* Hermes compat routes: ``qubit_compat.ROUTES`` (implemented + typed gated)
  served through ``dispatch()`` / ``match()``;
* Qubit CLI families: ``cli.build_parser()`` top-level subcommands;
* voice: ``voice_commands.REGISTRY`` / ``VOICE_GUARDS`` / ``VOICE_IMPOSSIBLE``;
* dashboard: ``dashboard_ops.catalog()`` (Tier-1 registry + Tier-2 extras)
  executed through the shared ``/api/ops`` broker.

Assertions:

1. every CLI family maps to >=1 registry entry or a typed ``VOICE_IMPOSSIBLE``
   reason (single clear invariant);
2. every catalog op has a dashboard surface, a CLI family, and either a voice
   execution path or an ``interface_reason`` + spoken guard -- no op is
   silently missing;
3. a representative set of catalog ops spanning every group executes through
   the real broker: reads answer 200, writes stage 409 CONFIRM_REQUIRED or a
   typed refusal -- never a fabricated 2xx; reads also execute over the real
   compat GET routes;
4. every ``disposition == "gated"`` route answers its typed code + counterpart
   and never 200.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _cli_families():
    from voicebridge.cli import build_parser

    names = set()
    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            names.update(action.choices.keys())
    return names


# Tier-2 extras claiming voice through a different Tier-1 id, same backend
# (mirrors tests/test_voice_parity_gap8u.py).
_EXTRA_VOICE_FAMILY = {
    "brain_export": ("brain_profile_export",),
    "brain_sweep": ("brain_profile_sweep",),
    "import_agent_import": ("import_refusal",),
    "kanban_delegated": ("kanban_swarm", "kanban_watch", "kanban_dispatch"),
}

# Tier-2 extras shipped with a dedicated spoken refusal entry (not registered
# in voice_commands.VOICE_GUARDS).
_EXTRA_VOICE_GUARD = {
    "export_state": "export_state_voice",
    "import_state": "import_state_voice",
}

# Extra groups that name a sub-area rather than the CLI family verb.
_EXTRA_GROUP_ALIAS = {
    "backup": "export-state",
    "state": "export-state",
    "editor": "acp",
    "agent": "import-agent",
    "computer": "computer-use",
    "webhooks": "webhook",
    "themes": "theme",
}
_EXTRA_FAMILY_OVERRIDE = {
    "backup_restore": "import-state",
    "import_state": "import-state",
    "lsp_install": "lsp",
}


def _extra_cli_family(op_id, group, families):
    family = _EXTRA_FAMILY_OVERRIDE.get(op_id) or _EXTRA_GROUP_ALIAS.get(
        group, group)
    return family if family in families else None


def _voice_surface(op_id):
    """The registry id that speaks for a catalog op, or None (a real gap)."""
    from voicebridge import dashboard_ops as OPS
    from voicebridge import voice_commands as VC

    if op_id in VC.REGISTRY:
        return op_id
    for cand in (f"{op_id}_refusal", f"{op_id}_voice"):
        if cand in VC.REGISTRY:
            return cand
    for cand in _EXTRA_VOICE_FAMILY.get(op_id, ()):
        if cand in VC.REGISTRY:
            return cand
    spec = OPS.EXTRA_OPS.get(op_id)
    if spec is not None and "voice" not in tuple(spec.interfaces):
        for cand in (f"guard_{op_id}", _EXTRA_VOICE_GUARD.get(op_id, "")):
            if cand and cand in VC.REGISTRY:
                return cand
    return None


def _registry_cli_families(entry, families):
    from voicebridge import voice_commands as VC

    direct = {fam for fam, targets in VC.CLI_COVERAGE.items()
              if entry.id in targets}
    same_backend = set()
    for other in VC.REGISTRY.values():
        if other.backend == entry.backend:
            same_backend.update(
                fam for fam, targets in VC.CLI_COVERAGE.items()
                if other.id in targets)
    chosen = (direct | same_backend) & families
    if not chosen and entry.backend in families:
        chosen = {entry.backend}
    return chosen or {"voice"}


# ----------------------------------------------------------- 1. CLI families
def test_every_cli_family_maps_to_registry_or_typed_reason():
    """Single-clear invariant: every ``qubit <family>`` is covered by the
    voice registry or a tested VOICE_IMPOSSIBLE reason (CLI/dashboard only)."""
    from voicebridge import voice_commands as VC

    families = _cli_families()
    assert set(VC.CLI_COVERAGE) == families, \
        f"CLI families drifted: {sorted(set(families) ^ set(VC.CLI_COVERAGE))}"

    for family, targets in VC.CLI_COVERAGE.items():
        for target in targets:
            assert target in VC.REGISTRY, (family, target)
        in_registry = [t for t in targets if t in VC.REGISTRY]
        reason_keys = [k for k in VC.VOICE_IMPOSSIBLE
                       if k == family or k.startswith(f"{family}.")]
        assert in_registry or reason_keys, \
            f"{family}: no registry entry and no VOICE_IMPOSSIBLE reason"

    for key, reason in VC.VOICE_IMPOSSIBLE.items():
        assert reason and len(reason) > 20, key
        family = key.split(".")[0]
        assert family in VC.CLI_COVERAGE, key
        if "." not in key:
            assert VC.CLI_COVERAGE[family] == (), key


# -------------------------------------------- 2. catalog tri-surface closure
def test_every_catalog_op_has_dashboard_cli_and_voice_surface(
        tmp_path, monkeypatch):
    """Every catalog op is dashboard-reachable, CLI-addressable, and either
    voice-executable or guarded with an interface reason. No silent misses."""
    from voicebridge import dashboard_ops as OPS
    from voicebridge import qubit_compat as HC
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    families = _cli_families()
    catalog = OPS.catalog()

    # Dashboard surface: the shared /api/ops broker is reachable via match()
    # and lists every catalog op.
    list_route, _ = HC.match("GET", "/api/ops")
    assert list_route is not None and list_route["handler"] == "h_ops_catalog"
    run_route, _ = HC.match("POST", "/api/ops/run")
    assert run_route is not None and run_route["handler"] == "h_ops_run"
    code, body = HC.dispatch(cfg, "GET", "/api/ops", {}, {})
    assert code == 200 and body.get("ok") is True
    assert {o["id"] for o in body["ops"]} == {o["id"] for o in catalog}

    gaps = []
    for op in catalog:
        op_id = op["id"]

        # CLI family.
        if op["tier"] == "registry":
            entry = VC.REGISTRY[op_id]
            chosen = _registry_cli_families(entry, families)
            if not (chosen & families):
                gaps.append((op_id, "cli-family", sorted(chosen)))
        else:
            family = _extra_cli_family(op_id, op["group"], families)
            if family is None:
                gaps.append((op_id, "cli-family", op["group"]))

        # Voice execution or a typed guard.
        if "voice" in op["interfaces"]:
            if _voice_surface(op_id) is None:
                gaps.append((op_id, "voice-entry", op["interfaces"]))
        else:
            if not op.get("interface_reason"):
                gaps.append((op_id, "interface-reason", op["interfaces"]))
            guard = op.get("voice_guard") or _EXTRA_VOICE_GUARD.get(op_id, "")
            guard = guard or (f"{op_id}_refusal")
            if guard not in _voice_registry_ids():
                gaps.append((op_id, "voice-guard", guard))

    if gaps:
        pytest.fail("GAP: catalog ops missing a surface: " + repr(gaps))


def _voice_registry_ids():
    from voicebridge import voice_commands as VC

    return set(VC.REGISTRY)


# ------------------------------------------------ 3. representative behavior
# Real compat GET reads (path, catalog op) -- every row answers 200.
GET_READ_OPS = [
    ("/api/status", "status"),
    ("/api/sessions", "sessions"),
    ("/api/config", "config_profiles"),
    ("/api/model/info", "model"),
    ("/api/profiles", "brain"),
    ("/api/skills", "skills_list"),
    ("/api/tools/toolsets", "capabilities_list"),
    ("/api/cron/jobs", "schedules_list"),
    ("/api/logs", "logs"),
    ("/api/system/stats", "status"),
    ("/api/mcp", "mcp"),
    ("/api/pairing", "pairing_list"),
    ("/api/webhooks", "webhooks"),
    ("/api/messaging/platforms", "msg_accounts"),
    ("/api/memory", "memory_search"),
    ("/api/analytics", "insights"),
    ("/api/files", "files_list"),
    ("/api/gateway", "gateway"),
    ("/api/curator", "curator"),
    ("/api/env", "auth_status"),
    ("/api/ops/doctor", "doctor"),
    ("/api/ops/checkpoints", "checkpoints_list"),
    ("/api/ops/hooks", "hooks_list"),
    ("/api/plugins/kanban/events", "kanban_list"),
    ("/api/dashboard/themes", "themes"),
    ("/api/dashboard/plugins", "skills_list"),
    ("/api/ops/boards", "kanban_boards"),
    ("/api/ops/runtime", "runtime_status"),
]


def test_representative_reads_execute_over_compat_get(tmp_path, monkeypatch):
    """>=25 reads spanning the families answer 200 over the real GET routes."""
    from voicebridge import dashboard_ops as OPS
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    catalog_ids = {o["id"] for o in OPS.catalog()}
    assert len(GET_READ_OPS) >= 25
    for path, op_id in GET_READ_OPS:
        assert op_id in catalog_ids, (path, op_id)
        code, out = HC.dispatch(cfg, "GET", path, {}, {})
        assert code == 200, (path, op_id, code, str(out)[:160])


def test_writes_stage_or_are_typed_never_fabricated_2xx(tmp_path, monkeypatch):
    """Catalog writes over the broker stage 409 CONFIRM_REQUIRED (or answer a
    typed refusal); a staged write is never rendered as 2xx success."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    writes = [
        ("kanban_create", {"title": "parity8u"}),
        ("kanban_claim", {"task_id": 1}),
        ("sessions_bulk", {"ids": "1"}),
        ("gateway_add", {"gateway_id": "g"}),
        ("webhook_add", {"name": "w", "url": "http://localhost:9/x",
                         "events": "message"}),
        ("pairing_create", {}),
        ("brain_profile_set", {"principal": "p", "field": "timezone"}),
        ("memory_remember", {"text": "x"}),
        ("task_run", {"task_text": "summarize notes"}),
        ("export_state", {"passphrase": "pw"}),
        ("import_state", {"bundle_path": "/tmp/missing.vbp",
                          "passphrase": "pw"}),
        ("backup_create", {"passphrase": "pw"}),
        ("webhook_verify", {"sub_id": "s", "payload_hex": "00",
                            "signature": "x", "timestamp": "1"}),
        ("frontier_chat", {"prompt": "hi"}),
        ("vision_describe", {"image": "/tmp/nope.png"}),
    ]
    for op_id, args in writes:
        code, out = HC.dispatch(cfg, "POST", "/api/ops/run", {},
                                {"id": op_id, "args": args})
        assert code != 200, (op_id, code, str(out)[:160])
        if code == 409:
            assert out.get("code") == "CONFIRM_REQUIRED", (op_id, out)
            assert out.get("confirmation_id"), (op_id, out)
        else:
            assert code in (400, 403), (op_id, code, out)
            assert out.get("code"), (op_id, out)
        cid = out.get("confirmation_id")
        if cid:
            HC.dispatch(cfg, "POST", "/api/ops/cancel",
                        {}, {"confirmation_id": cid})


# One curated (op, args, kind) for groups whose only voice op needs arguments.
_GROUP_CURATED = {
    "auth_login": ("auth_login", {"provider": "xai"}, "read"),
    "auth_verify": ("auth_verify", {"provider": "xai"}, "read"),
    "frontier_chat": ("frontier_chat", {"prompt": "hi"}, "write"),
    "frontier_show": ("frontier_show", {"model_id": "local/qubit-brain4"},
                      "read"),
    "memory": ("memory_search", {"query": "invoices"}, "read"),
    "plugins_install": ("plugins_install", {"manifest": "/tmp/nope.json"},
                        "write"),
    "plugins_remote": ("plugins_remote_refusal",
                       {"identifier": "github.com/x/y"}, "read"),
    "state": ("export_state", {"passphrase": "pw"}, "write"),
    "task_run": ("task_run", {"task_text": "summarize notes"}, "write"),
    "vision_describe": ("vision_describe", {"image": "/tmp/nope.png"},
                        "write"),
    # PRB-05: the speech-profile lifecycle registry entries each carry required
    # args, so the deterministic empty-arg probe cannot stage them; the curated
    # representative supplies the required fields and must still stage a real
    # confirmation (never execute on speak).
    "speech_profile_create": (
        "create_speech_profile",
        {"profile_id": "parity-speech", "kind": "speech_in",
         "provider": "whisper", "model": "large-v3"}, "write"),
    "speech_profile_clone": (
        "clone_speech_profile",
        {"source_id": "parity-speech", "new_id": "parity-speech-2"}, "write"),
    "speech_profile_remove": (
        "remove_speech_profile", {"profile_id": "parity-speech"}, "write"),
    "speech_profile_select": (
        "select_session_speech",
        {"profile_id": "parity-speech", "kind": "speech_in"}, "write"),
    "webhooks": ("webhook_verify",
                 {"sub_id": "s", "payload_hex": "00", "signature": "x",
                  "timestamp": "1"}, "write"),
}


def test_every_catalog_group_representative_reads_or_stages(
        tmp_path, monkeypatch):
    """Every catalog group has a live representative op: reads execute 200,
    writes stage 409 (or answer a typed refusal). Spans all groups."""
    from voicebridge import dashboard_ops as OPS
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    groups = {}
    for op in OPS.catalog():
        groups.setdefault(op["group"], []).append(op)
    assert len(groups) >= 25

    failures = []
    for group, ops in groups.items():
        op_id, args, kind = _pick_group_op(HC, cfg, group, ops)
        if op_id is None:
            failures.append((group, "no representative op"))
            continue
        code, out = HC.dispatch(cfg, "POST", "/api/ops/run", {},
                                {"id": op_id, "args": args})
        if kind == "read":
            if code != 200 or out.get("state") != "executed":
                failures.append((group, op_id, "read", code,
                                 out.get("state"), out.get("code")))
        else:
            if code == 409:
                if out.get("code") != "CONFIRM_REQUIRED":
                    failures.append((group, op_id, "write", code,
                                     out.get("code")))
            elif code in (400, 403) and out.get("code"):
                pass  # typed refusal/gate, not a fabricated success
            else:
                failures.append((group, op_id, "write", code,
                                 out.get("state"), out.get("code")))
        cid = out.get("confirmation_id")
        if cid:
            HC.dispatch(cfg, "POST", "/api/ops/cancel",
                        {}, {"confirmation_id": cid})
    if failures:
        pytest.fail("GAP: group representatives not live: " + repr(failures))


def _pick_group_op(HC, cfg, group, ops):
    """Deterministic representative: an empty-arg read that executes, then an
    empty-arg write that stages, else the curated argumented op."""
    for op in ops:
        if op["confirm"]:
            continue
        code, out = HC.dispatch(cfg, "POST", "/api/ops/run", {},
                                {"id": op["id"], "args": {}})
        if code == 200 and out.get("state") == "executed":
            return op["id"], {}, "read"
    for op in ops:
        if not op["confirm"]:
            continue
        code, out = HC.dispatch(cfg, "POST", "/api/ops/run", {},
                                {"id": op["id"], "args": {}})
        if code == 409 and out.get("code") == "CONFIRM_REQUIRED":
            return op["id"], {}, "write"
    return _GROUP_CURATED.get(group, (None, None, None))


# --------------------------------------------------- 4. typed gated routes
def _concretize(pattern):
    """Turn a route regex into one concrete path that full-matches it."""
    path = re.sub(r"\(\?P<[A-Za-z_][A-Za-z0-9_]*>", "", pattern)
    path = path.replace(".*", "x")
    path = re.sub(r"\[\^/\]\*", "x", path)
    path = re.sub(r"\[\^/\]\+", "x", path)
    return path.replace(")", "")


def test_every_gated_route_is_typed_never_200(tmp_path, monkeypatch):
    """Every compat route with disposition 'gated' answers its typed code and
    Qubit counterpart -- never a fabricated 200."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    gated = [r for r in HC.ROUTES if r["disposition"] == "gated"]
    assert gated, "the gated route table must not be empty"
    failures = []
    for route in gated:
        path = _concretize(route["pattern"])
        matched, _ = HC.match(route["method"], path)
        if matched is None or matched["pattern"] != route["pattern"]:
            failures.append((route["method"], route["pattern"], path,
                             "no match"))
            continue
        code, out = HC.dispatch(cfg, route["method"], path, {}, {})
        gate = route["gate"]
        if code == 200:
            failures.append((route["method"], route["pattern"], 200, out))
            continue
        if out.get("code") != gate.get("code"):
            failures.append((route["method"], route["pattern"], "code",
                             out.get("code"), gate.get("code")))
        if out.get("qubit_counterpart") != gate.get("counterpart"):
            failures.append((route["method"], route["pattern"], "counterpart",
                             out.get("qubit_counterpart")))
        if out.get("state") != "UNAVAILABLE" or out.get("ok") is not False:
            failures.append((route["method"], route["pattern"], "envelope",
                             out))
    if failures:
        pytest.fail("GAP: gated routes not typed: " + repr(failures))
