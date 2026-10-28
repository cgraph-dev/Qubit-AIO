"""Qubit native parity wave 1 — real production callers for the open
``audio_hw``, ``auth_logout``, ``frontier_show``, ``msg_account_show``,
``secrets`` and ``sync`` gap-ledger rows.

Every test here dispatches through the shared production entry point
(``dashboard_ops.run_op`` / ``run_extra_op`` / ``voice_say``) with the exact op
id, asserts the real typed result/state transition and the dispatcher's audit
receipt, and — where the catalog declares it — exercises the voice surface too.

Hardware/credential boundaries (sounddevice streams, the live qualification
operator gate, the speaker model file) are the *only* things mocked or driven
around; the production backend functions themselves always run.

Genuinely operator-only paths keep their typed degraded/gate result: no test
fabricates a microphone reading, a transcript, a speaker embedding, an
enrollment score, or a sync apply.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.delenv("VOICE_BRIDGE_QUALIFY", raising=False)
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _run(cfg, op_id, args=None, session="wave1"):
    from voicebridge import dashboard_ops as OPS

    return OPS.run_op(cfg, op_id, dict(args or {}), actor="dashboard:user",
                      session=session)


def _confirm(cfg, confirmation_id, session="wave1", **kw):
    from voicebridge import dashboard_ops as OPS

    return OPS.confirm_op(cfg, confirmation_id, actor="dashboard:user",
                          session=session, **kw)


def _say(cfg, text, session="wave1"):
    from voicebridge import voice_commands as VC

    return VC.voice_say(cfg, text, actor="voice:user", session=session,
                        source="user")


def _registry_receipt(out, op_id, session="wave1"):
    assert out["state"] == "executed", out
    assert out["receipt"]["entry"] == op_id, out
    assert out["receipt"]["session"] == session, out
    return out["receipt"]["result"]


def _offer(cfg, passphrase, requested_by="cli:user"):
    from voicebridge import sync as SY

    created = SY.offer(cfg, passphrase, requested_by=requested_by,
                       confirmed=True)
    offer = created.get("offer", {})
    assert offer.get("offer_id"), created
    return offer["offer_id"]


# --------------------------------------------------------------------- audio_hw
def test_mic_test_drives_real_capture_probe(tmp_path, monkeypatch):
    import numpy as np

    cfg = _cfg(tmp_path, monkeypatch)

    # Credential-independent path: without the live operator gate the real
    # capture probe fails closed before opening a device.
    out = _run(cfg, "mic_test")
    res = _registry_receipt(out, "mic_test")
    assert res["audio_hw"]["code"] == "LIVE_NOT_AUTHORIZED", res

    # Operator gate + mocked hardware boundary -> the real mic_selfcheck runs.
    monkeypatch.setenv("VOICE_BRIDGE_QUALIFY", "1")
    import voicebridge.audio.mic as M

    class _Stream:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, n):
            return np.full((n, 1), 0.5, dtype=np.float32), None

    monkeypatch.setattr(M.sd, "InputStream", lambda **kw: _Stream())
    live = _registry_receipt(_run(cfg, "mic_test"), "mic_test")
    assert live["audio_hw"]["state"] == "HEALTHY", live

    # Voice surface resolves the same backend and shares the receipt shape.
    said = _say(cfg, "qubit, microphone test")
    assert said["state"] == "executed", said
    assert said["receipt"]["entry"] == "mic_test"


def test_asr_test_drives_configured_provider_identity(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    res = _registry_receipt(_run(cfg, "asr_test"), "asr_test")
    hw = res["audio_hw"]
    assert hw["code"] == "ASR_TEST_NEEDS_WAV", hw
    assert hw["action"] == "asr_test", hw
    assert hw["provider"]["configured_provider"], hw
    assert hw["operator_cli"] == "qubit asr-test <file.wav>", hw


def test_tts_test_drives_real_playback_probe(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out = _run(cfg, "tts_test")
    res = _registry_receipt(out, "tts_test")
    assert res["audio_hw"]["code"] == "LIVE_NOT_AUTHORIZED", res

    monkeypatch.setenv("VOICE_BRIDGE_QUALIFY", "1")
    import sounddevice as sd

    class _Stream:
        active = True

    monkeypatch.setattr(sd, "play", lambda *a, **k: None)
    monkeypatch.setattr(sd, "stop", lambda *a, **k: None)
    monkeypatch.setattr(sd, "wait", lambda *a, **k: None)
    monkeypatch.setattr(sd, "get_stream", lambda *a, **k: _Stream())
    live = _registry_receipt(_run(cfg, "tts_test"), "tts_test")
    assert live["audio_hw"]["state"] == "OK", live
    assert live["audio_hw"]["audible"] is True, live


def test_enroll_voice_drives_real_voice_id_backend(tmp_path, monkeypatch):
    from voicebridge.voice_id import MODEL_NAME

    cfg = _cfg(tmp_path, monkeypatch)
    res = _registry_receipt(_run(cfg, "enroll_voice"), "enroll_voice")
    hw = res["audio_hw"]
    model_present = (cfg.model_dir() / MODEL_NAME).exists()
    assert hw["action"] == "enroll", hw
    assert hw["model_present"] is model_present, hw
    assert hw["code"] in ("VOICE_ID_MODEL_MISSING", "VOICE_ID_ENROLLED",
                          "VOICE_ID_NO_ENROLLMENT"), hw
    if not model_present:
        assert hw["code"] == "VOICE_ID_MODEL_MISSING", hw
        assert hw["error"], hw


def test_verify_voice_drives_real_voice_id_backend(tmp_path, monkeypatch):
    from voicebridge.voice_id import MODEL_NAME

    cfg = _cfg(tmp_path, monkeypatch)
    res = _registry_receipt(_run(cfg, "verify_voice"), "verify_voice")
    hw = res["audio_hw"]
    assert hw["action"] == "verify", hw
    assert hw["model_present"] is (cfg.model_dir() / MODEL_NAME).exists(), hw


def test_roundtrip_test_runs_real_qualification_pipeline(tmp_path,
                                                         monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    res = _registry_receipt(_run(cfg, "roundtrip_test"), "roundtrip_test")
    q = res["audio_hw"]
    assert q["state"] in ("OK", "failed"), q
    assert q["qualify_version"] == 1, q
    assert q["dry_run"] is True and q["live"] is False, q
    assert {"signal", "vad", "latency", "cleanup"} <= set(q["stages"]), q


# ----------------------------------------------------------------- auth_logout
def test_auth_logout_reaches_real_residue_scan(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    res = _registry_receipt(_run(cfg, "auth_logout"), "auth_logout")
    check = res["logout"]
    assert check["schema"] == "qubit-logout-check/1", res
    assert check["state"] in ("CLEAN", "FLAGGED"), res
    assert check["detail"], res

    said = _say(cfg, "qubit, logout check")
    assert said["state"] == "executed", said
    assert said["receipt"]["entry"] == "auth_logout"
    assert said["receipt"]["result"]["logout"]["schema"] == \
        "qubit-logout-check/1", said


# --------------------------------------------------------------- frontier_show
def test_frontier_show_reads_real_policy_entry(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    res = _registry_receipt(
        _run(cfg, "frontier_show", {"model_id": "local/qubit-brain"}),
        "frontier_show")
    assert res["provider"] == "local", res
    assert res["model"] == "qubit-brain", res
    assert res["allowed"] is False, res
    assert "disclosure" in res, res

    said = _say(cfg, "qubit, show model local/qubit-brain")
    assert said["state"] == "executed", said
    assert said["receipt"]["entry"] == "frontier_show"
    assert said["receipt"]["result"]["provider"] == "local", said


# ----------------------------------------------------------- msg_account_show
def test_msg_account_show_reads_real_account_record(tmp_path, monkeypatch):
    from voicebridge import messaging as MG

    cfg = _cfg(tmp_path, monkeypatch)
    MG.account_add(cfg, "team", "slack", "Team",
                   key_ref="env:SLACK_BOT_TOKEN",
                   requested_by="cli:user", confirmed=True)
    res = _registry_receipt(
        _run(cfg, "msg_account_show", {"account_id": "team"}),
        "msg_account_show")
    assert res["account"]["id"] == "team", res
    assert res["account"]["key_ref_kind"] == "env", res
    assert "SLACK_BOT_TOKEN" not in json.dumps(res), res

    said = _say(cfg, "qubit, show messaging account team")
    assert said["state"] == "executed", said
    assert said["receipt"]["entry"] == "msg_account_show"
    assert said["receipt"]["result"]["account"]["id"] == "team", said


# -------------------------------------------------------------------- secrets
def test_secrets_status_has_real_cli_and_voice_surface(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    res = _registry_receipt(_run(cfg, "secrets"), "secrets")
    assert "providers" in res["secrets"], res

    said = _say(cfg, "qubit, secret status")
    assert said["state"] == "executed", said
    assert said["receipt"]["entry"] == "secrets"

    # The declared CLI surface resolves to a real top-level parser family
    # (same mapping the ledger's ``_cli_path_for`` resolves to "qubit auth").
    import argparse

    from voicebridge import voice_commands as VC
    from voicebridge.cli import build_parser

    assert "secrets" in VC.CLI_COVERAGE.get("auth", ()), VC.CLI_COVERAGE
    families = set()
    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            families.update(action.choices)
    assert "auth" in families, sorted(families)


# ------------------------------------------------------------------- sync
def test_sync_accept_extra_runs_real_accept_backend(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    pw = "correct horse battery staple wave1a"
    offer_id = _offer(cfg, pw)
    bundle = Path(cfg.state_dir()) / "sync-outbox" / f"{offer_id}.vbp"

    staged = _run(cfg, "sync_accept",
                  {"bundle": str(bundle), "offer_id": offer_id,
                   "passphrase": pw})
    assert staged["state"] == "needs-confirm", staged
    assert staged.get("sensitive_required") == ["passphrase"], staged
    journal = (Path(cfg.state_dir()) / "approvals.jsonl").read_text(
        encoding="utf-8")
    assert pw not in journal

    done = _confirm(cfg, staged["confirmation_id"],
                    sensitive={"passphrase": pw})
    assert done["state"] == "executed", done
    assert done["receipt"]["op"] == "sync_accept", done
    result = done["receipt"]["result"]
    assert result["state"] == "OK" and result["applied"] is False, result
    assert result["offer_id"] == offer_id, result
    assert pw not in json.dumps(done), done


def test_sync_revoke_extra_runs_real_revoke_backend(tmp_path, monkeypatch):
    from voicebridge import sync as SY

    cfg = _cfg(tmp_path, monkeypatch)
    offer_id = _offer(cfg, "correct horse battery staple wave1b")
    staged = _run(cfg, "sync_revoke", {"offer_id": offer_id})
    assert staged["state"] == "needs-confirm", staged
    journal = (Path(cfg.state_dir()) / "approvals.jsonl").read_text(
        encoding="utf-8")
    assert "dashboard.sync_revoke" in journal

    done = _confirm(cfg, staged["confirmation_id"])
    assert done["state"] == "executed", done
    assert done["receipt"]["op"] == "sync_revoke", done
    assert done["receipt"]["result"]["state"] == "OK", done
    assert SY.show(cfg, offer_id)["offer"]["revoked"] is True, done


def test_sync_revoke_voice_runs_real_revoke_backend(tmp_path, monkeypatch):
    from voicebridge import sync as SY
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    offer_id = _offer(cfg, "correct horse battery staple wave1c")

    resolution = VC.resolve(f"qubit, revoke sync offer {offer_id}")
    assert resolution.kind == VC.MATCH, resolution
    assert resolution.entry.id == "sync_revoke_voice", resolution

    staged = _run(cfg, "sync_revoke_voice", {"offer_id": offer_id})
    assert staged["state"] == "needs-confirm", staged
    journal = (Path(cfg.state_dir()) / "approvals.jsonl").read_text(
        encoding="utf-8")
    assert "voice.sync_revoke_voice" in journal

    done = _confirm(cfg, staged["confirmation_id"])
    assert done["state"] == "executed", done
    assert done["receipt"]["entry"] == "sync_revoke_voice", done
    assert SY.show(cfg, offer_id)["offer"]["revoked"] is True, done
