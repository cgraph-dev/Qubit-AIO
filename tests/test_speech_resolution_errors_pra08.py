"""PRA-08 — speech resolver failures are typed, recorded, and surfaced.

An explicitly selected speech profile that cannot be resolved or is not
executable must never be silently swallowed into the config fallback: the
orchestrator records a typed error and the canonical runtime inventory reports
it, so CLI `qubit status`, dashboard `/api/runtime`, and voice status are
truthful.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _clear():
    from voicebridge.providers import speech as S
    for kind in S.SPEECH_KINDS:
        S.clear_resolution_error(kind)


class _FakeEff:
    def __init__(self, record):
        self._record = record

    def to_public_dict(self):
        return dict(self._record)


def test_not_executable_explicit_profile_is_recorded(tmp_path, monkeypatch):
    from voicebridge import orchestrator as O
    from voicebridge.providers import speech as S

    cfg = _cfg(tmp_path, monkeypatch)
    _clear()
    monkeypatch.setattr(S, "resolve_speech", lambda *a, **k: _FakeEff({
        "profile_id": "ext-tts", "kind": S.SPEECH_OUT, "source": "explicit",
        "executable": False, "route": "external", "egress_allowed": False,
        "allowlisted": False,
    }))

    override = O._speech_override(cfg, S.SPEECH_OUT)
    assert override == {}, override  # hardware config path preserved
    errors = S.resolution_errors()
    assert errors[S.SPEECH_OUT]["code"] == "SPEECH_PROFILE_NOT_EXECUTABLE", errors
    assert errors[S.SPEECH_OUT]["profile_id"] == "ext-tts"

    from voicebridge import runtime_inventory as RI
    voice = RI._voice(cfg)
    assert voice["profile_errors"][S.SPEECH_OUT]["code"] == \
        "SPEECH_PROFILE_NOT_EXECUTABLE"
    _clear()


def test_resolver_exception_is_typed_and_surfaced(tmp_path, monkeypatch):
    from voicebridge import orchestrator as O
    from voicebridge.providers import speech as S

    cfg = _cfg(tmp_path, monkeypatch)
    _clear()

    def _boom(*a, **k):
        raise S.ResolverError("BOOM", "resolver exploded")

    monkeypatch.setattr(S, "resolve_speech", _boom)
    override = O._speech_override(cfg, S.SPEECH_IN)
    assert override == {}, override
    errors = S.resolution_errors()
    assert errors[S.SPEECH_IN]["code"] == "BOOM", errors
    assert "resolver exploded" in errors[S.SPEECH_IN]["detail"]

    from voicebridge import runtime_inventory as RI
    assert RI._voice(cfg)["profile_errors"][S.SPEECH_IN]["code"] == "BOOM"
    _clear()


def test_executable_default_records_no_error(tmp_path, monkeypatch):
    from voicebridge import orchestrator as O
    from voicebridge.providers import speech as S

    cfg = _cfg(tmp_path, monkeypatch)
    _clear()
    # A default install resolves an executable local shipped/role default:
    # the hardware path is unchanged and no typed error is recorded.
    O._speech_override(cfg, S.SPEECH_IN)
    assert S.resolution_errors() == {}, S.resolution_errors()
