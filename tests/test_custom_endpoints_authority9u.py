"""Focused tests for the custom-endpoint authority + env ``is_set`` truth.

Audit wave 2026-09-29 (agent ``fix-endpoints``). Covers three confirmed defects
from the isolated Qubit audit (``:8895``):

* **D1 (high)** ``h_custom_endpoints_write/activate/delete`` (qubit_compat.py)
  hand-rolled ``if not b.get("confirm")`` instead of the canonical
  ``_compat_confirm``: they bypass the shared e-stop latch and accept
  ``confirm:"false"``/``"0"`` as confirmation. Fix: the coordinator-owned
  ``qubit_compat.py`` patch (``patches/fix-endpoints.patch``).
* **D2 (high)** ``h_custom_endpoints_activate`` only wrote ``_active`` to the
  JSON store; the runtime model/provider was untouched, so ``GET /model/info``
  still reported the local default while ``GET /providers/custom-endpoints``
  reported the endpoint as current — a fabricated active state. Fix: the
  owned :func:`voicebridge.providers.custom_endpoints.register_and_activate`
  (called by the patch).
* **D3 (med)** ``GET /api/env`` hard-coded ``is_set=true`` for every reference
  registry row, even when ``env:NAME`` names an absent variable. Fix:
  :func:`voicebridge.env_store.reference_present`, wired in by the patch.

The handler-level assertions are skipped while the coordinator-owned
``qubit_compat.py`` / ``dashboard_ops.py`` patch is not applied; the
module-level helpers the patch depends on are exercised unconditionally.
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# hermetic config
# ---------------------------------------------------------------------------
def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        (tmp_path / var.lower()).mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("QUBIT_HOME", str(home / "qh"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _require_patch(what: str):
    from voicebridge import qubit_compat as HC

    if what == "write":
        src = inspect.getsource(HC.h_custom_endpoints_write)
    elif what == "activate":
        src = inspect.getsource(HC.h_custom_endpoints_activate)
    else:
        src = inspect.getsource(HC.h_custom_endpoints_delete)
    if "_compat_confirm" not in src:
        pytest.skip(
            "qubit_compat.py patch not applied (patches/fix-endpoints.patch)")


# ---------------------------------------------------------------------------
# D3 — env reference presence truth (owned helper)
# ---------------------------------------------------------------------------
def test_reference_present_env_ref_absent_is_false(tmp_path, monkeypatch):
    from voicebridge import env_store as ES

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.delenv("QA_FIX_ABSENT_123", raising=False)
    assert ES.reference_present(cfg, "env:QA_FIX_ABSENT_123") is False


def test_reference_present_env_ref_process_env_is_true(tmp_path, monkeypatch):
    from voicebridge import env_store as ES

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("QA_FIX_PRESENT_123", "value")
    assert ES.reference_present(cfg, "env:QA_FIX_PRESENT_123") is True


def test_reference_present_env_ref_runtime_dotenv_is_true(tmp_path, monkeypatch):
    from voicebridge import config as C
    from voicebridge import env_store as ES

    cfg = _cfg(tmp_path, monkeypatch)
    path = Path(C.canonical_runtime_env_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("QA_FIX_DOTENV_123=from-file\n")

    assert ES.reference_present(cfg, "env:QA_FIX_DOTENV_123") is True
    assert ES.reference_present(cfg, "env:QA_FIX_NOT_IN_FILE") is False


def test_reference_present_file_ref_tracks_existence(tmp_path, monkeypatch):
    from voicebridge import env_store as ES

    cfg = _cfg(tmp_path, monkeypatch)
    present = tmp_path / "key.txt"
    present.write_text("secret\n")
    assert ES.reference_present(cfg, f"file:{present}") is True
    assert ES.reference_present(cfg, f"file:{tmp_path / 'nope'}") is False


def test_reference_present_vault_is_declared_and_raw_is_false(tmp_path,
                                                              monkeypatch):
    from voicebridge import env_store as ES

    cfg = _cfg(tmp_path, monkeypatch)
    # vault/ref name an external secret manager with no local resolver: the
    # declared registration is the only presence signal (unchanged behavior).
    assert ES.reference_present(cfg, "vault:external-entry") is True
    assert ES.reference_present(cfg, "ref:legacy-entry") is True
    # A raw secret (or malformed value) is never a valid reference.
    assert ES.reference_present(cfg, "sk-raw-secret") is False


def test_reference_present_absent_ref_from_store_is_false(tmp_path,
                                                          monkeypatch):
    from voicebridge import env_store as ES

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.delenv("QA_FIX_STORE_ABSENT", raising=False)
    ES.set_ref(cfg, "QA_FIX_STORE_ABSENT_REF", "env:QA_FIX_STORE_ABSENT")
    refs = ES.load(cfg)
    assert refs["QA_FIX_STORE_ABSENT_REF"] == "env:QA_FIX_STORE_ABSENT"
    assert ES.reference_present(cfg, refs["QA_FIX_STORE_ABSENT_REF"]) is False


def test_env_get_is_set_false_for_absent_reference(tmp_path, monkeypatch):
    """D3 handler path: ``GET /api/env`` must not hard-code is_set=true."""
    from voicebridge import qubit_compat as HC

    src = inspect.getsource(HC.h_env)
    if "_env_ref_present" not in src:
        pytest.skip(
            "qubit_compat.py patch not applied (patches/fix-endpoints.patch)")

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.delenv("QA_FIX_GET_ABSENT", raising=False)
    code, out = HC.env_write(
        cfg, {}, {"key": "QA_FIX_GET_REF", "value": "env:QA_FIX_GET_ABSENT",
                  "confirm": True}, {})
    assert code == 200, out
    code, env = HC.h_env(cfg, {}, {}, {})
    assert env["QA_FIX_GET_REF"]["is_set"] is False, env.get("QA_FIX_GET_REF")


# ---------------------------------------------------------------------------
# D2 — custom-endpoint store + real activation (owned helper)
# ---------------------------------------------------------------------------
def test_validate_rejects_incomplete_records(tmp_path, monkeypatch):
    from voicebridge.providers import custom_endpoints as CE

    assert CE.validate({})[0] is False
    assert CE.validate({"base_url": "x", "model": "m"})[0] is False
    assert CE.validate({"base_url": "http://", "model": "m"})[0] is False
    assert CE.validate({"base_url": "http://127.0.0.1:9/v1",
                        "model": ""})[0] is False
    assert CE.validate({"base_url": "http://127.0.0.1:9/v1",
                        "model": "m"})[0] is True


def test_active_selection_ignores_removed_or_incomplete(tmp_path,
                                                        monkeypatch):
    from voicebridge.providers import custom_endpoints as CE

    cfg = _cfg(tmp_path, monkeypatch)
    assert CE.active_selection(cfg) is None
    # _active points at a missing record -> None (never a stale selection).
    CE.store_path(cfg).write_text(json.dumps({"_active": "ghost"}))
    assert CE.active_selection(cfg) is None
    # _active points at an incomplete record -> None.
    CE.store_path(cfg).write_text(json.dumps(
        {"_active": "bad", "bad": {"base_url": "http://x"}}))
    assert CE.active_selection(cfg) is None


def test_register_and_activate_is_real_for_local_endpoint(tmp_path,
                                                          monkeypatch):
    from voicebridge.providers import custom_endpoints as CE
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch)
    record = {"base_url": "http://127.0.0.1:9/v1", "model": "qa-local-model"}
    out = CE.register_and_activate(cfg, "qa-local-endpoint", record)
    assert out["state"] == "OK", out
    assert out["kind"] == R.LOCAL and out["local"] is True

    prof = R.get_profile(cfg, "qa-local-endpoint")
    assert prof is not None
    assert prof.model == "qa-local-model"
    assert prof.base_url == "http://127.0.0.1:9/v1"

    active = R.get_active_profile(cfg)
    assert active["state"] == "OK" and active["active"] == "qa-local-endpoint"


def test_register_and_activate_is_recorded_for_remote_endpoint(tmp_path,
                                                               monkeypatch):
    from voicebridge.providers import custom_endpoints as CE
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch)
    record = {"base_url": "https://api.example.invalid/v1", "model": "qa-remote"}
    out = CE.register_and_activate(cfg, "qa-remote-endpoint", record)
    assert out["state"] == "OK", out
    # Remote endpoint: active is recorded, but egress stays denied (the
    # frontier policy still gates the turn path; activation is not a bypass).
    assert out["kind"] == R.PLUGIN and out["local"] is False
    prof = R.get_profile(cfg, "qa-remote-endpoint")
    assert prof is not None and prof.egress_consent == "deny"
    resolved = R.resolve_model(cfg)
    assert resolved.get("ok") is False
    assert resolved.get("code") == "EGRESS_DENIED"


def test_register_and_activate_refuses_incomplete(tmp_path, monkeypatch):
    from voicebridge.providers import custom_endpoints as CE

    cfg = _cfg(tmp_path, monkeypatch)
    out = CE.register_and_activate(cfg, "qa-bad", {"base_url": "http://x"})
    assert out["state"] == "REFUSED"
    assert out["code"] == "ENDPOINT_INCOMPLETE"


def test_register_does_not_report_success_on_missing_state():
    # A cfg whose state dir cannot resolve must yield a typed failure, never a
    # fabricated OK. (No resolver registry path -> write raises ResolverError.)
    from voicebridge.providers import custom_endpoints as CE

    class _NoState:
        def state_dir(self):
            raise RuntimeError("no state")

    out = CE.register_and_activate(
        _NoState(), "qa", {"base_url": "http://127.0.0.1:9/v1", "model": "m"})
    assert out["state"] in ("FAILED", "REFUSED")
    assert out.get("code")


# ---------------------------------------------------------------------------
# D1 — shared confirm/e-stop gate (handler-level; patch-gated)
# ---------------------------------------------------------------------------
def test_custom_endpoint_write_refuses_under_estop(tmp_path, monkeypatch):
    _require_patch("write")
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(HC, "_estop", lambda _cfg: True)
    code, out = HC.h_custom_endpoints_write(
        cfg, {}, {"id": "qa-estop", "base_url": "http://127.0.0.1:9/v1",
                  "model": "m", "confirm": True}, {})
    assert code == 403, out
    assert out.get("code") == "EMERGENCY_STOP", out
    # Nothing was persisted.
    assert not HC._custom_endpoints_path(cfg).exists()


def test_custom_endpoint_delete_refuses_under_estop(tmp_path, monkeypatch):
    _require_patch("delete")
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(HC, "_estop", lambda _cfg: True)
    code, out = HC.h_custom_endpoints_delete(
        cfg, {}, {"confirm": True}, {"id": "qa-estop"})
    assert code == 403, out
    assert out.get("code") == "EMERGENCY_STOP", out


@pytest.mark.parametrize("flag", ["false", "0", "", "no"])
def test_custom_endpoint_write_confirm_truthiness(tmp_path, monkeypatch, flag):
    _require_patch("write")
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(HC, "_estop", lambda _cfg: False)
    code, out = HC.h_custom_endpoints_write(
        cfg, {}, {"id": "qa-false", "base_url": "http://127.0.0.1:9/v1",
                  "model": "m", "confirm": flag}, {})
    assert code == 409, out
    assert out.get("code") == "CONFIRM_REQUIRED", out
    assert not HC._custom_endpoints_path(cfg).exists()


def test_custom_endpoint_unconfirmed_409_carries_usable_id(tmp_path,
                                                           monkeypatch):
    _require_patch("write")
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(HC, "_estop", lambda _cfg: False)
    code, out = HC.h_custom_endpoints_write(
        cfg, {}, {"id": "qa-stage", "base_url": "http://127.0.0.1:9/v1",
                  "model": "m"}, {})
    assert code == 409, out
    assert out.get("confirmation_id"), out


def test_custom_endpoint_activate_is_wired_to_the_resolver(tmp_path,
                                                           monkeypatch):
    """D2 handler path: activation must touch the runtime model authority."""
    _require_patch("activate")
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(HC, "_estop", lambda _cfg: False)

    code, created = HC.h_custom_endpoints_write(
        cfg, {}, {"id": "qa-act", "base_url": "http://127.0.0.1:9/v1",
                  "model": "qa-act-model", "confirm": True}, {})
    assert code == 200, created

    code, out = HC.h_custom_endpoints_activate(
        cfg, {}, {"confirm": True}, {"id": "qa-act"})
    assert code == 200, out
    assert out.get("active") == "qa-act"

    from voicebridge.providers import resolver as R

    resolved = R.resolve_model(cfg)
    assert resolved.get("ok") is True, resolved
    assert resolved.get("provider") == "qa-act"
    assert resolved.get("model") == "qa-act-model"

    # The runtime model surface must agree (no fabricated active state).
    code, info = HC.h_model_info(cfg, {}, {}, {})
    assert info.get("provider") == "qa-act", info
    assert info.get("model") == "qa-act-model", info
