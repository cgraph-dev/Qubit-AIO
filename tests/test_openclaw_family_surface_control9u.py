"""9U — OpenClaw surface-control family (controlUi + tools.invoke).

Static behavior proof (the manifest's own checks) plus runtime proof against the
canonical authorities: the one chat-session store
(``voicebridge.qubit_session_store``), the shared subscription registry
(``voicebridge.session_subscriptions``), and the canonical ``CapabilityBroker``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import openclaw_upgrade_manifest as OUM  # noqa: E402

MODULE = "voicebridge/gateway/openclaw_handlers/surface_control.py"

IMPL = ("controlUi.sessionPreview", "tools.invoke")
SAFE = {
    "controlUi.sessionPullRequests.subscribe":
        "CONTROLUI_SESSION_PR_SUBSCRIBE_LOCAL_REGISTRY",
}
OPS: dict = {}


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _registry():
    from voicebridge.gateway import rpc as R
    return R.default_registry()


def test_family_static_proof():
    sources = OUM.registry_sources(REPO_ROOT / "voicebridge" / "gateway"
                                   / "rpc.py")
    methods, funcs, consts, origins = OUM._parse_rpc_handlers_multi(sources)
    registry = set(_registry().names())
    ours = {m for m, origin in origins.items() if origin.endswith(MODULE)}
    assert ours == set(IMPL) | set(SAFE) | set(OPS), ours
    assert ours <= registry, f"unregistered: {ours - registry}"
    for method in sorted(ours):
        node = methods[method]
        code = SAFE.get(method)
        if code:
            assert OUM._emits_code(node, funcs, code, consts), (
                f"{method} never emits {code!r}")
        else:
            assert OUM._is_non_trivial(node, funcs), f"{method} does no work"


def test_manifest_dispositions_match_family():
    rows = json.loads((REPO_ROOT / "docs" / "archive" / "2026-09-21"
                       / "QUBIT_OPENCLAW_UPGRADE_MANIFEST.json"
                       ).read_text())["pinned_surface_disposition"]["methods"]
    for m in IMPL:
        assert rows[m]["status"] == "IMPLEMENTED", rows[m]
        assert rows[m]["evidence"] == MODULE, rows[m]
    for m, code in SAFE.items():
        assert rows[m]["status"] == "SAFE_EQUIVALENT", rows[m]
        assert rows[m]["code"] == code, rows[m]


# --------------------------------------------------------- sessionPreview
def test_session_preview_projects_the_canonical_store(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    from voicebridge.qubit_session_store import QubitSessionStore

    store = QubitSessionStore(cfg)
    store.create("Preview target", session_id="sess-preview")
    store.append("sess-preview", "user", "hello world")
    out = _registry().handle(cfg, "controlUi.sessionPreview",
                             {"sessionKey": "sess-preview"})
    assert out["status"] == "ok", out
    assert out["sessionKey"] == "sess-preview", out
    assert out["lastMessagePreview"] == "hello world", out
    assert out["messageCount"] >= 1, out


def test_session_preview_missing_is_unavailable_not_error(tmp_path,
                                                          monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out = _registry().handle(cfg, "controlUi.sessionPreview",
                             {"sessionKey": "does-not-exist"})
    assert out["status"] == "unavailable", out


# ------------------------------------------------- sessionPullRequests.subscribe
def test_session_pr_subscribe_uses_the_shared_registry(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    from voicebridge import session_subscriptions as SS

    SS.REGISTRY.reset()
    out = _registry().handle(
        cfg, "controlUi.sessionPullRequests.subscribe",
        {"sessionKeys": ["sess-a", "sess-b"]})
    assert out["subscribed"] is True, out
    assert out["code"] == "CONTROLUI_SESSION_PR_SUBSCRIBE_LOCAL_REGISTRY", out
    assert len(out["subscriptions"]) == 2, out
    assert {r["sessionId"] for r in SS.REGISTRY.list()} == {"sess-a", "sess-b"}
    SS.REGISTRY.reset()


# --------------------------------------------------------------- tools.invoke
def test_tools_invoke_unknown_tool_never_fabricates_success(tmp_path,
                                                            monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out = _registry().handle(cfg, "tools.invoke",
                             {"name": "no.such.capability", "args": {}})
    assert out["ok"] is False, out
    assert out["error"]["code"] in ("forbidden", "internal_error"), out


def test_tools_invoke_requires_a_name(tmp_path, monkeypatch):
    from voicebridge.gateway.gateway_ws import GatewayError

    cfg = _cfg(tmp_path, monkeypatch)
    try:
        _registry().handle(cfg, "tools.invoke", {})
    except GatewayError as exc:
        assert exc.code == "GATEWAY_BAD_PARAMS", exc.code
    else:  # pragma: no cover - must fail closed
        raise AssertionError("missing name must be a typed bad-params error")
