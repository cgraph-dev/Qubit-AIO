"""D5 — Qubit code-side editor/acp/lsp rows through the shared surfaces.

The editor integration has no second agent authority and never downloads
unpinned binaries, so the safe Qubit equivalent of the upstream ACP serve /
accept-hooks / LSP install operations is a typed refusal; the real,
read-only lifecycle is ``acp check`` + ``lsp list/status/lifecycle/which``.
Every test drives the production surface (dashboard ``run_extra_op``, the
voice backend, or the backend directly) and asserts the typed state — never a
status tile. Hermetic (no PATH execution, no network, no writes).
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


def test_d5_dashboard_editor_refusals_are_typed(tmp_path, monkeypatch):
    from voicebridge import acp_lsp as AL
    from voicebridge import dashboard_ops as OPS

    cfg = _cfg(tmp_path, monkeypatch)
    for op_id, expected in (
        ("acp_serve", AL.acp_refuse_serve()),
        ("acp_accept_hooks", AL.acp_refuse_accept_hooks()),
    ):
        out = OPS.run_extra_op(cfg, op_id, {}, actor="dashboard:user")
        assert out["state"] == "executed", (op_id, out)
        assert out["receipt"]["result"] == expected, (op_id, out)
        assert expected["state"] in ("REFUSED", "REJECTED")
        assert expected["owner"] == "qubit-policy"

    refused = OPS.run_extra_op(cfg, "lsp_install", {"server": "gopls"},
                               actor="dashboard:user")
    assert refused["state"] == "executed", refused
    result = refused["receipt"]["result"]
    assert result == AL.lsp_refuse_install("gopls")
    assert result["code"] == AL.NO_DOWNLOADS_CODE
    assert "gopls" in result["server"]


def test_d5_voice_backend_editor_refusals_match_dashboard(tmp_path, monkeypatch):
    from voicebridge import acp_lsp as AL
    from voicebridge import voice_commands as VC

    serve = VC._b_acp(None, {"action": "serve"})["refused"]
    assert serve == AL.acp_refuse_serve() == AL.acp_serve_refusal()
    assert serve["state"] == "REFUSED" and serve["code"] == AL.NO_SERVER_CODE

    hooks = VC._b_acp(None, {"action": "accept_hooks"})["refused"]
    assert hooks == AL.acp_refuse_accept_hooks() == \
        AL.acp_accept_hooks_refusal()
    assert hooks["state"] == "REJECTED"
    assert hooks["code"] == AL.ACCEPT_HOOKS_CODE

    install = VC._b_lsp(None, {"action": "install", "server": "gopls"})[
        "refused"]
    assert install == AL.lsp_refuse_install("gopls")
    assert install["code"] == AL.NO_DOWNLOADS_CODE


def test_d5_acp_check_is_read_only_and_setup_never_writes(tmp_path,
                                                          monkeypatch):
    from voicebridge import acp_lsp as AL

    cfg_dir = tmp_path / "home"
    cfg_dir.mkdir()
    monkeypatch.setenv("HOME", str(cfg_dir))

    check = AL.acp_check()
    assert check["state"] == "OK"
    assert check["server"] == "ABSENT"
    assert {e["editor"] for e in check["editors"]} == \
        {"vscode", "zed", "jetbrains"}
    # The probe reads presence only: no editor config is created.
    assert not (cfg_dir / ".config" / "Code" / "User" / "settings.json").exists()

    zed = AL.acp_setup_snippet("zed")
    assert zed["state"] == "OK" and zed["editor"] == "zed"
    assert "127.0.0.1:18789" in zed["snippet"]
    assert "never edits live editor config" in zed["handoff"]
    assert not (cfg_dir / ".config" / "zed" / "settings.json").exists()

    unknown = AL.acp_setup_snippet("emacs")
    assert unknown["state"] == "UNKNOWN"
    assert unknown["code"] == AL.UNKNOWN_EDITOR_CODE


def test_d5_lsp_lifecycle_is_honest_and_never_spawns(monkeypatch):
    from voicebridge import acp_lsp as AL

    listed = AL.lsp_list()
    assert listed["state"] == "OK"
    assert listed["layer"] == "ABSENT"
    assert len(listed["servers"]) == len(AL.KNOWN_LSP)
    for row in listed["servers"]:
        assert row["managed"].startswith("externally")

    status = AL.lsp_status()
    assert status["layer"] == "ABSENT"
    assert status["active_clients"] == "none"
    assert status["lifecycle"] == AL.LSP_ACTIVE_STATE
    assert status["lifecycle_states"] == list(AL.LSP_LIFECYCLE_STATES)

    lifecycle = AL.lsp_lifecycle("pyright")
    assert lifecycle["state"] == "OK"
    assert [s["server"] for s in lifecycle["servers"]] == ["pyright"]
    assert lifecycle["servers"][0]["lifecycle"] == "STOPPED"
    assert lifecycle["servers"][0]["clients"] == 0

    # start/stop/restart never claim a client; install never downloads.
    for out in (AL.lsp_start("pyright"), AL.lsp_stop("pyright"),
                AL.lsp_restart()):
        assert out["state"] == "NO_CLIENTS"
    assert AL.lsp_which("no-such-server")["state"] == "UNKNOWN"
    assert AL.lsp_lifecycle("no-such-server")["state"] == "UNKNOWN"
