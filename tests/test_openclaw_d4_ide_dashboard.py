"""D4 — OpenClaw ide/dashboard rows closed by real capability + negative.

* OC-59 LSP layer — ``voicebridge/acp_lsp.py`` Qubit-native equivalent:
  read-only PATH inventory + explicit lifecycle state, no persistent client
  is spawned, and binary downloads are typed-refused.
* OC-D20 PDF + transcripts — ``voicebridge/doc_extract.py`` with the
  zero-dependency stdlib PDF text path and bounded transcript segments.
* OC-69 dashboard compat routes + operations console — routes registered and
  dispatched through the shared broker and real registry operations.
* OC-70 Control-UI boards/tabs/widgets — ``voicebridge/kanban.py`` tab and
  sandboxed widget kinds (including the inert ``plugin`` widget kind).
"""
from __future__ import annotations

import json

import pytest

import voicebridge.doc_extract as DE
from voicebridge import acp_lsp as AL
from voicebridge import kanban as KB


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _project(cfg, tmp_path, name="Site"):
    from voicebridge import projects as PJ

    folder = tmp_path / "work" / "site"
    folder.mkdir(parents=True, exist_ok=True)
    created = PJ.create(cfg, name, [str(folder)], requested_by="cli:user",
                        confirmed=True)
    slug = created["project"]["slug"]
    PJ.use(cfg, slug, requested_by="cli:user", confirmed=True)
    return folder


# ------------------------------------------------------------------ OC-59
def test_oc59_lsp_inventory_and_lifecycle_are_read_only(monkeypatch):
    import shutil
    import subprocess

    def _no_spawn(*a, **k):
        raise AssertionError("inventory must use PATH only, never spawn")

    monkeypatch.setattr(subprocess, "run", _no_spawn)
    monkeypatch.setattr(subprocess, "Popen", _no_spawn)
    listing = AL.lsp_list()
    assert listing["layer"] == "ABSENT"
    assert len(listing["servers"]) >= 10
    status = AL.lsp_status()
    assert status["active_clients"] == "none"
    assert status["lifecycle"] == AL.LSP_ACTIVE_STATE
    lifecycle = AL.lsp_lifecycle("gopls")
    assert lifecycle["servers"][0]["clients"] == 0
    assert AL.lsp_lifecycle("cobol-ls-9000")["code"] == AL.UNKNOWN_SERVER_CODE
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    assert AL.lsp_which("gopls")["state"] == "PRESENT"


def test_oc59_no_clients_and_no_downloads_fail_closed():
    assert AL.lsp_start("gopls")["code"] == AL.NO_CLIENTS_CODE
    assert AL.lsp_stop("gopls")["code"] == AL.NO_CLIENTS_CODE
    assert AL.lsp_restart()["state"] == "NO_CLIENTS"
    refused = AL.lsp_refuse_install("gopls")
    assert refused["code"] == AL.NO_DOWNLOADS_CODE
    assert refused["owner"] == "qubit-policy"
    # ACP serve / hook auto-approval would duplicate or bypass the broker.
    assert AL.acp_refuse_serve()["code"] == AL.NO_SERVER_CODE
    assert AL.acp_refuse_accept_hooks()["code"] == AL.ACCEPT_HOOKS_CODE


# ----------------------------------------------------------------- OC-D20
def test_ocd20_document_status_kinds_and_transcripts(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    folder = _project(cfg, tmp_path)
    assert DE.status(cfg)["state"] == "OK"
    kinds = DE.kinds(cfg)["kinds"]
    assert "transcript" in kinds and "pdf" in kinds
    (folder / "meeting.transcript").write_text(
        "[00:00:01] Alice: hello there\n"
        "00:00:07.000 --> 00:00:09.000\n"
        "Bob: bye now\n")
    out = DE.extract(cfg, str(folder / "meeting.transcript"), confirmed=True)
    assert out["kind"] == "transcript"
    assert [s["time"] for s in out["segments"]] == ["00:00:01", "00:00:07.000"]
    assert "hello there" in DE.render(out)


def test_ocd20_extract_confirmation_escape_and_empty_pdf(tmp_path,
                                                         monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    folder = _project(cfg, tmp_path)
    (folder / "notes.txt").write_text("hello\n")
    with pytest.raises(DE.DocExtractError) as exc:
        DE.extract(cfg, str(folder / "notes.txt"))
    assert exc.value.code == DE.CONFIRM_CODE
    outside = tmp_path / "outside.txt"
    outside.write_text("private\n")
    with pytest.raises(DE.DocExtractError) as exc:
        DE.extract(cfg, str(outside), confirmed=True)
    assert exc.value.code == DE.ESCAPE_CODE
    (folder / "empty.pdf").write_bytes(b"%PDF-1.4\nno streams here\n")
    monkeypatch.setattr(DE, "PDF_BACKENDS", (("stdlib", "stdlib"),))
    assert DE.status(cfg)["pdf"]["available"] is True
    with pytest.raises(DE.DocExtractError) as exc:
        DE.extract(cfg, str(folder / "empty.pdf"), confirmed=True)
    assert exc.value.code == DE.BAD_INPUT_CODE


# ------------------------------------------------------------------ OC-69
def test_oc69_compat_routes_and_real_registry_dispatch(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    route, _ = HC.match("GET", "/api/analytics")
    assert route is not None
    status, _body = HC.dispatch(cfg, "GET", "/api/analytics")
    assert status != 404
    status, body = HC.dispatch(cfg, "GET", "/api/d4-not-a-route")
    assert status == 404 and body.get("code") == "NOT_FOUND"

    catalog = {o["id"]: o for o in OPS.catalog()}
    for op_id in ("usage_summary", "receipts_ledger", "policy_conformance",
                  "egress_check", "computer_screen"):
        assert op_id in catalog, op_id
    out = OPS.run_op(cfg, "usage_summary", {}, actor="dashboard:user",
                     session="dashboard")
    assert out["state"] == "executed"
    assert out["receipt"]["entry"] == "usage_summary"


# ------------------------------------------------------------------ OC-70
def test_oc70_board_tabs_and_sandboxed_widget_kinds(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    KB.boards_create(cfg, "wave", "Wave", requested_by="cli:user",
                     confirmed=True)
    tab = KB.tab_create(cfg, "wave", "alpha", "Alpha", requested_by="cli:user",
                        confirmed=True)
    assert tab["state"] == "OK"
    assert any(t["slug"] == "alpha" for t in KB.tab_list(cfg, "wave")["tabs"])

    note = KB.widget_add(cfg, "wave", kind="note", title="Note",
                         content="# TODO", tab="alpha", confirmed=True)
    assert note["state"] == "OK" and note["widget"]["kind"] == "note"

    # markup widgets are stored inert (never executed server-side)
    html = KB.widget_add(cfg, "wave", kind="html", title="HTML",
                         content="<script>alert(1)</script>", confirmed=True)
    assert html["widget"]["trust"] == "inert"

    # plugin widget requires the review-only plugin:read grant
    with pytest.raises(KB.KanbanError) as exc:
        KB.widget_add(cfg, "wave", kind="plugin", title="Plugin",
                      content=json.dumps({"name": "acme.panel",
                                          "version": "1.0.0", "props": {}}),
                      confirmed=True)
    assert exc.value.code == KB.DENIED_CODE
    ok = KB.widget_add(cfg, "wave", kind="plugin", title="Plugin",
                       content=json.dumps({"name": "acme.panel",
                                           "version": "1.0.0", "props": {}}),
                       declared_grants=["plugin:read"], confirmed=True)
    assert ok["state"] == "OK" and ok["widget"]["kind"] == "plugin"
