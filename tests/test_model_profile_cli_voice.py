"""Impl-1 — canonical model-profile CLI + voice surface.

Exercises the production resolver through ``qubit profile model ...`` and the
spoken model-profile intents. Hermetic: a temp XDG config/state tree, no
provider, network, model, or audio touched. Proves dry-run vs --confirm,
session selection, typed raw-secret refusal, and that voice never accepts,
echoes, or logs a key value.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _run(cfg, argv, capsys):
    from voicebridge import cli as C

    ns = C.build_parser().parse_args(argv)
    rc = C._cmd_profile(cfg, ns)
    out = capsys.readouterr().out.strip()
    data = json.loads(out) if out.startswith("{") else {}
    return rc, data


_API_CREATE = [
    "profile", "model", "create", "--id", "research",
    "--kind", "api", "--provider", "openai", "--model", "gpt-4o",
    "--base-url", "https://api.openai.com/v1",
    "--key-ref", "env:OPENAI_API_KEY", "--egress-consent", "allow",
]


# ------------------------------------------------------------------ parser
def test_parser_builds_model_profile_family():
    from voicebridge import cli as C

    parser = C.build_parser()
    for sub in ("list", "show", "test", "create", "clone", "edit", "activate",
                "deactivate", "rollback", "remove"):
        extra = []
        if sub in ("show", "test", "activate", "rollback", "remove", "edit"):
            extra = ["x"]
        elif sub == "clone":
            extra = ["a", "b"]
        elif sub == "create":
            extra = ["--id", "x"]
        ns = parser.parse_args(["profile", "model", sub] + extra)
        assert ns.profile_command == "model", sub
        assert ns.profile_model_command == sub, sub
    ns = parser.parse_args(_API_CREATE)
    assert ns.id == "research" and ns.key_ref == "env:OPENAI_API_KEY"
    ns = parser.parse_args(
        ["profile", "model", "list", "--json"])
    assert ns.json is True


# ------------------------------------------------------------ list / show
def test_list_and_show_via_temp_config(tmp_path, monkeypatch, capsys):
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch)
    rc, data = _run(cfg, ["profile", "model", "list", "--json"], capsys)
    assert rc == 0
    assert data["count"] == 1
    assert data["profiles"][0]["id"] == R.DEFAULT_LOCAL_ID

    rc, data = _run(cfg, _API_CREATE + ["--confirm"], capsys)
    assert rc == 0 and data["action"] == "persisted"

    rc, data = _run(cfg, ["profile", "model", "list", "--json"], capsys)
    ids = {p["id"] for p in data["profiles"]}
    assert ids == {R.DEFAULT_LOCAL_ID, "research"}

    rc, data = _run(cfg, ["profile", "model", "show", "research"], capsys)
    assert rc == 0
    assert data["profile"]["id"] == "research"
    assert data["profile"]["key_ref"] == "env:OPENAI_API_KEY"
    assert data["gate"]["state"] == "OK"


# --------------------------------------------------- secret refusal
def test_create_refuses_raw_secret(tmp_path, monkeypatch, capsys):
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch)
    raw = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
    rc, data = _run(cfg, [
        "profile", "model", "create", "--id", "leaky", "--kind", "api",
        "--provider", "openai", "--model", "gpt-4o",
        "--base-url", "https://api.openai.com/v1", "--key-ref", raw,
    ] + ["--confirm"], capsys)
    assert rc == 2
    assert data["state"] == "FAILED"
    assert data["code"] == "RAW_SECRET_REFUSED"
    assert "env:NAME" in data["detail"] and "file:PATH" in data["detail"]
    assert R.get_profile(cfg, "leaky") is None
    reg = R._registry_file(cfg)
    if reg is not None and reg.exists():
        assert raw not in reg.read_text()

    # a wrong-but-not-secret shaped ref is also typed.
    rc, data = _run(cfg, [
        "profile", "model", "create", "--id", "badref", "--kind", "api",
        "--provider", "openai", "--model", "gpt-4o",
        "--base-url", "https://api.openai.com/v1", "--key-ref", "justatext",
    ], capsys)
    assert rc == 2 and data["code"] == "BAD_SECRET_REF"


# ------------------------------------------- dry-run vs confirm + session
def test_create_activate_dry_run_vs_confirm(tmp_path, monkeypatch, capsys):
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch)

    rc, data = _run(cfg, _API_CREATE, capsys)
    assert rc == 0 and data["dry_run"] is True
    assert R.get_profile(cfg, "research") is None

    rc, data = _run(cfg, _API_CREATE + ["--confirm"], capsys)
    assert rc == 0 and data["action"] == "persisted"
    assert R.get_profile(cfg, "research") is not None

    rc, data = _run(cfg, ["profile", "model", "activate", "research",
                          "--session", "s1"], capsys)
    assert rc == 0 and data["dry_run"] is True
    assert R.resolve(cfg, role="chat", session="s1").profile_id == \
        R.DEFAULT_LOCAL_ID

    rc, data = _run(cfg, ["profile", "model", "activate", "research",
                          "--session", "s1", "--confirm"], capsys)
    assert rc == 0 and data["action"] == "selected"
    assert R.resolve(cfg, role="chat", session="s1").profile_id == "research"


def test_session_select_and_deactivate(tmp_path, monkeypatch, capsys):
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch)
    _run(cfg, _API_CREATE + ["--confirm"], capsys)
    _run(cfg, ["profile", "model", "activate", "research", "--session", "s2",
               "--confirm"], capsys)
    assert R.resolve(cfg, role="chat", session="s2").profile_id == "research"

    rc, data = _run(cfg, ["profile", "model", "deactivate", "--session", "s2",
                          "--role", "*"], capsys)
    assert rc == 0 and data["dry_run"] is True
    assert R.resolve(cfg, role="chat", session="s2").profile_id == "research"

    rc, data = _run(cfg, ["profile", "model", "deactivate", "--session", "s2",
                          "--role", "*", "--confirm"], capsys)
    assert rc == 0 and data["changed"] is True
    assert R.resolve(cfg, role="chat", session="s2").profile_id == \
        R.DEFAULT_LOCAL_ID


def test_edit_and_immutable_default(tmp_path, monkeypatch, capsys):
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch)
    _run(cfg, _API_CREATE + ["--confirm"], capsys)
    rc, data = _run(cfg, ["profile", "model", "edit", "research",
                          "--model", "gpt-4o-mini", "--roles",
                          "chat,coding", "--confirm"], capsys)
    assert rc == 0 and data["action"] == "persisted"
    edited = R.get_profile(cfg, "research")
    assert edited.model == "gpt-4o-mini"
    assert edited.roles == ["chat", "coding"]

    rc, data = _run(cfg, ["profile", "model", "edit", R.DEFAULT_LOCAL_ID,
                          "--model", "nope", "--confirm"], capsys)
    assert rc == 2 and data["code"] == "IMMUTABLE_DEFAULT"


def test_clone_rollback_and_remove(tmp_path, monkeypatch, capsys):
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch)
    _run(cfg, _API_CREATE + ["--confirm"], capsys)

    rc, data = _run(cfg, ["profile", "model", "clone", "research",
                          "research-canary", "--confirm"], capsys)
    assert rc == 0 and data["action"] == "persisted"
    cloned = R.get_profile(cfg, "research-canary")
    assert cloned.rollback_to == "research"

    rc, data = _run(cfg, ["profile", "model", "rollback", "research-canary",
                          "--session", "s3", "--confirm"], capsys)
    assert rc == 0 and data["action"] == "selected"
    assert R.resolve(cfg, role="chat", session="s3").profile_id == "research"

    # the shipped default is immutable.
    rc, data = _run(cfg, ["profile", "model", "remove", R.DEFAULT_LOCAL_ID,
                          "--confirm"], capsys)
    assert rc == 2 and data["code"] == "IMMUTABLE_DEFAULT"


# ------------------------------------------------------- voice resolution
_VOICE_INTENTS = {
    "use my local brain": "model_profile_local",
    "use my research model for this session": "model_profile_use",
    "what model and voice are active?": "model_profile_status",
    "use the realtime voice profile": "model_profile_realtime",
    "run this task with my coding model": "model_profile_coding",
    "return to local-only mode": "model_profile_local_only",
}


def test_voice_intents_resolve_to_their_backend():
    from voicebridge import voice_commands as VC

    for text, backend in _VOICE_INTENTS.items():
        res = VC.resolve(text)
        assert res.kind == VC.MATCH, (text, res.kind, res.speech)
        assert res.entry is not None and res.entry.backend == backend, text
        assert res.entry.id in VC.MODEL_PROFILE_REGISTRY
        assert res.entry.scope == ("model-profile",)

    # PRB-05: the speech-profile lifecycle lives in the main REGISTRY (not the
    # model-profile registry) and still resolves through the speech resolver.
    speech = VC.REGISTRY.get("speech_profile_test")
    assert speech is not None and speech.backend == "speech_profile_test"
    res = VC.resolve("test my speech profile")
    assert res.kind == VC.MATCH and res.entry.id == "speech_profile_test"


def test_voice_selection_is_confirm_gated_and_applies(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC
    from voicebridge.providers import resolver as R

    cfg = _cfg(tmp_path, monkeypatch)
    out = VC.voice_say(cfg, "use my local brain", session="v1")
    assert out["state"] == "needs-confirm"
    assert out["entry"] == "model_profile_local"
    assert out.get("confirmation_id")

    # nothing persisted until the shared journal confirmation.
    ctx = VC.make_ctx(cfg, actor="voice:user", session="v1", source="user")
    done = VC.confirm_pending(ctx, out["confirmation_id"])
    assert done.kind == VC.EXECUTED
    assert R.resolve(cfg, role="chat", session="v1").profile_id == \
        R.DEFAULT_LOCAL_ID


def test_voice_never_accepts_or_echoes_a_raw_secret(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    raw = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
    out = VC.voice_say(
        cfg, f"use my {raw} model for this session", session="v2")
    assert out["state"] == "failed"
    blob = json.dumps(out)
    assert raw not in blob
    assert "env:NAME" in out["speech"] and "file:PATH" in out["speech"]

    # no model-profile registry file was written and no session override
    # references the secret.
    from voicebridge.providers import resolver as R

    reg = R._registry_file(cfg)
    if reg is not None and reg.exists():
        assert raw not in reg.read_text()
    sess = R._sessions_path(cfg)
    if sess.exists():
        assert raw not in sess.read_text()


# --------------------------------------------------- speech profile intents
def test_speech_profile_test_uses_speech_registry(tmp_path, monkeypatch):
    """'test my speech profile' must inspect the speech registry, not the
    language-model registry (V1 fix)."""
    from voicebridge import voice_commands as VC
    from voicebridge.providers import speech as SP

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = VC.make_ctx(cfg, actor="voice:user", session="sp1", source="user")
    out = VC._BACKENDS["speech_profile_test"](ctx, {})
    mp = out["model_profile"]
    assert mp["state"] == "OK", mp
    assert mp["kind"] == SP.SPEECH_OUT, mp


def test_realtime_intent_selects_shipped_speech_profile(tmp_path, monkeypatch):
    """The realtime intent resolves the versioned realtime speech profile, not
    a language-model kind (V1 fix)."""
    from voicebridge import voice_commands as VC
    from voicebridge.providers import speech as SP

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = VC.make_ctx(cfg, actor="voice:user", session="rt1", source="user")
    out = VC._BACKENDS["model_profile_realtime"](ctx, {})
    mp = out["model_profile"]
    assert mp["state"] == "OK", mp
    assert mp["kind"] == SP.REALTIME, mp
    assert mp["profile"] == SP.DEFAULT_SPEECH_IDS[SP.REALTIME], mp
    assert SP.resolve_realtime(cfg, session="rt1").profile_id == \
        SP.DEFAULT_SPEECH_IDS[SP.REALTIME]
