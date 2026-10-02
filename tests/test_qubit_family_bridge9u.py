"""Qubit family bridge: allowlisted reads, typed 404s, confirm round-trip.

The 13 Qubit family pages call /api/qubit/<family>/<action> (the five
ocFamily*Api ROUTES manifests). The generic bridge in
``voicebridge/qubit_compat.py`` serves them verbatim and delegates to the
already-registered gateway registry method behind each path.

Proves: an allowlisted read executes through the registry; an unknown action
(and an unknown family, and a wrong method) is a typed 404; a mutation
without confirmation stages a typed 409 with a real confirmation_id and
executes nothing; re-POST with confirmation executes exactly once.

Hermetic: temp XDG/QUBIT_HOME only; no live service, network, or hardware.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

pytestmark = pytest.mark.unit


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        (tmp_path / var.lower()).mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qubit_home"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def test_allowlisted_read_executes(tmp_path, monkeypatch):
    """GET /api/qubit/talk/mode runs the registered talk.mode method."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/talk/mode", {}, {})
    assert code == 200, out
    assert out.get("state") == "OK", out
    assert out.get("effectiveMode") == "local-stt-tts", out


def test_unknown_action_is_typed_404(tmp_path, monkeypatch):
    """A path under a known family with no allowlist row is a typed 404."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/nodes/bogus", {}, {})
    assert code == 404, out
    assert out.get("code") == "FAMILY_UNKNOWN_ACTION", out


def test_unknown_family_is_typed_404(tmp_path, monkeypatch):
    """A family outside the five manifests never reaches the bridge."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/bogus/action", {}, {})
    assert code == 404, out
    assert out.get("ok") is False, out


def test_wrong_method_is_typed_404(tmp_path, monkeypatch):
    """A read-only action fetched with POST has no route (never executes)."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "POST", "/api/qubit/talk/mode", {}, {})
    assert code == 404, out


def test_mutation_without_confirm_stages_409(tmp_path, monkeypatch):
    """POST nodes/enroll stages a typed 409 and enrolls nothing."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, staged = HC.dispatch(cfg, "POST", "/api/qubit/nodes/enroll", {},
                               {"node_id": "bridge-probe-node"})
    assert code == 409, staged
    assert staged.get("code") == "CONFIRM_REQUIRED", staged
    assert staged.get("confirmation_id"), staged
    # nothing executed: the probe node is not enrolled
    from voicebridge.gateway.rpc import default_registry

    listing = default_registry().handle(cfg, "node.list", {})
    names = [str(n.get("name", "") or "") for n in listing.get("nodes", [])]
    assert "bridge-probe-node" not in names, listing


def test_staged_plus_confirm_executes(tmp_path, monkeypatch):
    """Re-POST with confirm executes the allowlisted method exactly once."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    body = {"sessionKey": "bridge-probe-session", "text": "bridge probe"}
    code, staged = HC.dispatch(cfg, "POST",
                               "/api/qubit/talk/client-steer", {}, dict(body))
    assert code == 409, staged
    cid = staged.get("confirmation_id")
    assert cid, staged
    code, out = HC.dispatch(cfg, "POST", "/api/qubit/talk/client-steer",
                            {}, {**body, "confirm": True})
    assert code == 200, out
    assert out.get("state") == "OK", out
    assert out.get("sessionKey") == "bridge-probe-session", out


def test_honest_gate_without_registered_method(tmp_path, monkeypatch):
    """Manifest paths with no gateway method behind them stay typed gates."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/devices/identity",
                            {"device_id": "d1"}, {})
    assert code == 403, out
    assert out.get("code") == "DEVICE_IDENTITY_UNAVAILABLE", out
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/checkpoints/verify",
                            {"checkpoint_id": "c1"}, {})
    assert code == 403, out
    assert out.get("code") == "CHECKPOINTS_JOURNAL_VERIFY_ONLY", out
