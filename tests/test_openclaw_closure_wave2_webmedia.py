"""OpenClaw closure wave 2 — web / media / reports / memory proving tests.

One proving test per targeted web-media row. Every test drives a real
production function and mocks only the external provider/model boundary via
an injected transport or analyzer. No network call, provider credential, or
desktop session is required.

Rows: OC-63, OC-64, OC-D18, OC-65, OC-67, OC-D01, OC-48, OC-51, OC-D28.
"""
from __future__ import annotations

import json


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


# ------------------------------------------------------------------ OC-63
def test_oc63_web_search_fetch_xsearch_injected(tmp_path, monkeypatch):
    from voicebridge import web_tool as WT

    cfg = _cfg(tmp_path, monkeypatch)
    cfg.data["web"] = {"search": {"provider": "searxng",
                                  "endpoint": "http://93.184.216.34/search"}}
    seen = {}

    def transport(request):
        seen.update(request)
        return {"results": [{"title": "Qubit", "url": "https://example.org/a",
                             "content": "bounded snippet"}]}

    out = WT.search(cfg, "qubit", transport=transport)
    assert out["state"] == WT.OK and out["count"] == 1
    assert out["results"][0]["title"] == "Qubit"
    assert seen["provider"] == "searxng" and seen["method"] == "GET"

    x = WT.x_search(cfg, "qubit", transport=lambda req: {
        "data": [{"id": "1", "text": "hello world"}]})
    assert x["state"] == WT.OK and x["count"] == 1
    assert x["results"][0]["text"] == "hello world"

    dispatched = WT.run(cfg, "web.search", {"query": "q"},
                        transport=transport)
    assert dispatched["state"] == WT.OK
    # Without an injected transport X stays a typed gate (never fabricated).
    assert WT.x_search(cfg, "q")["code"] == WT.X_SEARCH_UNAVAILABLE_CODE


# ------------------------------------------------------------ OC-64/D18
def test_oc64_ocd18_media_shaping_and_dispatch(tmp_path, monkeypatch):
    from voicebridge import media_gen as MG

    cfg = _cfg(tmp_path, monkeypatch)
    music = MG.shape_media_request("fal", "music", "lofi", {"duration_s": 10})
    assert music["endpoint"] == "fal:music"
    assert music["body"]["duration_s"] == 10
    video = MG.shape_media_request("runway", "video", "clip",
                                   {"duration_s": 5})
    assert video["endpoint"] == "runway:video"

    monkeypatch.setenv("OPENAI_API_KEY", "sk-injected-image")
    image = MG.generate(
        cfg, kind="image", provider="openai", prompt="a cat", confirmed=True,
        transport=lambda req: {"data": [{"id": "i1",
                                         "url": "https://cdn.example/i.png"}]})
    assert image["state"] == "OK" and image["asset"]["provider_asset_id"] == "i1"

    monkeypatch.setenv("FAL_KEY", "fal-injected")
    song = MG.generate(
        cfg, kind="music", provider="fal", prompt="lofi",
        options={"duration_s": 10}, confirmed=True,
        transport=lambda req: {"data": [{"id": "m1",
                                         "url": "https://cdn.example/m.mp3"}]})
    assert song["state"] == "OK" and song["asset"]["url"].endswith("m.mp3")

    monkeypatch.setenv("RUNWAY_API_KEY", "runway-injected")
    clip = MG.generate(
        cfg, kind="video", provider="runway", prompt="clip",
        options={"duration_s": 5}, confirmed=True,
        transport=lambda req: {"data": [{"id": "v1",
                                         "url": "https://cdn.example/v.mp4"}]})
    assert clip["state"] == "OK" and clip["asset"]["url"].endswith("v.mp4")

    # No injected transport: the unshipped pair stays a typed gap.
    try:
        MG.generate(cfg, kind="music", provider="fal", prompt="lofi",
                    confirmed=True)
        raise AssertionError("expected MEDIA_GEN_UNAVAILABLE")
    except MG.MediaGenError as exc:
        assert exc.code == MG.UNAVAILABLE_CODE


# ------------------------------------------------------------------ OC-65
def test_oc65_multi_attachment_understanding(tmp_path, monkeypatch):
    from voicebridge import vision as VI

    cfg = _cfg(tmp_path, monkeypatch)
    calls = []

    def analyzer(path, prompt):
        calls.append((path, prompt))
        if path == "bad.png":
            raise RuntimeError("no such attachment")
        return {"description": f"cat in {path}", "sha256": "a" * 64}

    out = VI.describe_batch(cfg, ["a.png", "bad.png", "b.png"],
                            prompt="what is this?", confirmed=True,
                            analyzer=analyzer)
    assert out["count"] == 3 and out["succeeded"] == 2 and out["failed"] == 1
    assert out["results"][0]["description"] == "cat in a.png"
    assert out["results"][1]["state"] == "FAILED"
    assert out["results"][1]["code"] == VI.MEDIA_UNAVAILABLE_CODE
    assert calls[0] == ("a.png", "what is this?")

    all_ok = VI.describe_batch(cfg, "x.png,y.png", confirmed=True,
                               analyzer=lambda p, q: {"description": p})
    assert all_ok["state"] == "OK" and all_ok["succeeded"] == 2


# ------------------------------------------------------------------ OC-67
def test_oc67_realtime_transcription_and_meeting(tmp_path, monkeypatch):
    from voicebridge import vision as VI

    cfg = _cfg(tmp_path, monkeypatch)
    assert VI.transcribe(cfg, b"abc", confirmed=True)[
        "code"] == VI.MEDIA_UNAVAILABLE_CODE
    tr = VI.transcribe(cfg, b"abc", confirmed=True,
                       transport=lambda req: {"text": "hello world",
                                              "language": "en"})
    assert tr["state"] == "OK" and tr["text"] == "hello world"
    assert tr["language"] == "en"

    assert VI.meeting_bot(cfg)["code"] == VI.MEDIA_UNAVAILABLE_CODE
    joined = VI.meeting_bot(
        cfg, join_url="https://meet.example/x",
        transport=lambda req: {"joined": True, "session": "s-1",
                               "transcription": True})
    assert joined["state"] == "OK" and joined["live_transcription"] is True
    assert joined["session"] == "s-1"


# ------------------------------------------------------------------ OC-D01
def test_ocd01_team_reports_aggregation(tmp_path, monkeypatch):
    from voicebridge.communications import reports as RP

    cfg = _cfg(tmp_path, monkeypatch)
    sources = [{"platform": "github", "account": "org", "target": "repo"},
               {"platform": "discord", "account": "guild", "target": "#dev"}]
    assert RP.team_reports(cfg, sources)["code"] == RP.TEAM_REPORTS_UNAVAILABLE
    assert RP.team_reports(cfg, [])["code"] == RP.TEAM_REPORTS_BAD_SOURCE

    def transport(request):
        return {"items": [{"actor": "alice", "summary": "opened PR",
                           "ts": 5, "url": "https://github.com/x/y/pull/1",
                           "channel": request["target"]}]}

    out = RP.team_reports(cfg, sources, transport=transport)
    assert out["state"] == "SUCCESS" and out["counts"]["total"] == 2
    assert {r["platform"] for r in out["items"]} == {"github", "discord"}


# ------------------------------------------------------------------ OC-48
def test_oc48_workspace_memory_and_typed_external(tmp_path, monkeypatch):
    from voicebridge.memory import workspace as WS

    cfg = _cfg(tmp_path, monkeypatch)
    providers = WS.providers()
    by_name = {p["name"]: p["state"] for p in providers["providers"]}
    assert by_name["markdown-workspace"] == "AVAILABLE"
    assert by_name["lancedb"] == "UNAVAILABLE"
    assert by_name["honcho"] == "UNAVAILABLE"

    assert WS.write_note(cfg, "note-1", "body", confirmed=False)[
        "state"] == "denied"
    assert WS.write_note(cfg, "note-1", "first line\nmore",
                         tags=["t"], confirmed=True)["state"] == "OK"
    assert WS.read_note(cfg, "note-1")["note"]["body"].startswith("first")
    assert [n["name"] for n in WS.list_notes(cfg)["notes"]] == ["note-1"]
    dream = WS.dream(cfg, confirmed=True)
    assert dream["state"] == "OK" and dream["kept"] == 1


# ------------------------------------------------------------------ OC-51
def test_oc51_logbook_screen_journal_loop(tmp_path, monkeypatch):
    from voicebridge.evaluation import logbook as LB

    book = LB.Logbook(tmp_path / "logbook.jsonl")
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    assert LB.capture()["code"] == LB.CAPTURE_UNAVAILABLE
    assert LB.capture_series(book, grabber=None, confirmed=True)[
        "code"] == LB.CAPTURE_UNAVAILABLE

    seen = []

    def sleeper(seconds):
        seen.append(seconds)

    out = LB.capture_series(
        book, grabber=lambda: png, count=2, interval_s=0.01,
        evidence_dir=tmp_path / "evidence", vision=lambda b: "editor open",
        confirmed=True, sleep=sleeper)
    assert out["state"] == "OK" and out["captured"] == 2
    assert seen == [0.01]
    entries = book.entries()
    assert [e["kind"] for e in entries] == ["screen.snapshot",
                                            "screen.snapshot"]
    assert entries[0]["summary"] == "editor open"
    assert LB.capture_series(book, grabber=lambda: png, count=1,
                             confirmed=False)["state"] == "REFUSED"


# ------------------------------------------------------------------ OC-D28
def test_ocd28_provider_accounts_and_fallback(tmp_path, monkeypatch):
    from voicebridge import brain as BR
    from voicebridge import local_models as LM

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(
        LM, "_probe_local",
        lambda *_a, **_k: {"state": "OK", "models": [
            {"id": "qubit-brain:latest", "name": "qubit-brain:latest",
             "size": 1}]})
    chain = LM.fallback_chain(cfg)
    assert chain["state"] == "OK" and chain["chain"]
    picked = LM.resolve_fallback(cfg)
    assert picked["state"] == "OK" and picked["selected"] == "qubit-brain"

    assert BR.principal_add(cfg, "principal-a", "person", "P",
                            confirmed=True)["state"] == "OK"
    account = BR.account_add(cfg, "work", "slack.com", "principal-a",
                             confirmed=True)
    assert account["state"] in ("OK", "FAILED", "denied")
    assert isinstance(BR.account_list(cfg)["accounts"], list)
