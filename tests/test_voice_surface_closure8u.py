"""Voice surface closure (owner directive 2026-09-11).

Every catalog operation has a real spoken surface:

- ops that are voice-executable are in ``voice_commands.REGISTRY`` and route
  to the same backend as CLI/dashboard;
- ops that are unsafe to speak (passphrase-sealed, nested structured data,
  preview-hash binding, raw byte payloads) expose a dedicated voice entry that
  executes a typed, audited refusal naming the safe counterpart. This is real
  execution of the Qubit policy path, not help text: no unsafe payload is ever
  bound, and the outcome carries a typed code + guidance.

No catalog op may be silently missing a voice surface.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# No-voice ops that already shipped with a dedicated refusal entry.
EXISTING_VOICE_GUARDS = {
    "export_state": "export_state_voice",
    "import_state": "import_state_voice",
}


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _guard_entry_id(op_id):
    from voicebridge import voice_commands as VC

    if op_id in VC.VOICE_GUARDS:
        assert VC.VOICE_GUARDS[op_id]["aliases"]
        return f"guard_{op_id}"
    return EXISTING_VOICE_GUARDS.get(op_id)


def test_every_catalog_op_has_a_voice_surface(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = VC.make_ctx(cfg, actor="voice:user", session="closure", source="user")
    checked_exec = 0
    checked_guard = 0
    for op in OPS.catalog():
        op_id = op["id"]
        if "voice" in op["interfaces"]:
            # Voice-executable (Tier-1 registry, or a Tier-2 extra whose
            # same-backend voice entry is proven by test_voice_parity_gap8u).
            checked_exec += 1
            continue
        # not voice-executable: must be documented AND have a spoken guard
        assert op.get("interface_reason"), op_id
        entry_id = _guard_entry_id(op_id)
        assert entry_id, f"{op_id}: no voice guard"
        assert VC.REGISTRY.get(entry_id) is not None, (op_id, entry_id)

    # every guard entry really executes a typed refusal (real policy path)
    for op_id, spec in VC.VOICE_GUARDS.items():
        entry_id = f"guard_{op_id}"
        text = "qubit, " + spec["aliases"][0]
        res = VC.resolve(text)
        assert res.kind == VC.MATCH and res.entry.id == entry_id, \
            (text, res.kind, getattr(res.entry, "id", None))
        out = VC.execute(ctx, res)
        assert out.kind == VC.EXECUTED, (op_id, out.kind, out.speech)
        result = out.receipt.get("result", {})
        assert result.get("state") == "refused", (op_id, result)
        assert result.get("code"), (op_id, result)
        assert len(result.get("guidance", "")) > 20, (op_id, result)
        assert out.confirmation_id == "", "a refusal must stage nothing"
        checked_guard += 1
    assert checked_exec >= 320, checked_exec
    assert checked_guard == len(VC.VOICE_GUARDS) >= 14, checked_guard


def test_guards_never_bind_secrets_or_unsafe_payloads():
    from voicebridge import voice_commands as VC

    forbidden = ("passphrase", "password", "secret", "payload_hex",
                 "envelope_json", "args_json", "expected_hash", "snapshot")
    for op_id, spec in VC.VOICE_GUARDS.items():
        entry = VC.REGISTRY[f"guard_{op_id}"]
        arg_names = {a.name for a in entry.args}
        assert not (arg_names & set(forbidden)), (op_id, arg_names)
        fixed = dict(entry.fixed or ())
        assert set(fixed) <= {"op", "guidance", "code"}, (op_id, fixed)
        blob = json.dumps({"aliases": list(entry.aliases),
                           "help": entry.help, "fixed": fixed})
        assert "hunter2" not in blob
        assert spec["code"] in ("PASSPHRASE_NEVER_SPOKEN",
                                "VOICE_NESTED_UNSUPPORTED",
                                "VOICE_PREVIEW_HASH_ONLY",
                                "VOICE_BYTES_UNSUPPORTED"), (op_id, spec)


def test_guard_registry_does_not_hijack_existing_commands(tmp_path,
                                                          monkeypatch):
    from voicebridge import voice_commands as VC

    _cfg(tmp_path, monkeypatch)
    samples = {
        "qubit, export my state": "export_state_voice",
        "qubit, import my state": "import_state_voice",
        "qubit, system status": "status",
        "qubit, list sessions": "sessions",
        "qubit, create task buy milk": "kanban_create",
    }
    for text, want in samples.items():
        res = VC.resolve(text)
        assert res.kind == VC.MATCH, (text, res.kind)
        assert res.entry.id == want, (text, res.entry.id, want)
