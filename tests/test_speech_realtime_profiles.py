"""Speech-input / speech-output / realtime versioned profiles (PV-05, PV-06).

Verifies the canonical speech registry that extends the SAME versioned
``profiles.json`` store the model resolver owns:

* shipped default records migrated from ``config.asr`` / ``config.tts`` /
  ``config.realtime`` (sherpa STT, piper + en_US-amy-medium TTS, OpenAI
  Realtime lane);
* create/select/clone/remove + session selection;
* external realtime egress gating through ``FrontierPolicy``;
* raw-secret refusal and secret-reference-only storage;
* fallback/rollback chain, and backward-compatible config reads.

Hermetic: no network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch, frontier: bool = False, allow=None):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    body = "logging:\n  jsonl: false\n"
    if frontier:
        allow = allow or ["openai/gpt-4o-mini-realtime-preview"]
        body += (
            "intent:\n"
            "  frontier:\n"
            "    enabled: true\n"
            f"    allowlist: {json.dumps(allow)}\n")
    p.write_text(body)
    return C.Config.load(p)


# ------------------------------------------------------------------ defaults
def test_default_speech_in_is_local_sherpa_record(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R
    from voicebridge.providers import speech as S

    cfg = _cfg(tmp_path, monkeypatch)
    eff = R.resolve_speech_in(cfg)
    assert eff.profile_id == S.DEFAULT_SPEECH_IDS[S.SPEECH_IN]
    assert eff.kind == S.SPEECH_IN and eff.route == "local"
    assert eff.locality == "local" and eff.executable is True
    assert eff.settings["engine"] == "parakeet"
    assert eff.settings["vad"] is True


def test_default_speech_out_is_local_piper_amy(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R
    from voicebridge.providers import speech as S

    cfg = _cfg(tmp_path, monkeypatch)
    eff = R.resolve_speech_out(cfg)
    assert eff.profile_id == S.DEFAULT_SPEECH_IDS[S.SPEECH_OUT]
    assert eff.provider == "piper" and eff.voice == "en_US-amy-medium"
    assert eff.route == "local" and eff.executable is True
    assert eff.settings["sample_rate"] == 22050
    # the model-profile default is untouched by the speech registry
    assert R.resolve(cfg).profile_id == "qubit-brain4"


def test_default_records_track_configured_values(tmp_path, monkeypatch):
    from voicebridge import config as C
    from voicebridge.providers import speech as S

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text(
        "logging:\n  jsonl: false\n"
        "asr:\n  provider: whisper\n  engine: small\n  language: de\n"
        "tts:\n  provider: openai\n  voice: nova\n"
        "realtime:\n  model: gpt-4o-realtime-preview\n")
    cfg = C.Config.load(p)
    profs = {x.kind: x for x in S.default_speech_profiles(cfg)}
    assert profs[S.SPEECH_IN].provider == "whisper"
    assert profs[S.SPEECH_IN].language == "de"
    assert profs[S.SPEECH_OUT].provider == "openai"
    assert profs[S.SPEECH_OUT].voice == "nova"
    assert profs[S.REALTIME].model == "gpt-4o-realtime-preview"


# ----------------------------------------------------------- create + select
def test_create_and_select_speech_profile(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R
    from voicebridge.providers import speech as S

    cfg = _cfg(tmp_path, monkeypatch)
    prof = S.SpeechProfile(
        id="my-voice", kind=S.SPEECH_OUT, provider="piper",
        voice="en_US-lessac-medium", locality="local",
        latency="quality", rollback_to=S.DEFAULT_SPEECH_IDS[S.SPEECH_OUT])
    assert R.upsert_speech_profile(cfg, prof, confirm=True)["state"] == "OK"
    # dry-run does not persist
    assert R.upsert_speech_profile(
        cfg, S.SpeechProfile(id="dry", kind=S.SPEECH_IN, provider="sherpa",
                             engine="moonshine", locality="local")
    )["dry_run"] is True

    eff = R.resolve_speech_out(cfg, "my-voice")
    assert eff.profile_id == "my-voice" and eff.source == "explicit"
    assert eff.settings["latency"] == "quality"

    S.set_session_speech_profile(cfg, "s1", S.SPEECH_OUT, "my-voice",
                                 confirm=True)
    session_eff = R.resolve_speech_out(cfg, session="s1")
    assert session_eff.profile_id == "my-voice"
    assert session_eff.source == "session"
    # a different session keeps the shipped default
    assert R.resolve_speech_out(cfg, session="s2").profile_id == \
        S.DEFAULT_SPEECH_IDS[S.SPEECH_OUT]
    S.clear_session_speech_profile(cfg, "s1")
    assert R.resolve_speech_out(cfg, session="s1").profile_id == \
        S.DEFAULT_SPEECH_IDS[S.SPEECH_OUT]


def test_clone_and_remove_speech_profile(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R
    from voicebridge.providers import speech as S

    cfg = _cfg(tmp_path, monkeypatch)
    assert R.clone_speech_profile(
        cfg, S.DEFAULT_SPEECH_IDS[S.SPEECH_OUT], "cloned-voice",
        confirm=True)["state"] == "OK"
    cloned = R._speech_mod().get_speech_profile(cfg, "cloned-voice")
    assert cloned is not None and cloned.rollback_to == \
        S.DEFAULT_SPEECH_IDS[S.SPEECH_OUT]

    with pytest.raises(R.ResolverError) as exc:
        R.remove_speech_profile(cfg, S.DEFAULT_SPEECH_IDS[S.SPEECH_OUT],
                                confirm=True)
    assert exc.value.code == "IMMUTABLE_DEFAULT"

    out = R.remove_speech_profile(cfg, "cloned-voice", confirm=True)
    assert out["state"] == "OK" and out["action"] == "removed"


def test_remove_speech_profile_in_use_is_refused(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R
    from voicebridge.providers import speech as S

    cfg = _cfg(tmp_path, monkeypatch)
    prof = S.SpeechProfile(id="in-use", kind=S.SPEECH_IN, provider="sherpa",
                           engine="moonshine", locality="local")
    R.upsert_speech_profile(cfg, prof, confirm=True)
    S.set_session_speech_profile(cfg, "s1", S.SPEECH_IN, "in-use",
                                 confirm=True)
    out = R.remove_speech_profile(cfg, "in-use", confirm=True)
    assert out["state"] == "DENIED" and out["code"] == "PROFILE_IN_USE"


# ------------------------------------------------------------------ security
def test_raw_secret_in_speech_profile_is_refused(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R
    from voicebridge.providers import speech as S

    cfg = _cfg(tmp_path, monkeypatch)
    prof = S.SpeechProfile(
        id="bad-rt", kind=S.REALTIME, provider="openai",
        model="gpt-4o-mini-realtime-preview", locality="external",
        egress_consent="allow", key_ref="sk-abcdefghijklmnopqrstuvwxyz012345")
    out = R.upsert_speech_profile(cfg, prof, confirm=True)
    assert out["state"] == "FAILED" and out["code"] == "RAW_SECRET"


def test_secret_reference_is_stored_never_value(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R
    from voicebridge.providers import speech as S

    cfg = _cfg(tmp_path, monkeypatch)
    prof = S.SpeechProfile(
        id="rt-ref", kind=S.REALTIME, provider="openai",
        model="gpt-4o-mini-realtime-preview", locality="external",
        egress_consent="allow", key_ref="env:OPENAI_API_KEY")
    assert R.upsert_speech_profile(cfg, prof, confirm=True)["state"] == "OK"
    reg = json.loads((cfg.state_dir() / "profiles.json").read_text())
    rec = next(r for r in reg["realtime_profiles"] if r["id"] == "rt-ref")
    assert rec["key_ref"] == "env:OPENAI_API_KEY"
    assert "sk-" not in json.dumps(reg)


# ------------------------------------------------------------- egress gating
def test_external_realtime_is_gated_by_consent_and_policy(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R
    from voicebridge.providers import speech as S

    cfg = _cfg(tmp_path, monkeypatch, frontier=False)
    default_eff = R.resolve_realtime(cfg)
    assert default_eff.profile_id == S.DEFAULT_SPEECH_IDS[S.REALTIME]
    assert default_eff.egress_required is True
    assert default_eff.egress_allowed is False
    assert default_eff.executable is False

    prof = S.SpeechProfile(
        id="rt-consented", kind=S.REALTIME, provider="openai",
        model="gpt-4o-mini-realtime-preview", locality="external",
        egress_consent="allow", key_ref="env:OPENAI_API_KEY", duplex=True)
    R.upsert_speech_profile(cfg, prof, confirm=True)
    # consent given but frontier disabled -> still not executable
    assert R.resolve_realtime(cfg, "rt-consented").executable is False

    cfg2 = _cfg(tmp_path, monkeypatch, frontier=True)
    eff = R.resolve_realtime(cfg2, "rt-consented")
    assert eff.executable is True
    assert eff.route == "external"
    assert eff.egress_allowed is True and eff.allowlisted is True
    typed = S.resolve_speech_dict(cfg2, S.REALTIME, "rt-consented")
    assert typed["ok"] is True


def test_unallowlisted_realtime_stays_closed(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R
    from voicebridge.providers import speech as S

    cfg = _cfg(tmp_path, monkeypatch, frontier=True,
               allow=["openai/gpt-4o"])
    prof = S.SpeechProfile(
        id="rt-other", kind=S.REALTIME, provider="openai",
        model="gpt-4o-mini-realtime-preview", locality="external",
        egress_consent="allow", key_ref="env:OPENAI_API_KEY")
    R.upsert_speech_profile(cfg, prof, confirm=True)
    eff = R.resolve_realtime(cfg, "rt-other")
    assert eff.executable is False and eff.allowlisted is False


# ----------------------------------------------------- fallback / rollback
def test_fallback_and_rollback_chain_is_recorded(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R
    from voicebridge.providers import speech as S

    cfg = _cfg(tmp_path, monkeypatch)
    prof = S.SpeechProfile(
        id="primary-tts", kind=S.SPEECH_OUT, provider="piper",
        voice="en_US-lessac-medium", locality="local",
        fallback_to=S.DEFAULT_SPEECH_IDS[S.SPEECH_OUT],
        rollback_to=S.DEFAULT_SPEECH_IDS[S.SPEECH_OUT])
    R.upsert_speech_profile(cfg, prof, confirm=True)
    eff = R.resolve_speech_out(cfg, "primary-tts")
    assert eff.fallback_chain == (S.DEFAULT_SPEECH_IDS[S.SPEECH_OUT],)
    assert eff.rollback_to == S.DEFAULT_SPEECH_IDS[S.SPEECH_OUT]


# ------------------------------------------------------- registry + config BC
def test_speech_profiles_share_the_model_registry_file(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R
    from voicebridge.providers import speech as S

    cfg = _cfg(tmp_path, monkeypatch)
    R.upsert_speech_profile(cfg, S.SpeechProfile(
        id="my-asr", kind=S.SPEECH_IN, provider="sherpa", engine="moonshine",
        locality="local"), confirm=True)
    reg = json.loads((cfg.state_dir() / "profiles.json").read_text())
    assert reg["speech_in_profiles"]
    # additive: no duplicate store; only the shared registry keys are written
    assert set(reg) <= {
        "speech_in_profiles", "speech_out_profiles", "realtime_profiles",
        "model_profiles", "version"}


def test_existing_config_reads_still_work(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R  # noqa: F401

    cfg = _cfg(tmp_path, monkeypatch)
    assert cfg.get("asr", "provider") == "sherpa"
    assert cfg.get("asr", "engine") == "parakeet"
    assert cfg.get("tts", "provider") == "piper"
    assert cfg.get("tts", "voice") == "en_US-amy-medium"
    assert cfg.get("realtime", "model") == "gpt-4o-mini-realtime-preview"


def test_list_and_show_speech_profiles(tmp_path, monkeypatch):
    from voicebridge.providers import resolver as R
    from voicebridge.providers import speech as S

    cfg = _cfg(tmp_path, monkeypatch)
    listed = R.list_speech_profiles(cfg)
    assert listed["count"] == 3
    kinds = {item["kind"] for item in listed["profiles"]}
    assert kinds == set(S.SPEECH_KINDS)
    shown = R.show_speech_profile(cfg, S.DEFAULT_SPEECH_IDS[S.SPEECH_IN])
    assert shown["gate"]["state"] == "OK"
    assert shown["effective"]["executable"] is True
