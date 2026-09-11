"""Packet 8U — Qubit local-models surface (Hermes ``local_models`` parity).

Proves the reads (status/hardware/catalog/jobs) are honest and derived from
real Qubit state, and that every write (install/download/sideload/server/
eject/activate/quickstart) is a typed gate/refusal that never downloads and
never touches the filesystem.
"""
from __future__ import annotations

import json

from voicebridge import local_models as LM


def _cfg(tmp_path, monkeypatch, extra: str = ""):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n" + extra)
    return C.Config.load(p)


def _no_estop(monkeypatch):
    monkeypatch.setattr(LM, "estop_engaged", lambda _cfg: False)


def _runtime(models):
    return {"state": "OK",
            "models": [{"id": m, "name": m, "size": 123} for m in models]}


def test_status_honest_states(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    # Runtime unreachable -> typed UNAVAILABLE, never a fabricated OK.
    monkeypatch.setattr(LM, "_probe_local", lambda *_a, **_k: None)
    out = LM.status(cfg)
    assert out["state"] == "UNAVAILABLE" and out["available"] is False
    assert out["provider"] == LM.PROVIDER and out["backend"] == LM.BACKEND
    assert out["model_present"] is False

    # Runtime up + configured model present -> AVAILABLE.
    monkeypatch.setattr(LM, "_probe_local",
                        lambda *_a, **_k: _runtime(["qubit-brain4:latest"]))
    out = LM.status(cfg)
    assert out["state"] == "AVAILABLE" and out["available"] is True
    assert out["model_present"] is True and out["observed_models"] == 1

    # Runtime up, configured model missing -> honest DEGRADED.
    monkeypatch.setattr(LM, "_probe_local",
                        lambda *_a, **_k: _runtime(["other:7b"]))
    out = LM.status(cfg)
    assert out["state"] == "DEGRADED" and out["model_present"] is False

    # Disabled in config -> UNAVAILABLE regardless of any probe.
    cfg_off = _cfg(tmp_path, monkeypatch,
                   "intent:\n  local_llm:\n    enabled: false\n")
    monkeypatch.setattr(LM, "_probe_local",
                        lambda *_a, **_k: _runtime(["qubit-brain4"]))
    out = LM.status(cfg_off)
    assert out["state"] == "UNAVAILABLE" and out["enabled"] is False


def test_hardware_reuses_gauges_and_degrades(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(LM._RIN, "current_vram_mb", lambda: 2048)
    monkeypatch.setattr(LM, "free_vram_mib", lambda: 1500)
    monkeypatch.setattr(LM, "_vision_gauge",
                        lambda _c: {"state": "READY", "model": "qwen3-vl:8b",
                                    "size_mib": 9000, "free_vram_mib": 1500})
    out = LM.hardware(cfg)
    assert out["state"] == "OK" and out["accelerator"] == "nvidia"
    assert out["vram_free_mb"] == 2048 and out["free_vram_mib"] == 1500
    assert out["vision"]["state"] == "READY"

    # No gauge -> typed UNKNOWN, and NO fabricated numbers.
    monkeypatch.setattr(LM._RIN, "current_vram_mb", lambda: -1)
    monkeypatch.setattr(LM, "free_vram_mib", lambda: None)
    monkeypatch.setattr(LM, "_vision_gauge",
                        lambda _c: {"state": "UNAVAILABLE"})
    out = LM.hardware(cfg)
    assert out["state"] == "UNKNOWN" and out["accelerator"] == "unknown"
    assert out["vram_free_mb"] is None and out["free_vram_mib"] is None


def test_catalog_from_config_and_runtime(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(LM, "_probe_local",
                        lambda *_a, **_k: _runtime(["fake-model:latest"]))
    out = LM.catalog(cfg)
    assert out["state"] == "OK" and out["runtime_observed"] is True
    by_id = {row["id"]: row for row in out["models"]}
    assert by_id["fake-model:latest"]["state"] == "installed"
    assert by_id["fake-model:latest"]["source"] == "runtime"
    assert by_id["fake-model:latest"]["size"] == 123
    # Config-declared resident model is listed even though not observed.
    assert by_id["qubit-brain4"]["state"] == "configured"
    assert by_id["qubit-brain4"]["size"] is None

    # Runtime down -> configured models only, runtime_observed False.
    monkeypatch.setattr(LM, "_probe_local", lambda *_a, **_k: None)
    out = LM.catalog(cfg)
    assert out["runtime_observed"] is False
    assert all(r["state"] == "configured" for r in out["models"])
    assert any(r["id"] == "qubit-brain4" for r in out["models"])


def test_jobs_typed_empty(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out = LM.jobs(cfg)
    assert out["state"] == "EMPTY" and out["code"] == LM.JOBS_EMPTY_CODE
    assert out["jobs"] == []


def test_every_write_is_a_typed_gate_and_writes_nothing(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _no_estop(monkeypatch)
    monkeypatch.setattr(LM, "_probe_local",
                        lambda *_a, **_k: _runtime(["qubit-brain4"]))
    state_dir = cfg.state_dir()
    before = sorted(p.name for p in state_dir.iterdir())

    writes = [
        LM.runtime_install(cfg, confirmed=True),
        LM.download(cfg, model="qubit-brain4", confirmed=True),
        LM.sideload(cfg, path="/tmp/models/x.gguf", confirmed=True),
        LM.server(cfg, "start", confirmed=True),
        LM.eject(cfg, model="qubit-brain4", confirmed=True),
        LM.activate(cfg, model="qubit-brain4", confirmed=True),
        LM.quickstart(cfg, confirmed=True),
    ]
    for out in writes:
        assert out["state"] == "REFUSED", out
        assert out["code"] == LM.BROKERED_CODE, out
        assert out["owner"] == "qubit-policy" and out["rollback"]
        assert "qubit runtime" in out["guidance"]

    after = sorted(p.name for p in state_dir.iterdir())
    assert after == before  # no state file, model, or download was created


def test_confirmation_gate_precedes_broker(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _no_estop(monkeypatch)
    monkeypatch.setattr(LM, "_probe_local", lambda *_a, **_k: None)
    for out in (LM.runtime_install(cfg),
                LM.download(cfg, model="qubit-brain4"),
                LM.quickstart(cfg)):
        assert out["code"] == LM.CONFIRM_CODE, out
        assert out["state"] == "REFUSED"


def test_runtime_ops_typed_unavailable_without_backend(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _no_estop(monkeypatch)
    monkeypatch.setattr(LM, "_probe_local", lambda *_a, **_k: None)
    for out in (LM.server(cfg, "stop", confirmed=True),
                LM.eject(cfg, model="qubit-brain4", confirmed=True),
                LM.activate(cfg, model="qubit-brain4", confirmed=True)):
        assert out["code"] == LM.UNAVAILABLE_CODE, out
        assert out["state"] == "REFUSED"


def test_unknown_model_and_bad_input_typed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _no_estop(monkeypatch)
    monkeypatch.setattr(LM, "_probe_local", lambda *_a, **_k: None)

    out = LM.download(cfg, model="no-such-model-xyz", confirmed=True)
    assert out["code"] == LM.UNKNOWN_CODE, out
    out = LM.activate(cfg, model="no-such-model-xyz", confirmed=True)
    assert out["code"] == LM.UNKNOWN_CODE, out
    out = LM.eject(cfg, model="no-such-model-xyz", confirmed=True)
    assert out["code"] == LM.UNKNOWN_CODE, out
    out = LM.quickstart(cfg, model="no-such-model-xyz", confirmed=True)
    assert out["code"] == LM.UNKNOWN_CODE, out

    assert LM.download(cfg, model="", confirmed=True)["code"] == LM.BAD_INPUT_CODE
    assert LM.sideload(cfg, path="/tmp/x.bin",
                       confirmed=True)["code"] == LM.BAD_INPUT_CODE
    assert LM.server(cfg, "explode",
                     confirmed=True)["code"] == LM.BAD_INPUT_CODE


def test_redaction_of_endpoints_and_model_ids(tmp_path, monkeypatch):
    secret = "sk-AbCdEfGhIjKlMnOpQrStUvWxYz1234567890"
    cfg = _cfg(tmp_path, monkeypatch,
               "intent:\n  local_llm:\n    url: "
               f"\"http://user:{secret}@127.0.0.1:11434\"\n")
    monkeypatch.setattr(LM, "_probe_local", lambda *_a, **_k: None)
    out = LM.status(cfg)
    assert secret not in json.dumps(out)
    assert "[REDACTED]" in out["endpoint"]

    _no_estop(monkeypatch)
    out = LM.download(cfg, model=secret, confirmed=True)
    assert secret not in json.dumps(out)
    assert LM.redact_spec(secret) == "[REDACTED]"


def test_estop_latches_writes(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(LM, "estop_engaged", lambda _cfg: True)
    out = LM.download(cfg, model="qubit-brain4", confirmed=True)
    assert out["state"] == "ESTOP" and out["code"] == LM.ESTOP_CODE
    out = LM.server(cfg, "start", confirmed=True)
    assert out["state"] == "ESTOP" and out["code"] == LM.ESTOP_CODE
