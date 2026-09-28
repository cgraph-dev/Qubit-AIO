"""A42 — cloud-worker adapter config + snapshot ops (typed, reference-only)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    return C.Config({})


def test_config_set_requires_confirmation(tmp_path, monkeypatch):
    from voicebridge import cloud_workers as CW

    cfg = _cfg(tmp_path, monkeypatch)
    out = CW.config_set(cfg, provider="acme", key_ref="env:ACME_KEY")
    assert out["state"] == "denied"
    assert out["code"] == CW.CONFIRM_CODE


def test_plaintext_secret_reference_is_refused(tmp_path, monkeypatch):
    from voicebridge import cloud_workers as CW

    cfg = _cfg(tmp_path, monkeypatch)
    out = CW.config_set(cfg, provider="acme", key_ref="sk-1234567890abcdef",
                        confirmed=True)
    assert out["state"] == "REFUSED"
    assert out["code"] == CW.SECRET_CODE


def test_confirmed_reference_config_persists(tmp_path, monkeypatch):
    from voicebridge import cloud_workers as CW

    cfg = _cfg(tmp_path, monkeypatch)
    out = CW.config_set(cfg, provider="acme", endpoint="https://acme.example",
                        key_ref="env:ACME_KEY", confirmed=True)
    assert out["state"] == "OK"
    stored = CW.config_get(cfg)["config"]
    assert stored["provider"] == "acme"
    assert stored["key_ref"] == "env:ACME_KEY"
    # No plaintext secret value is ever stored.
    assert "sk-" not in str(stored)


def test_snapshot_build_is_typed_unavailable_without_transport(
        tmp_path, monkeypatch):
    from voicebridge import cloud_workers as CW

    cfg = _cfg(tmp_path, monkeypatch)
    out = CW.snapshot_build(cfg, name="snap1", confirmed=True)
    assert out["state"] == "UNAVAILABLE"
    assert out["code"] == CW.UNAVAILABLE_CODE


def test_snapshot_build_uses_injected_transport(tmp_path, monkeypatch):
    from voicebridge import cloud_workers as CW

    cfg = _cfg(tmp_path, monkeypatch)
    seen = {}

    def transport(req):
        seen.update(req)
        return {"built": True, "snapshot_id": "snap-9", "provider": "acme"}

    out = CW.snapshot_build(cfg, name="snap1", confirmed=True,
                            transport=transport)
    assert out["state"] == "OK"
    assert out["snapshot"] == "snap-9"
    assert seen["type"] == "snapshot.build"


def test_registry_exposes_cloud_worker_methods():
    from voicebridge.gateway.rpc import default_registry

    names = set(default_registry().names())
    assert "cloud_workers.config.get" in names
    assert "cloud_workers.config.set" in names
    assert "cloud_workers.snapshot.build" in names
