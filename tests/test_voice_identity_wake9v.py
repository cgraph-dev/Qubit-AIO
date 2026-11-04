"""Voice-ID gates hands-free wake activation and spoken confirmations.

Before this lane, ``speaker_verify`` only gated barge-in: an enrolled owner
could be interrupted only by their own voice, but ANY speaker could still wake
the assistant ("hey qubit, ...") and answer a staged "say yes" confirmation.
``check_command_audio`` now applies the same owner policy to those actions:

* no enrolled profile / explicit opt-out -> historical any-voice behaviour;
* enrolled profile + matching voice -> allowed;
* enrolled profile + impostor / unverifiable audio -> typed refusal, and the
  segment never arms the listener, settles a confirmation, or executes.

Hermetic: no mic, model, socket, service or X.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge import voice_id
from voicebridge.voice_edit import wake as wake_mod
from voicebridge.voice_edit.wake import WakeMonitor

# One second of 16-bit mono audio (the verifier's minimum window).
PCM = b"\x00\x01" * 16000


class FakeCfg:
    def __init__(self, speaker_verify=None, wake=None):
        self._d = {
            "voice_edit": {"beep": False, "wake": wake or {"enabled": True}},
            "feedback": {"beeps": False},
        }
        if speaker_verify is not None:
            self._d["speaker_verify"] = speaker_verify

    def get(self, *p, default=None):
        node = self._d
        for seg in p:
            if isinstance(node, dict) and seg in node:
                node = node[seg]
            else:
                return default
        return node

    def model_dir(self):
        return Path("/tmp/no-model")

    def state_dir(self):
        return Path("/tmp/no-state")


class FakeVerifier:
    def __init__(self, accepted=True, score=0.9, error=None):
        self.ready = True
        self._accepted = accepted
        self._score = score
        self._error = error
        self.calls = 0

    def verify(self, samples, sample_rate=16000):
        self.calls += 1
        if self._error:
            raise self._error
        return self._accepted, self._score


class UnreadyVerifier(FakeVerifier):
    def __init__(self):
        super().__init__()
        self.ready = False


class _NoJournal:
    def __init__(self, monkeypatch):
        self.events = []
        monkeypatch.setattr(
            voice_id, "log_event",
            lambda event, **kw: self.events.append((event, kw)))


# ---------------------------------------------------------------------------
# policy / check_command_audio
# ---------------------------------------------------------------------------
def test_policy_off_when_explicitly_disabled():
    cfg = FakeCfg({"enabled": False, "require_commands": True})
    assert voice_id.command_identity_policy(cfg, FakeVerifier()) == \
        voice_id.POLICY_OFF
    assert voice_id.check_command_audio(cfg, FakeVerifier(accepted=False),
                                        PCM) == (True,
                                                 voice_id.REASON_POLICY_OFF)


def test_policy_off_when_commands_explicitly_opted_out():
    cfg = FakeCfg({"enabled": True, "require_commands": False})
    assert voice_id.command_identity_policy(cfg, FakeVerifier()) == \
        voice_id.POLICY_OFF
    assert voice_id.check_command_audio(cfg, FakeVerifier(accepted=False),
                                        PCM)[0] is True


def test_policy_inactive_without_an_enrolled_profile():
    cfg = FakeCfg({"enabled": True, "require_commands": True})
    assert voice_id.command_identity_policy(cfg, None) == \
        voice_id.POLICY_INACTIVE
    assert voice_id.command_identity_policy(cfg, UnreadyVerifier()) == \
        voice_id.POLICY_INACTIVE
    # Absent profile must never wedge normal wake.
    assert voice_id.check_command_audio(cfg, None, PCM) == \
        (True, voice_id.REASON_NO_PROFILE)


def test_enrolled_profile_with_broken_verifier_fails_closed(tmp_path,
                                                            monkeypatch):
    _NoJournal(monkeypatch)
    speakers = tmp_path / "speakers"
    speakers.mkdir()
    (speakers / "user.npy").write_bytes(b"not-a-real-embedding")

    class EnrolledCfg(FakeCfg):
        def state_dir(self):
            return tmp_path

    cfg = EnrolledCfg({"enabled": True, "require_commands": True})
    # A stored enrollment that cannot be loaded is still REQUIRED: never
    # silently downgrade back to any-voice.
    assert voice_id.command_identity_policy(cfg, None) == \
        voice_id.POLICY_REQUIRED
    assert voice_id.check_command_audio(cfg, None, PCM) == \
        (False, voice_id.REASON_VERIFY_ERROR)


def test_policy_required_and_verified_is_allowed(monkeypatch):
    _NoJournal(monkeypatch)
    cfg = FakeCfg({"enabled": True, "require_commands": True})
    assert voice_id.command_identity_policy(cfg, FakeVerifier()) == \
        voice_id.POLICY_REQUIRED
    assert voice_id.check_command_audio(cfg, FakeVerifier(accepted=True),
                                        PCM) == (True,
                                                 voice_id.REASON_VERIFIED)


def test_unverified_speaker_is_refused_typed(monkeypatch):
    journal = _NoJournal(monkeypatch)
    cfg = FakeCfg({"enabled": True, "require_commands": True})
    assert voice_id.check_command_audio(cfg, FakeVerifier(accepted=False,
                                                          score=0.1),
                                        PCM) == (False,
                                                 voice_id.REASON_UNVERIFIED)
    assert any(e[0] == "speaker.command_gate" and e[1].get("accepted") is False
               for e in journal.events)


def test_verification_error_fails_closed_typed(monkeypatch):
    journal = _NoJournal(monkeypatch)
    cfg = FakeCfg({"enabled": True, "require_commands": True})
    v = FakeVerifier(error=RuntimeError("embedding exploded"))
    assert voice_id.check_command_audio(cfg, v, PCM) == \
        (False, voice_id.REASON_VERIFY_ERROR)
    assert any(e[0] == "speaker.command_gate" and
               e[1].get("reason") == voice_id.REASON_VERIFY_ERROR
               for e in journal.events)


def test_short_audio_is_refused_when_profile_required():
    cfg = FakeCfg({"enabled": True, "require_commands": True})
    v = FakeVerifier(error=voice_id.SpeakerUnavailable("too short"))
    assert voice_id.check_command_audio(cfg, v, b"\x00\x01" * 100) == \
        (False, voice_id.REASON_INSUFFICIENT_AUDIO)


# ---------------------------------------------------------------------------
# WakeMonitor integration
# ---------------------------------------------------------------------------
def _check_for(cfg, verifier):
    def _check(pcm):
        return voice_id.check_command_audio(cfg, verifier, pcm, 16000,
                                            via="wake")
    return _check


def _monitor(cfg, text, verifier, *, pending=False):
    commands = []
    m = WakeMonitor(
        cfg,
        on_command=commands.append,
        is_busy=lambda: False,
        transcribe=lambda pcm: text,
        pending_probe=lambda: bool(pending),
        speaker_check=_check_for(cfg, verifier),
    )
    return m, commands


def test_verified_owner_wake_executes(monkeypatch):
    _NoJournal(monkeypatch)
    cfg = FakeCfg({"enabled": True, "require_commands": True})
    m, commands = _monitor(cfg, "hey qubit, open youtube", FakeVerifier())
    m._handle_segment(PCM)
    assert commands == ["open youtube"]


def test_unverified_wake_never_arms_or_executes(monkeypatch):
    journal = []
    monkeypatch.setattr(wake_mod, "_trace",
                        lambda stage, **kw: journal.append((stage, kw)))
    monkeypatch.setattr(voice_id, "log_event", lambda *a, **k: None)
    cfg = FakeCfg({"enabled": True, "require_commands": True})
    verifier = FakeVerifier(accepted=False, score=0.1)

    never, distinct = _monitor(cfg, "hey qubit, open youtube", verifier)
    never._handle_segment(PCM)
    assert distinct == []
    assert never._armed_until == 0.0
    assert never.last_error == "voice_id:unverified"
    assert any(stage == "voice_id_refused" and
               kw.get("reason") == voice_id.REASON_UNVERIFIED
               for stage, kw in journal)

    bare, commands = _monitor(cfg, "hey qubit", verifier)
    bare._handle_segment(PCM)
    assert commands == []
    assert bare._armed_until == 0.0


def test_unverified_confirmation_never_settles(monkeypatch):
    monkeypatch.setattr(voice_id, "log_event", lambda *a, **k: None)
    cfg = FakeCfg({"enabled": True, "require_commands": True})
    verifier = FakeVerifier(accepted=False, score=0.1)
    m, commands = _monitor(cfg, "yes", verifier, pending=True)
    m._handle_segment(PCM)
    assert commands == []
    assert m.last_error == "voice_id:unverified"


def test_verified_confirmation_still_settles(monkeypatch):
    monkeypatch.setattr(voice_id, "log_event", lambda *a, **k: None)
    cfg = FakeCfg({"enabled": True, "require_commands": True})
    m, commands = _monitor(cfg, "yes", FakeVerifier(), pending=True)
    m._handle_segment(PCM)
    assert commands == ["yes"]


def test_helper_wake_check_uses_the_shared_verifier(monkeypatch):
    monkeypatch.setattr(voice_id, "log_event", lambda *a, **k: None)
    from voicebridge.desktop_helper import DesktopHelper

    dummy = DesktopHelper.__new__(DesktopHelper)
    dummy.cfg = FakeCfg({"enabled": True, "require_commands": True})
    dummy._speaker = FakeVerifier(accepted=False, score=0.1)
    dummy._speaker_loaded = True
    assert dummy._wake_speaker_check(PCM) == (False,
                                              voice_id.REASON_UNVERIFIED)

    dummy._speaker = FakeVerifier(accepted=True)
    assert dummy._wake_speaker_check(PCM) == (True, voice_id.REASON_VERIFIED)


def test_no_profile_keeps_normal_wake_and_confirmation(monkeypatch):
    monkeypatch.setattr(voice_id, "log_event", lambda *a, **k: None)
    cfg = FakeCfg({"enabled": True, "require_commands": True})
    m, commands = _monitor(cfg, "hey qubit, open youtube", None)
    m._handle_segment(PCM)
    assert commands == ["open youtube"]

    p, confirms = _monitor(cfg, "yes", None, pending=True)
    p._handle_segment(PCM)
    assert confirms == ["yes"]


def test_explicit_opt_out_allows_even_an_unmatched_voice(monkeypatch):
    monkeypatch.setattr(voice_id, "log_event", lambda *a, **k: None)
    cfg = FakeCfg({"enabled": True, "require_commands": False})
    m, commands = _monitor(cfg, "hey qubit, open youtube",
                           FakeVerifier(accepted=False))
    m._handle_segment(PCM)
    assert commands == ["open youtube"]

    off = FakeCfg({"enabled": False, "require_commands": True})
    m2, commands2 = _monitor(off, "hey qubit, open youtube",
                             FakeVerifier(accepted=False))
    m2._handle_segment(PCM)
    assert commands2 == ["open youtube"]
