"""``POST /api/profiles/{name}/describe-auto`` is a real ported handler.

Wave 17: the route was converted from a typed refusal to the real reference
implementation (``web_routers/profiles.py`` / ``qubit_cli/profile_describer.py``).
It validates the profile name, resolves the profile home, and runs the
auxiliary-LLM describer. A failed generation is reported inline
(``ok: false`` + ``reason``), never as an HTTP error, so the UI can surface it
and let the operator retry.
"""
from __future__ import annotations


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _dispatch(cfg, path, body):
    from voicebridge import qubit_compat as HC

    return HC.dispatch(cfg, "POST", path, {}, body)


def test_describe_auto_unknown_profile_is_typed_404(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    code, out = _dispatch(cfg, "/api/profiles/does-not-exist/describe-auto", {})
    assert code == 404 and out["code"] == "PROFILE_NOT_FOUND", (code, out)
    assert out.get("qubit_counterpart"), out


def test_describe_auto_bad_name_is_typed_400(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    code, out = _dispatch(cfg, "/api/profiles/../etc/describe-auto", {})
    assert code in (400, 404), (code, out)
    assert out.get("ok") is False, out


def test_describe_auto_runs_the_real_describer(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    # Create a real profile (confirm-gated write), then describe it.
    code, out = _dispatch(cfg, "/api/profiles", {"name": "probe", "confirm": True})
    assert code == 200, (code, out)

    code, out = _dispatch(cfg, "/api/profiles/probe/describe-auto", {})
    # The handler always answers 200; success or an inline reason, never a
    # refusal and never a 5xx.
    assert code == 200, (code, out)
    assert isinstance(out.get("ok"), bool), out
    assert "reason" in out and "description" in out, out
    assert out.get("description_auto") == out.get("ok"), out
