"""9U — OpenClaw content-family handlers are real delegates, not stubs.

Two independent proofs:

1. static (AST) — every method this family registers as an implementation
   (non-trivial handler body) is reached by the manifest's own behavior proof,
   and every registered typed refusal actually emits its declared code;
2. runtime — the live handlers are invoked in-process against a temp state dir
   and their real behavior/typed codes are asserted directly.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import qubit_upgrade_manifest as OUM  # noqa: E402

FAMILY_MODULE = "voicebridge/gateway/qubit_handlers/content.py"
CONTENT = REPO_ROOT / FAMILY_MODULE

#: The typed refusals this family registers (method -> emitted code).
REFUSALS = {
    "projects.searchRemote": "PROJECT_REMOTE_SEARCH_OPERATOR_REQUIRED",
    "channels.start": "CHANNEL_LISTENER_OPERATOR_REQUIRED",
    "channels.stop": "CHANNEL_LISTENER_OPERATOR_REQUIRED",
    "channels.logout": "CHANNEL_LISTENER_OPERATOR_REQUIRED",
    "models.authLogout": "AUTH_LOGOUT_MANUAL_HANDOFF",
    "push.test": "PUSH_WEB_OPERATOR_REQUIRED",
    "push.web.vapidPublicKey": "PUSH_WEB_OPERATOR_REQUIRED",
    "push.web.subscribe": "PUSH_WEB_OPERATOR_REQUIRED",
    "push.web.unsubscribe": "PUSH_WEB_OPERATOR_REQUIRED",
    "push.web.test": "PUSH_WEB_OPERATOR_REQUIRED",
    "push.web.preferences.get": "PUSH_WEB_OPERATOR_REQUIRED",
    "push.web.preferences.set": "PUSH_WEB_OPERATOR_REQUIRED",
    "tools.github.status": "GITHUB_OPERATOR_REQUIRED",
    "tools.github.configure": "GITHUB_OPERATOR_REQUIRED",
    "tools.github.authorize.start": "GITHUB_OPERATOR_REQUIRED",
    "tools.github.authorize.poll": "GITHUB_OPERATOR_REQUIRED",
    "tools.github.authorize.cancel": "GITHUB_OPERATOR_REQUIRED",
}

#: The real IMPLEMENTED handlers this family adds on top of the pinned reads.
IMPLEMENTED = (
    "cron.add", "cron.remove", "cron.run",
    "projects.list", "projects.add", "projects.register", "projects.remove",
    "skills.status", "skills.search", "skills.detail", "skills.skillCard",
    "skills.curator.status", "skills.proposals.list", "skills.proposals.inspect",
    "skills.proposals.historyStatus", "skills.proposals.create",
    "skills.library.list", "skills.library.read", "skills.library.save",
    "skills.library.mutate", "skills.library.import", "skills.library.upload",
    "skills.library.activate", "skills.install",
    "plugins.install", "plugins.uninstall", "plugins.setEnabled",
    "models.list", "models.authStatus", "models.probe",
    "tts.status", "tts.providers", "tts.personas", "tts.speak", "tts.convert",
    "talk.catalog", "talk.speak", "talk.config",
    "channels.status",
    "channels.pairing.list", "channels.pairing.approve",
    "channels.pairing.dismiss",
    "commands.list", "voicewake.get",
    "mcp.app.view", "mcp.app.listTools",
    "board.get", "board.widget.appView", "board.widget.put", "board.data.read",
    "canvas.document.view",
)


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    home = tmp_path / "home" / ".hermes"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("QUBIT_HOME", str(home))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _registry():
    from voicebridge.gateway import rpc as R
    return R.default_registry()


# ---------------------------------------------------------------- static proof
def test_registered_implementations_pass_manifest_behavior_proof():
    sources = OUM.registry_sources(REPO_ROOT / "voicebridge" / "gateway"
                                   / "rpc.py")
    methods, funcs, consts, origins = OUM._parse_rpc_handlers_multi(sources)
    registry = set(_registry().names())
    ours = {m for m, origin in origins.items()
            if origin.endswith("qubit_handlers/content.py")}
    assert ours, "content family registered no methods"
    assert ours <= registry, f"unregistered: {ours - registry}"
    for method in sorted(ours):
        node = methods[method]
        status_refusal = method in REFUSALS
        if status_refusal:
            assert not OUM._is_non_trivial(node, funcs), (
                f"{method} is a refusal but performs backend work; it would "
                "be misreported as IMPLEMENTED")
            assert OUM._emits_code(node, funcs, REFUSALS[method], consts), (
                f"{method} never emits its declared code {REFUSALS[method]!r}")
        else:
            assert OUM._is_non_trivial(node, funcs), (
                f"{method} is registered as an implementation but does no "
                "backend work")


# --------------------------------------------------------------- runtime proof
def test_cron_add_remove_run_round_trip(tmp_path, monkeypatch):
    from voicebridge.gateway.gateway_ws import GatewayError

    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()

    with pytest.raises(GatewayError) as exc:
        reg.handle(cfg, "cron.add", {"name": "j1", "when": "once in 10080 "
                                     "minutes", "task": "say hi"})
    assert exc.value.code == "SCHEDULES_CONFIRM_REQUIRED"

    added = reg.handle(cfg, "cron.add", {
        "name": "j1", "when": "once in 9999 minutes", "task": "say hi",
        "confirmed": True})
    job_id = added["job"]["id"]
    assert job_id > 0

    # not due yet -> a due pass runs nothing and fabricates no execution
    run = reg.handle(cfg, "cron.run", {"id": job_id, "mode": "due"})
    assert run["state"] == "OK" and run["ran"] == 0, run

    removed = reg.handle(cfg, "cron.remove", {"id": job_id, "confirmed": True})
    assert removed["deleted"] == job_id and removed["history_retained"] is True


def test_projects_models_tts_channels_commands_reads(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()

    projects = reg.handle(cfg, "projects.list", {})
    assert projects["state"] == "OK" and "projects" in projects

    models = reg.handle(cfg, "models.list", {})
    assert models["state"] == "OK"
    assert "models" in models and "profiles" in models

    auth = reg.handle(cfg, "models.authStatus", {})
    assert auth["schema"] == "qubit-auth-status/1"
    assert "providers" in auth

    tts_status = reg.handle(cfg, "tts.status", {})
    assert tts_status["state"] == "OK" and tts_status["kind"] == "speech_out"

    providers = reg.handle(cfg, "tts.providers", {})
    assert providers["state"] == "OK" and providers["providers"]
    assert providers["configured"]

    personas = reg.handle(cfg, "tts.personas", {})
    assert personas["state"] == "OK" and "profiles" in personas

    channels = reg.handle(cfg, "channels.status", {})
    assert channels["state"] in ("OK", "ESTOP") and "listener" in channels

    commands = reg.handle(cfg, "commands.list", {})
    assert commands["state"] == "OK" and commands["count"] > 0

    wake = reg.handle(cfg, "voicewake.get", {})
    assert wake["state"] in ("READY", "NO_MODEL")

    catalog = reg.handle(cfg, "talk.catalog", {})
    assert catalog["state"] == "OK" and catalog["count"] > 0


def test_skills_mcp_and_pairing_reads(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()

    skills = reg.handle(cfg, "skills.status", {})
    assert skills["state"] == "OK" and "skills" in skills

    search = reg.handle(cfg, "skills.search", {"query": "anything"})
    assert search["state"] == "OK" and search["recall"] == []

    # staging a mutation with no license is a real typed refusal, not a failure
    staged = reg.handle(cfg, "skills.proposals.create",
                        {"name": "demo-skill", "content": "body"})
    assert staged["code"] == "LICENSE_REQUIRED", staged

    curator = reg.handle(cfg, "skills.curator.status", {})
    assert curator["state"] == "OK" and "curator" in curator

    journal = reg.handle(cfg, "skills.proposals.historyStatus", {})
    assert journal["journal"]["state"] in ("OK", "EMPTY", "CORRUPT")

    mcp_view = reg.handle(cfg, "mcp.app.view", {})
    assert mcp_view["state"] == "OK" and "tools" not in mcp_view
    assert "servers" in mcp_view and "grants" in mcp_view

    mcp_tools = reg.handle(cfg, "mcp.app.listTools", {})
    assert mcp_tools["state"] == "OK" and "tools" in mcp_tools

    pairings = reg.handle(cfg, "channels.pairing.list", {})
    assert pairings["state"] == "OK" and pairings["pairings"] == []


def test_models_probe_rejects_unknown_provider(tmp_path, monkeypatch):
    from voicebridge.gateway.gateway_ws import GatewayError

    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()
    with pytest.raises(GatewayError) as exc:
        reg.handle(cfg, "models.probe", {"provider": "no-such-provider"})
    assert exc.value.code == "GATEWAY_BAD_PARAMS"


def test_board_canvas_round_trip(tmp_path, monkeypatch):
    from voicebridge import kanban as K
    from voicebridge.gateway.gateway_ws import GatewayError

    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()
    K.boards_create(cfg, "main", confirmed=True)

    board = reg.handle(cfg, "board.get", {"board": "main"})
    assert board["state"] == "OK" and board["board"] == "main"

    with pytest.raises(GatewayError) as exc:
        reg.handle(cfg, "board.widget.put", {"board": "main", "kind": "note",
                                             "title": "t"})
    assert exc.value.code == "KANBAN_CONFIRM_REQUIRED"

    placed = reg.handle(cfg, "board.widget.put", {
        "board": "main", "kind": "note", "title": "t", "content": "body",
        "confirmed": True})
    widget_id = placed["widget"]["id"]
    view = reg.handle(cfg, "board.widget.appView", {"widgetId": widget_id})
    assert view["state"] == "OK" and view["widget"]["id"] == widget_id


def test_new_content_reads(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()

    talk = reg.handle(cfg, "talk.config", {})
    assert talk["state"] == "OK"
    assert "profiles" in talk and "speech_out" in talk and "audio" in talk

    board = reg.handle(cfg, "board.data.read", {})
    assert board["state"] == "OK" and isinstance(board["snapshot"], dict)


def test_tts_convert_is_real_synthesis_not_a_stub(tmp_path, monkeypatch):
    from voicebridge.gateway.gateway_ws import GatewayError

    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()

    with pytest.raises(GatewayError) as exc:
        reg.handle(cfg, "tts.convert", {})
    assert exc.value.code == "GATEWAY_BAD_PARAMS"

    # A real local TTS adapter yields a data URL; an absent provider/model is a
    # typed AUDIO_TTS_UNAVAILABLE, never a fabricated clip.
    try:
        out = reg.handle(cfg, "tts.convert", {"text": "hello"})
    except GatewayError as exc:
        assert exc.code in ("AUDIO_TTS_UNAVAILABLE", "GATEWAY_METHOD_FAILED")
    else:
        assert out["state"] == "OK" and out["data_url"].startswith("data:")


def test_plugins_set_enabled_routes_through_confirmation(tmp_path, monkeypatch):
    from voicebridge.gateway.gateway_ws import GatewayError

    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()

    with pytest.raises(GatewayError) as exc:
        reg.handle(cfg, "plugins.setEnabled", {})
    assert exc.value.code == "GATEWAY_BAD_PARAMS"

    staged = reg.handle(cfg, "plugins.setEnabled",
                        {"name": "some-tool", "enabled": False})
    assert staged["code"] == "CONFIRM_REQUIRED"
    assert staged["confirmation_id"]


def test_skills_library_mutations_are_real_typed_results(tmp_path, monkeypatch):
    from voicebridge.gateway.gateway_ws import GatewayError

    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()

    saved = reg.handle(cfg, "skills.library.save",
                       {"name": "demo", "content": "body"})
    assert saved["code"] == "LICENSE_REQUIRED", saved

    mutated = reg.handle(cfg, "skills.library.mutate",
                         {"action": "create", "name": "demo", "content": "b"})
    assert mutated["code"] == "LICENSE_REQUIRED", mutated

    with pytest.raises(GatewayError) as exc:
        reg.handle(cfg, "skills.library.mutate", {"action": "bogus"})
    assert exc.value.code == "GATEWAY_BAD_PARAMS"

    with pytest.raises(GatewayError) as exc:
        reg.handle(cfg, "skills.library.activate", {"id": "nope"})
    assert exc.value.code == "CONFIRMATION_REQUIRED"

    with pytest.raises(GatewayError) as exc:
        reg.handle(cfg, "skills.library.activate",
                   {"id": "nope", "confirmed": True})
    assert exc.value.code == "GATEWAY_BAD_PARAMS"


def test_refusals_emit_typed_codes(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()
    for method, code in REFUSALS.items():
        out = reg.handle(cfg, method, {})
        assert out["code"] == code, (method, out)


def test_registry_composes_content_family(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()
    for method in REFUSALS:
        assert method in reg.names()
    for method in IMPLEMENTED:
        assert method in reg.names(), method
