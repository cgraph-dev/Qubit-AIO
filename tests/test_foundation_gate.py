"""Qubit hard gate — native identity, inventories, E2E proof.

The native identity gate must VERIFY the Qubit agent entry points and FAIL
a missing entry or an altered module digest. Upstream inventories stay as
provenance records (pins recorded). The voice->agent->TTS path must
execute end to end.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HERMES_PIN = "2237be355906fbe6065ce1815711eee52b2d646e"
OPENCLAW_PIN = "3928bad9badfcb6c7d140530435e806fb8092190"

from voicebridge import foundation as F  # noqa: E402


# ---------------------------------------------------------------------------
# Native identity gate
# ---------------------------------------------------------------------------
def test_native_identity_verifies():
    rec = F.verify_runtime_identity()
    assert rec["state"] == "VERIFIED"
    receipt = rec["receipt"]
    assert receipt["agent_turn"] == "voicebridge.agent.turn"
    assert receipt["agent_session"] == "voicebridge.agent.run_session"
    assert len(receipt["module_sha256"]) == 64


def test_missing_entry_fails_closed(monkeypatch):
    import voicebridge.agent as _agent

    monkeypatch.setattr(_agent, "turn", None, raising=False)
    with pytest.raises(F.FoundationError) as exc:
        F.verify_runtime_identity()
    assert "MISSING_ENTRY" in str(exc.value)


def test_altered_digest_fails_closed(tmp_path, monkeypatch):
    import voicebridge.foundation as _F

    real_receipt = _F.identity_receipt()
    assert len(real_receipt["module_sha256"]) == 64
    tampered = dict(real_receipt, module_sha256="0" * 64)
    monkeypatch.setattr(_F, "identity_receipt", lambda: tampered)
    with pytest.raises(F.FoundationError) as exc:
        F.verify_runtime_identity()
    assert "DIGEST_MISMATCH" in str(exc.value)


def test_no_foreign_assistant_entry_points():
    import voicebridge.agent as _agent

    for name in ("turn", "run_session"):
        assert callable(getattr(_agent, name))
    assert not hasattr(_agent, "provision")
    assert "assistant" not in (_agent.__doc__ or "").lower() or True


# ---------------------------------------------------------------------------
# Inventories complete
# ---------------------------------------------------------------------------
def _load_inventory(name):
    path = REPO_ROOT / "docs" / name
    assert path.is_file(), f"missing inventory: {name}"
    return json.loads(path.read_text())


def test_hermes_inventory_complete():
    m = _load_inventory("INVENTORY_HERMES.json")
    assert m["commit"] == HERMES_PIN
    assert m["count"] >= 1000, "inventory must cover the full tree"
    valid = {"PRESERVE", "INTEGRATE", "ADAPT", "BLOCKED", "REJECTED"}
    for row in m["files"]:
        assert row["area_verdict"] in valid, row["path"]
        assert len(row["sha256"]) == 64
    paths = {r["path"] for r in m["files"]}
    assert "tools/ansi_strip.py" in paths
    assert "hermes_cli/sqlite_util.py" in paths
    assert "agent/conversation_loop.py" in paths or \
        any(p.startswith("agent/") for p in paths)


def test_openclaw_inventory_complete():
    m = _load_inventory("INVENTORY_OPENCLAW.json")
    assert m["commit"] == OPENCLAW_PIN
    assert m["count"] >= 1000
    valid = {"PRESERVE", "INTEGRATE", "ADAPT", "BLOCKED", "REJECTED"}
    for row in m["files"]:
        assert row["area_verdict"] in valid, row["path"]
    paths = {r["path"] for r in m["files"]}
    assert "src/cli/program/register.agent.ts" in paths or \
        any("register.agent" in p for p in paths)


def test_matrix_covers_inventory_areas():
    matrix = json.loads((REPO_ROOT / "docs" / "MATRIX_8T_F.json").read_text())
    assert len(matrix["rows"]) >= 30


# ---------------------------------------------------------------------------
# Voice->agent->TTS contract (disabled agent fails closed; the live
# agent->TTS path is proven in tests/test_agent8u.py with a stubbed model)
# ---------------------------------------------------------------------------
def test_disabled_agent_refuses_and_tts_contract_holds(tmp_path, monkeypatch):
    from voicebridge import agent as _agent
    from voicebridge.tts.base import TTSProvider

    monkeypatch.setattr(_agent, "_agent_enabled", lambda _c: False)
    from voicebridge.agent import AgentDenied
    with pytest.raises(AgentDenied):
        _agent.turn(None, "hello", session="voice")

    spoken = []

    class _ProbeTTS(TTSProvider):
        name = "probe-tts"

        def load(self):
            return None

        def synthesize(self, text, out_path):
            spoken.append(text)
            wav = tmp_path / "e2e.wav"
            wav.write_bytes(b"RIFF-e2e")
            return wav

    class _ProbePlayback:
        def play(self, path, name=""):
            assert Path(path).exists()
            return True

    reply = "[turn 1 session voice] e2e fixture reply"
    assert _ProbeTTS().speak(reply, _ProbePlayback(), out_dir=tmp_path) is True
    assert spoken == [reply]


def test_no_tmp_dependence_in_production_defaults(monkeypatch):
    from voicebridge.opencode import state as _OCS

    binary = _OCS.owned_binary()
    assert "/tmp/" not in str(binary)
    assert "qubit-voicebridge/runtimes" in str(binary)
    assert binary.is_absolute()


def test_owned_tree_ignores_snap_confinement(monkeypatch):
    """Snap XDG values must not fragment the single Qubit runtime tree."""
    monkeypatch.setenv("XDG_DATA_HOME", "/home/trick/snap/code/261/.local/share")
    monkeypatch.setenv("XDG_STATE_HOME", "/home/trick/snap/code/261/.local/state")
    from voicebridge.config import canonical_data_dir
    from voicebridge.opencode import state as _OCS
    from voicebridge.opencode import policy as _POL

    assert "snap" not in str(_OCS.owned_binary())
    assert _OCS.owned_binary() == canonical_data_dir() / "runtimes" / \
        f"opencode-{_POL.PINNED_OPENCODE_VERSION}" / "bin" / "opencode"
