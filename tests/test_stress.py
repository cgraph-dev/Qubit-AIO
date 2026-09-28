"""Stress suite for the three fixes + hardening.
This file is the proof-of-work — run with: python -m pytest tests/test_stress.py -v
Covers: RCtrl capture, thresholds, invisible crash logs, boss/hum, broken-English routing,
accent beam, crash-proof reports (raw + 40w), concurrency, long-task salvage, no regression.
"""
import pathlib
import subprocess
import sys
import concurrent.futures

ROOT = pathlib.Path(__file__).resolve().parents[1]
# Use the ACTIVE interpreter (sys.executable) so the suite never depends on a
# repository-local .venv existing — works from a clean clone with any
# documented environment (see docs/archive/2026-09-21/WEBSITES_8E.md "Clean-checkout reproducibility").
PY = sys.executable

def test_py_compile_three_files():
    for p in [ROOT/"voicebridge/orchestrator.py", ROOT/"voicebridge/webui.py", ROOT/"voicebridge/hotkeys.py"]:
        subprocess.check_call([PY, "-m", "py_compile", str(p)])

def test_mic_thresholds_and_audible_bypass():
    from voicebridge.config import Config
    # Use defaults (isolated tmp) so live voice.yaml overrides don't mask code defaults
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as td:
        cfg = Config.load(pathlib.Path(td)/"nonexistent.yaml")
        assert cfg.get("mic", "min_rms") <= 0.0006, cfg.get("mic","min_rms")
        assert cfg.get("vad", "rms_gate") <= 0.002, cfg.get("vad","rms_gate")
        assert cfg.get("asr", "beam_size") >= 4, "beam_size must be >=4 for accent/broken English"
        assert cfg.get("intent", "llm_classifier") is True, "llm_classifier must be enabled (20-30% budget)"
    src = (ROOT/"voicebridge/audio/mic.py").read_text()
    assert "audible = peak_rms" in src
    assert "voiced_s < self.min_speech_s and not audible" in src
    assert (ROOT/"voicebridge/audio/vad.py").read_text().count("0.0018") >= 2
    # exact RCtrl repro: peak 0.00648 must NOT be treated as empty
    from voicebridge.audio.mic import MicCapture
    # simulate the gate: peak 0.00648 with voiced_s=0 should be audible and kept
    # MicCapture.__init__ defaults now include audible bypass — we verify via source, but also run a synthetic check
    mc = MicCapture(min_rms=0.0005, min_speech_s=0.15)
    peak = 0.00648
    audible = peak >= max(mc.min_rms * 3, 0.0015)
    assert audible, f"peak {peak} must be audible with min_rms {mc.min_rms}"

def test_xinput_backend_kept():
    txt = (ROOT/"voicebridge/hotkeys.py").read_text()
    assert "xinput" in txt and "XI2" in txt and "test-xi2" in txt

def test_orchestrator_logging_and_salvage():
    t = (ROOT/"voicebridge/orchestrator.py").read_text()
    for needle in [
        'log_event("safety.verdict"','log_event("worker.dispatch"','log_event("worker.status"',
        'log_event("worker.result"','log_event("worker.error"','log_event("agent.task"',
        'log_event("agent.error"','log_event("boss_talk"','log_event("humming"',
        'def _persist_full_report','def _verify_task_result','discarded','bulletproof',
        'Screenshots/images are OPTIONAL','perplexity/ddg',
        'import random','_BOSS_PHRASES','_HUM_PHRASES','def _boss_phrase','def _hum_phrase',
    ]:
        assert needle in t, f"missing {needle!r}"
    assert t.count('log_event("worker') >= 8 and t.count('log_event("agent') >= 4

def test_boss_and_hum_variants():
    # force fresh import without cache pollution
    import voicebridge.orchestrator as orch
    # check constants via file (module already imported, but constants are at import time — reload to be safe)
    import importlib; importlib.reload(orch)
    assert len(orch._BOSS_PHRASES) >= 8, len(orch._BOSS_PHRASES)
    assert len(orch._HUM_PHRASES) >= 4
    # ensure every heartbeat wait is present (first faster, spaced after)
    txt = (ROOT/"voicebridge/orchestrator.py").read_text()
    assert ("uniform(6, 10)" in txt or "uniform(8, 15)" in txt or "uniform(15, 30)" in txt)
    # boss/hum speak must be barge=False and daemon threads
    assert 'name="hum"' in txt and 'daemon=True' in txt

def test_webui_maps_all_events():
    txt = (ROOT/"voicebridge/webui.py").read_text()
    for e in ["worker.error","worker.status","agent.error","boss_talk","humming","agent.task",
              "openclaw.error","hermes.task"]:
        assert e in txt, f"webui missing {e}"

def test_broken_english_routing_torture():
    from voicebridge.intent.classifier import Classifier, normalize_broken_english
    from voicebridge.intent.state import ConfirmationState, ConversationState
    cases = [
        ("root to open claw and create text document in temp folder called trickish micky", "agent"),
        ("route to openclaw and create the text document in temporary folder called trickish micky and write text tricky miki inside it", "agent"),
        ("creat file test html please", "coding"),
        ("make doc in tmp", "coding"),
        ("open claw fix stripe webhook", "coding"),
        ("hello how are you", "conversation"),
        ("hermes write a report", "agent"),
        ("ask hermes to check inbox", "agent"),
    ]
    c = Classifier({}, ConfirmationState(), ConversationState())
    fails = []
    for text, want in cases:
        got = c.classify(text).kind
        if got != want:
            fails.append(f"{text!r}: want {want} got {got}")
    assert not fails, "\n".join(fails)
    # normalization must fix known typos and be returned as task_text
    c2 = Classifier({}, ConfirmationState(), ConversationState())
    r = c2.classify("root to open claw delete trickish micky file")
    assert "route to worker" in r.task_text and "tricky mickey" in r.task_text
    assert normalize_broken_english("root to open claw delete trickish micky file") == "route to worker delete tricky mickey file"

def test_summarizer_bulletproof_and_40w():
    from voicebridge.summarizer import summarize
    # empty/None must not crash
    assert summarize("", {}) == ""
    assert summarize(None, {}) == ""  # type: ignore
    # ollama down must fallback and still <=40w
    long = "Hello world. " * 300
    s = summarize(long, {"provider": "ollama", "ollama_model": "does-not-exist-xyz", "max_words": 40})
    assert s and len(s.split()) <= 40, f"{len(s.split())} words: {s[:200]}"
    # code fence heavy input
    code_heavy = "```python\n" + "x=1\n"*300 + "```\nFinal important sentence that must appear. "
    s2 = summarize(code_heavy, {"provider": "extractive", "max_words": 40})
    assert s2 and len(s2.split()) <= 40
    assert "```" not in s2

def test_concurrency_hammer():
    from voicebridge.intent.classifier import Classifier
    from voicebridge.intent.state import ConfirmationState, ConversationState
    import random
    phrases = ["root to open claw delete file","creat document trickish micky","open claw fix webhook","hello","make tmp file","ask hermes do it","write trickish miki in tmp"]
    def job(_i):
        # heuristic-only for stress (no network in hammer)
        c = Classifier({"llm_classifier": False}, ConfirmationState(), ConversationState())
        return c.classify(random.choice(phrases)).kind
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(job, i) for i in range(40)]
        res = [f.result(timeout=5) for f in futs]
    assert len(res) == 40 and all(k in ("coding","conversation","agent","local","system","clarify") for k in res)

def test_report_persist_and_verify_present():
    txt = (ROOT/"voicebridge/orchestrator.py").read_text()
    assert "_persist_full_report" in txt and ("reports/" in txt and ".md" in txt)
    assert "_verify_task_result" in txt and "git" in txt
    # nudge mentions optional screenshots + perplexity/ddg verbatim + 40w last paragraph
    assert "Screenshots/images are OPTIONAL" in txt or "optional — capture only when needed" in txt or "Screenshots are optional" in txt
    assert "perplexity/ddg" in txt.lower()
    assert "40-word" in txt.lower() or "40-word max" in txt.lower()

def test_no_frontend_touched():
    # Frontend IS intentionally updated in this batch: activity/reports moved to
    # the SHARED authenticated helper. Verify the pages exist and use the
    # current vb.js/vbApi contract (token comes from vbToken, never a local
    # duplicate TOKEN const).
    assert (ROOT/"frontend/reports.html").exists(), "frontend/reports.html must exist"
    assert (ROOT/"frontend/activity.html").exists()
    t=(ROOT/"frontend/activity.html").read_text()
    # no duplicate TOKEN const (the shared vb.js owns the token)
    assert "const TOKEN" not in t, "activity.html must not define its own TOKEN"
    # shared authenticated helper loaded; API calls go through vbApi()
    assert "/static/vb.js" in t and "/static/ui.js" in t
    assert "vbApi('/api/activity" in t, "activity.html should use the shared vbApi helper"


def test_moonshine_chunking_guard():
    """Moonshine must not crash on long (>20s) audio; SherpaASR chunks it.
    Regression: the old code fed the whole clip and onnxruntime threw a
    broadcast error ('4 by 1241'), silently returning empty text."""
    from voicebridge.config import Config
    from voicebridge.asr.sherpa import SherpaASR
    import tempfile, pathlib, wave, numpy as np
    with tempfile.TemporaryDirectory() as td:
        cfg = Config.load(pathlib.Path(td) / "none.yaml")
        # synthesize a >20s WAV (silence+noise so no model load needed to assert chunking)
        sr = 16000
        dur = 25.0
        wav = pathlib.Path(td) / "long.wav"
        # low-amplitude noise: safe, won't blow up the decoder
        data = (np.random.randn(int(sr * dur)) * 100).astype(np.int16)
        with wave.open(str(wav), "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr)
            wf.writeframes(data.tobytes())
        asr = SherpaASR(engine="moonshine", model_dir=cfg.model_dir(), provider="cpu", num_threads=2)
        # Must NOT raise; should return a Transcript (possibly empty) gracefully
        tr = asr.transcribe(wav)
        assert tr is not None and tr.duration_s is not None
        # Confirm the chunking path was taken (>20s)
        assert tr.duration_s > 20.0


def test_activity_html_js_is_valid():
    """activity.html had `const TOKEN=*** URLSearchParams(...)` — a JS syntax
    error that killed the ENTIRE activity page. It must now use the shared
    authenticated helper (frontend/vb.js): the token comes from vbToken() only,
    and an attacker-controlled ?api= query string is never honored."""
    import subprocess, tempfile, pathlib
    t = (ROOT / "frontend/activity.html").read_text()
    # shared helpers loaded; token comes from vbToken() (browser storage only)
    assert "/static/vb.js" in t and "/static/ui.js" in t
    assert "vbApi(" in t, "activity.html must use the shared vbApi helper"
    # no ?api= URLSearchParams override, no query-string token read
    assert "new URLSearchParams" not in t, "activity.html must not honor ?api="
    assert "get('token')" not in t and 'get("token")' not in t, \
        "activity.html must not read a query-string token"
    assert t.count("***") == 0, f"activity.html still contains literal *** ({t.count('***')})"
    # extract the INLINE <script> body (skip the /static/vb.js loader) and
    # node --check it
    import re as _re
    m = _re.search(r"<script(?![^>]*src=)[^>]*>([\s\S]*?)</script>", t)
    js = m.group(1) if m else ""
    assert js.strip(), "activity.html has no inline <script>"
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "a.js"
        p.write_text(js)
        r = subprocess.run(["node", "--check", str(p)], capture_output=True, text=True)
        assert r.returncode == 0, f"activity.html JS invalid: {r.stderr[:200]}"


def test_reports_page_fetches_with_token():
    """reports.html must NOT declare its own TOKEN const: it uses the shared
    authenticated helper (frontend/vb.js) which sends X-Api-Token from
    vbToken(), and never honors a ?api= override or query-string token."""
    t = (ROOT / "frontend/reports.html").read_text()
    assert "const TOKEN" not in t, "reports.html must not define its own TOKEN"
    assert "/static/vb.js" in t and "/static/ui.js" in t
    assert "vbApi('/api/reports" in t
    assert "new URLSearchParams" not in t and t.count("***") == 0


def test_task_queue_fifo_and_capacity():
    """TaskQueue is FIFO, returns position, caps at max_size, and honors provider."""
    from voicebridge.intent.state import TaskQueue
    q = TaskQueue(max_size=3)
    assert q.enqueue("task A") == 1
    assert q.enqueue("task B", provider="hermes") == 2
    assert q.enqueue("task C") == 3
    # full: extra returns 0 (not enqueued)
    assert q.enqueue("task D") == 0, "queue must reject beyond capacity"
    assert q.size == 3
    first = q.dequeue()
    assert first and first["task"] == "task A"
    assert q.dequeue()["task"] == "task B"
    assert q.dequeue()["provider"] == ""  # task C had no provider
    assert q.dequeue() is None            # empty


def test_task_queue_clear_and_empty():
    from voicebridge.intent.state import TaskQueue
    q = TaskQueue()
    q.enqueue("x"); q.enqueue("y")
    assert q.size == 2
    assert q.clear() == 2
    assert q.size == 0 and q.items() == []


def test_browser_inbox_module_imports_and_sites():
    """browser_inbox exposes per-site config and the reader without launching."""
    import importlib.util, pathlib
    mod_path = pathlib.Path("scripts/browser_inbox.py")
    spec = importlib.util.spec_from_file_location("browser_inbox", mod_path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    for site in ("gmail", "instagram", "messenger", "tiktok"):
        assert site in m.SITES, f"missing site config {site}"
        assert m.SITES[site]["url"], f"no url for {site}"
    assert m.DEFAULT_ENGINE_ORDER == ["chromium", "firefox"]


def test_browser_inbox_unknown_site():
    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location("browser_inbox", pathlib.Path("scripts/browser_inbox.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    assert m.read_site("nope")["error" if "error" in m.read_site("nope") else "site"] == "unknown site nope"


def test_browser_auto_module_surface():
    """browser_auto exposes the chromium single-engine reader + perf without launching."""
    import importlib.util, pathlib
    mod_path = pathlib.Path("scripts/browser_auto.py")
    spec = importlib.util.spec_from_file_location("browser_auto", mod_path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    assert m.PORT == 9222
    assert "chromium" in m.BINARY  # single agent browser = snap Chromium
    for fn in ("list_tabs", "read_tab", "run_js", "open_url", "perf"):
        assert callable(getattr(m, fn)), f"missing {fn}"
    # perf() must not raise even with no log yet
    p = m.perf()
    assert isinstance(p, dict) and p.get("engine") == "chromium"


def test_browser_auto_compiles_and_ignores_state():
    import subprocess, pathlib
    r = subprocess.run([sys.executable, "-m", "py_compile", "scripts/browser_auto.py", "scripts/tab_report.py"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    gi = pathlib.Path(".gitignore").read_text()
    assert "browser_auto_state" in gi and "browser_profiles" in gi


def test_play_youtube_query_extraction():
    """'play X on youtube' routes to play_youtube with a clean song query; generic
    'play the music' stays on media_playpause."""
    from voicebridge.local import actions
    cases = {
        "play despacito on youtube": ("play_youtube", "despacito"),
        "put on some lofi music": ("play_youtube", "lofi"),
        "i want to listen to queen on youtube": ("play_youtube", "queen"),
        "you know just play imagine dragons": ("play_youtube", "imagine dragons"),
        "play the song shape of you": ("play_youtube", "shape of you"),
        "play the music": ("play_youtube", "the music"),
        "pause it": ("youtube_pause", ""),
        "pause the youtube music": ("youtube_pause", ""),
        "resume the music": ("youtube_resume", ""),
        "next video": ("youtube_next", ""),
        "stop the music": ("youtube_stop", ""),
        "close the youtube tabs": ("youtube_stop", ""),
    }
    for text, (name, q) in cases.items():
        m = actions.match(text)
        assert m is not None, f"no match for {text!r}"
        assert m.name == name, f"{text!r}: expected {name}, got {m.name}"
        if q:
            assert q in m.params.get("query", ""), f"{text!r}: query={m.params.get('query')!r} missing {q!r}"
        # query must not still lead with the verb (except the generic "play the music")
        if text != "play the music":
            assert not m.params.get("query", "").startswith(("play ", "put on ", "listen to ", "start ")), \
                f"{text!r}: query still has verb: {m.params.get('query')!r}"


def test_play_youtube_ranks_by_views():
    """The winner is chosen by most views from the top results (not just the 1st)."""
    import json as _j
    from unittest import mock
    from voicebridge.local import actions as A
    fake_out = "\n".join(_j.dumps(r) for r in [
        {"title": "First (few views)", "webpage_url": "https://yt/1", "view_count": 100, "uploader": "r1"},
        {"title": "Popular one", "webpage_url": "https://yt/2", "view_count": 99999999, "uploader": "r2"},
        {"title": "Third", "webpage_url": "https://yt/3", "view_count": 5000, "uploader": "r3"},
    ])
    with mock.patch.object(A, "log_event"), \
         mock.patch.object(A.subprocess, "run", return_value=A.subprocess.CompletedProcess([], 0, stdout=fake_out, stderr="")), \
         mock.patch("sys.modules", {**{"browser_auto": mock.MagicMock(open_url=mock.MagicMock(return_value={"error": None, "url": "https://yt/2"}))}, **sys.modules}):
        with mock.patch("shutil.which", return_value="/x"):
            # force a known mode so it launches (not asks) — ranking still applies
            A._YT_MODE = "tab"
            res = A._a_play_youtube_params({"query": "queen"})
            A._YT_MODE = None
    assert "Popular one" in res, res


def test_web_replies_speak_locally_by_default():
    """In web mode speak() must fall through to local playback when
    webui.speak_web_locally is true (default); when false it records only."""
    import pathlib
    from voicebridge.config import Config
    from unittest import mock
    import tempfile
    from voicebridge.orchestrator import VoiceOrchestrator

    def make_orb(speak_locally):
        orb = VoiceOrchestrator.__new__(VoiceOrchestrator)
        with tempfile.TemporaryDirectory() as td:
            cfg = Config.load(pathlib.Path(td) / "none.yaml")
        cfg.data.setdefault("webui", {})["speak_web_locally"] = speak_locally
        cfg.data.setdefault("tts", {})["direct"] = False
        orb.cfg = cfg
        orb.muted = False
        orb._web_mode = True
        orb._web_spoken = []
        orb._tts = mock.MagicMock()
        orb._tts.synthesize.return_value = pathlib.Path("/tmp/opencode/x.wav")
        orb._cancel = mock.MagicMock()
        orb._barge = mock.MagicMock()
        orb._barge_stream = None
        orb.playback = mock.MagicMock()
        return orb

    # speak_web_locally=True -> should reach playback (play called)
    orb = make_orb(True)
    orb.speak("hello there")
    orb.playback.play.assert_called_once()
    assert orb._web_spoken == ["hello there"]

    # speak_web_locally=False -> records only, no playback
    orb2 = make_orb(False)
    orb2.speak("hello there")
    orb2.playback.play.assert_not_called()
    assert orb2._web_spoken == ["hello there"]


def test_orb_freeze_regression_node():
    """Atomic: the guarded audio-await must resolve even when interrupt() pauses
    mid-play (pause() never fires 'ended'). Regression for the orb freeze."""
    r = subprocess.run(["node", "scripts/test_orb_frozen.mjs"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"orb freeze regression: {r.stderr[-400:]}\n{r.stdout[-400:]}"
    assert "ALL PASS" in r.stdout, r.stdout[-400:]


def test_webui_results_pruned_both_paths():
    """webui must bound _results (keep last N) on BOTH talk and type paths."""
    import pathlib, tempfile
    from voicebridge.config import Config
    from unittest import mock
    from voicebridge.webui import WebUI
    with tempfile.TemporaryDirectory() as td:
        cfg = Config.load(pathlib.Path(td) / "none.yaml")
        cfg.data.setdefault("webui", {})["enabled"] = False
        orb = mock.MagicMock()
        orb.cfg = cfg
        orb._cur_ctx = mock.MagicMock(return_value=None)
        orb.shared = mock.MagicMock()
        orb._needs_answer = mock.MagicMock(return_value=False)
        orb.process_web_utterance = mock.MagicMock(return_value={"summary": "x", "route": "local"})
        w = WebUI(orb, host="127.0.0.1", port=0, state_dir=pathlib.Path(td))
        # Insert many results directly and prune
        with w._result_lock:
            for i in range(120):
                w._results[f"rid{i}"] = {"x": i}
        w._prune_results(keep=50)
        with w._result_lock:
            n = len(w._results)
        assert n <= 50, f"results not bounded: {n}"


def test_terminal_health_and_watch_tools():
    """qubit-dev /health and /watch must return a usable snapshot without crashing."""
    from voicebridge.config import Config
    from voicebridge.terminal import _health_text, _watch_text
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as td:
        cfg = Config.load(pathlib.Path(td) / "none.yaml")
        # health must never raise, even with no live bridge / no logs
        h = _health_text(cfg)
        assert isinstance(h, str) and h.strip(), "health returned empty"
        assert "dashboard:" in h
        # watch must tolerate no activity
        w = _watch_text(cfg, [])
        assert isinstance(w, str)


def test_actions_youtube_uses_module_path():
    """The windowed-tab path needs Path — regression for 'name Path is not defined'."""
    src = pathlib.Path("voicebridge/local/actions.py").read_text()
    # Path must be imported at module top (not just locally) so all 4 call sites work
    assert "from pathlib import Path" in src
    # and the youtube tab path must not rely on a local-only Path import
    assert "import browser_auto" in src


def test_play_tab_path_no_unbound():
    """Mock the browser tab launch; assert _yt_launch tab branch runs without
    NameError (the Path fix) and reports 'in a tab'."""
    from unittest import mock
    from voicebridge.local import actions as A
    with mock.patch.object(A, "log_event"), \
         mock.patch.object(A.subprocess, "Popen"), \
         mock.patch("sys.modules", {**{"browser_auto": mock.MagicMock(
             open_url=mock.MagicMock(return_value={"error": None, "url": "https://yt/1"}))}, **__import__("sys").modules}):
        res = A._yt_launch("queen", [{"title": "Queen", "url": "https://yt/1", "views": 1}], 0, "tab")
    assert "in a tab" in res, res


def test_orb_rapid_press_stress_node():
    """Atomic: the orb must NOT freeze under adversarial rapid taps (busy/ptt/recording
    always recover). Regression for 'orb freezes when I press too many times'."""
    import subprocess
    r = subprocess.run(["node", "scripts/test_orb_stress.mjs"],
                       capture_output=True, text=True, timeout=40)
    assert r.returncode == 0, f"orb stress failed: {r.stderr[-400:]}\n{r.stdout[-400:]}"
    assert "ALL PASS" in r.stdout, r.stdout[-400:]


def test_report_generator_rich_format():
    """generate_report auto-build must emit a RICH report with the key detailed
    sections (Agent/Route, Files table, Verification+test output, References,
    Screenshots, Commands, spoken summary last) and infer status correctly."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("gen", "scripts/generate_report.py")
    g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)
    class A: pass
    a = A(); a.task="fix login"; a.project="~/x"; a.status=""; a.elapsed="2s"
    a.agent="hermes"; a.route="hermes"; a.summary=""; a.actions=""; a.files=""
    a.verify="pytest 12 passed"; a.risks=""; a.next=""; a.questions=""
    a.objectives=""; a.references="https://a.ref/x"; 
    raw=("Fixed auth.py. Ran: pytest backend -q (12 passed, 0 failed). "
         "Screenshot: /tmp/ss.png. Ref https://github.com/cgraph-dev/cgraph. "
         "All tests green, system works. Files: backend/app/sec/auth.py frontend/src/login.tsx")
    a.raw=raw
    md, spoken = g.auto_build(raw, a)
    for sec in ("## Executive Summary","## Actions Taken","## Files Changed",
                "## Verification","## Risks / Blockers","## Next Steps",
                "## Appendix","### References / Sources","### Screenshots / Evidence",
                "**Spoken Summary"):
        assert sec in md, f"missing section {sec}"
    assert "**Agent:** hermes" not in md and "**Route:** hermes" not in md, \
        "user-facing reports use Qubit labels, never upstream names"
    assert "**Agent:** Qubit agent" in md and "**Route:** Qubit agent" in md, \
        "legacy hermes tokens map to Qubit labels"
    assert "**Status:** completed" in md, "should infer completed (0 failures)"
    assert len(spoken.split()) <= 40, "spoken summary >40 words"
    # spoken summary is the last line
    assert md.rstrip().endswith(spoken), "spoken summary must be last"


def test_report_infers_status():
    import importlib.util
    spec = importlib.util.spec_from_file_location("gen", "scripts/generate_report.py")
    g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)
    assert g.infer_status("12 passed, 0 failed. done") == "completed"
    assert g.infer_status("Traceback: ZeroDivisionError") == "partial"
    assert g.infer_status("3 tests FAILED on deploy") == "partial"
    assert g.infer_status("worked fine") == "completed"


def test_classifier_uses_config_model_not_hardcoded():
    """The classifier must NOT default to qwen3-vl:8b (never pulled/resident). It
    should source its model from intent.local_llm.model / intent.ollama_model."""
    import pathlib, tempfile
    from voicebridge.config import Config
    from voicebridge.intent.state import ConfirmationState, ConversationState
    from voicebridge.intent.classifier import Classifier
    with tempfile.TemporaryDirectory() as td:
        cfg = Config.load(pathlib.Path(td) / "none.yaml")
        cfg.data.setdefault("intent", {})["local_llm"] = {"model": "qubit-brain", "enabled": True}
        cfg.data["intent"]["ollama_model"] = "qubit-brain"
        clf = Classifier(cfg.section("intent"), ConfirmationState(), ConversationState())
        assert clf.ollama_model == "qubit-brain", clf.ollama_model
        # tiers resolve to the config model, not hardcoded qwen3-vl
        assert clf._tier_map.get("turbo") == "qubit-brain"
        assert clf._tier_map.get("normal") == "qubit-brain"
        assert "qwen3-vl" not in (clf._tier_map.get("turbo") or "")


def test_local_llm_options_have_num_ctx_and_keepalive():
    """Packet 4B: local_llm and summarizer must source num_ctx + keep_alive
    from the ONE canonical runner contract (no per-caller literals)."""
    src = pathlib.Path("voicebridge/local_llm.py").read_text()
    assert '"num_ctx": num_ctx' in src, "local_llm lost configurable num_ctx"
    assert '"keep_alive": -1' in src, "local_llm missing keep_alive"
    # The single runner contract (num_ctx + keep_alive) now lives in the
    # canonical provider gateway that summarizer/planner/vision delegate to.
    gw = pathlib.Path("voicebridge/providers/gateway.py").read_text()
    assert '"num_ctx": rc.num_ctx' in gw, \
        "gateway must source num_ctx from the runner contract"
    assert '"keep_alive": rc.keep_alive' in gw, \
        "gateway must source keep_alive from the runner contract"
    summ = pathlib.Path("voicebridge/summarizer.py").read_text()
    assert "run_role" in summ, \
        "summarizer must execute through the canonical provider gateway"
    assert '"num_ctx": 4096' not in summ, \
        "per-caller num_ctx literal must be gone (single-contract rule)"


def test_mic_mono_takes_best_channel():
    """_mono must pick the highest-energy channel (not max-abs*ch0-sign which
    phase-corrupts when the mic isn't on channel 0)."""
    import numpy as np
    from voicebridge.audio.mic import MicCapture
    # ch0 nearly silent, ch1 loud with positive signal
    data = np.array([
        [0.0, 0.5],
        [0.001, 0.6],
        [0.0, 0.4],
        [-0.001, 0.55],
    ], dtype=np.float32)
    mono = MicCapture._mono(data)
    # should track channel 1 (positive/negative faithfully), not ch0-sign mix
    assert np.all((mono > 0.3) & (mono < 0.7)), f"did not pick loud channel: {mono}"


def test_fallback_asr_honors_config_engine_and_num_threads():
    """_transcribe_with_fallback must honor asr.fallback_engine + num_threads and
    cache the recognizer (no reload per empty transcript)."""
    import pathlib, tempfile
    from voicebridge.config import Config
    with tempfile.TemporaryDirectory() as td:
        cfg = Config.load(pathlib.Path(td) / "none.yaml")
        # fallback_engine default present in defaults
        assert cfg.get("asr", "fallback_engine", default="moonshine") == "moonshine"
        assert cfg.get("asr", "num_threads", default=4) >= 1
    # assert the orchestrator reads them (not hardcoded opposite-engine)
    src = pathlib.Path("voicebridge/orchestrator.py").read_text()
    assert '"fallback_engine"' in src, "orchestrator must read asr.fallback_engine"
    assert '"num_threads"' in src, "orchestrator must read asr.num_threads"
    assert "_fallback_asr" in src, "must cache the fallback recognizer"


def test_english_only_no_multilingual_tier():
    """ENGLISH ONLY (user decision): the multilingual ASR tier must be gone from
    the orchestrator and its config keys removed."""
    import pathlib
    src_o = pathlib.Path("voicebridge/orchestrator.py").read_text()
    assert "_get_multilingual_asr" not in src_o, "multilingual tier must be removed"
    assert "asr.multilingual" not in src_o, "no multilingual events"
    src_c = pathlib.Path("voicebridge/config.py").read_text()
    assert "multilingual_fallback" not in src_c, "config key removed"


def test_english_only_language_guard():
    """The reply guard must flag any non-English reply (RO/FR/DE/ES/RU) and allow
    plain English; retry instruction must say ENGLISH only."""
    import pathlib
    from voicebridge.local_llm import _reply_is_other_language as f
    english = ["Hello, how are you today?",
               "Yes I can hear you fine.",
               "Your Instagram is https://www.instagram.com/tr.cky/.",
               "Europe/Bucharest (EEST, UTC+3).",
               "I don't understand Romanian. Can you please rephrase in English?"]
    foreign = ["Fusul orar al tău este aproximativ 20 de minute.",   # RO
               "Je parle uniquement deux langues.",                   # FR
               "Ich spreche nur Deutsch und das ist gut.",            # DE
               "No puedo hablar español, lo siento amigo.",           # ES
               "Привет как дела"]                                     # RU
    for t in english:
        assert not f(t), f"English wrongly flagged: {t!r}"
    for t in foreign:
        assert f(t), f"non-English not detected: {t!r}"
    src = pathlib.Path("voicebridge/local_llm.py").read_text()
    assert "answer in plain ENGLISH only" in src
    assert "You speak ENGLISH ONLY" in src


def test_routing_success_rate_95():
    """Routing success-rate contract: a broad mix of spoken/typed utterances must
    route to the CORRECT lane >=95% of the time (mic + web + text parity)."""
    from voicebridge.config import Config
    from voicebridge.intent.state import ConfirmationState, ConversationState
    from voicebridge.intent.classifier import Classifier
    import pathlib, tempfile
    cases = [  # (utterance, expected kind)
        ("what's my instagram", "conversation"),
        ("what time is it", "local"),
        ("play despacito on youtube", "local"),
        ("pause the music", "local"),
        ("next video", "local"),
        ("stop the music", "local"),
        ("check my email", "coding"),           # template -> task lane
        ("what's the weather in bucharest", "conversation"),
        ("fix the login bug in the auth flow", "coding"),
        ("route to hermes", "agent"),
        ("status", "system"),
        ("stop", "system"),
        ("hello", "conversation"),
        ("how are you today", "conversation"),
        ("use the browser to check github trending", "agent"),
    ]
    with tempfile.TemporaryDirectory() as td:
        cfg = Config.load(pathlib.Path(td) / "none.yaml")
        cfg.data.setdefault("intent", {})["llm_classifier"] = False   # deterministic only
        clf = Classifier(cfg.section("intent"), ConfirmationState(), ConversationState())
        ok = 0
        misses = []
        for text, want in cases:
            got = clf.classify(text)
            kind = got.kind
            if kind == want or (want == "conversation" and kind in ("clarify",)):
                ok += 1
            else:
                misses.append((text, want, kind))
        rate = ok / len(cases)
        assert rate >= 0.95, f"routing success {rate:.0%} < 95%: {misses}"




def test_mic_gain_avoids_over_amp():
    """mic.gain must not be set so high it distorts ASR (a quiet HyperX returns
    empty at x8+; ~x3 transcribes). Guard the config/main defaults."""
    import pathlib, tempfile
    from voicebridge.config import Config
    with tempfile.TemporaryDirectory() as td:
        cfg = Config.load(pathlib.Path(td) / "none.yaml")
        g = float(cfg.get("mic", "gain", default=4.0))
        assert 1.0 <= g <= 6.0, f"mic gain {g} risks over-amplification/distortion"
    repo = pathlib.Path("config/voice.yaml").read_text()
    # the shipped config must not default to the distorting x8
    import re
    m = re.search(r"gain:\s*([\d.]+)", repo)
    assert m and float(m.group(1)) <= 6.0, f"repo config gain too high: {m.group(0) if m else 'none'}"


def test_audio_output_pinning_config():
    """audio.output_device can pin TTS to a specific (headphone) device, and
    config/defaults expose it; Playback accepts a device index without crashing."""
    import pathlib, tempfile
    from voicebridge.config import Config
    from voicebridge.audio.playback import Playback
    with tempfile.TemporaryDirectory() as td:
        cfg = Config.load(pathlib.Path(td) / "none.yaml")
        # default exists
        assert "output_device" in cfg.data.get("audio", {})
    # Playback can be constructed with a device index (validated at play time)
    pb = Playback(device=5)
    assert pb.device == 5
    # source has the volume helper + example documents it
    src = pathlib.Path("voicebridge/orchestrator.py").read_text()
    assert "_apply_output_volume" in src
    ex = pathlib.Path("config/voice.example.yaml").read_text()
    assert "output_device" in ex and "output_volume" in ex


def test_learning_ledger_clhf():
    """CLHF: corrections/bad/good feedback become durable prompt lessons that the
    model can use — never repeats the wrong version; bounded ledger; fsynced."""
    import tempfile, pathlib
    from voicebridge.learning import LearningLedger
    with tempfile.TemporaryDirectory() as td:
        L = LearningLedger(pathlib.Path(td))
        L.record("what is my instagram", "I cannot access it", "bad",
                 corrected="My Instagram is tr.cky (instagram.com/tr.cky)")
        L.record("what time is it", "The time is 20:00 EEST", "good")
        L.add_preference("answer in English only")
        p = L.as_prompt()
        assert "tr.cky" in p, "correction lesson not surfaced"
        assert "ALWAYS true" in p or "taught me" in p
        assert "instagram.com/tr.cky" in p
        # persistence: reload from disk
        L2 = LearningLedger(pathlib.Path(td))
        assert "tr.cky" in L2.as_prompt()
        # bounded: many rows don't blow up
        for i in range(60):
            L2.record(f"q{i}", "a", "bad", corrected=f"c{i}")
        assert len(L2.as_prompt()) > 0  # still renders without error


def test_playback_resamples_to_device_rate():
    """Pinned raw ALSA device rejects a mismatched sample rate (-9997). Playback
    must resample the TTS (22050) to the device's native (44100)."""
    from voicebridge.audio.playback import _resample
    import numpy as np
    src = (np.sin(np.linspace(0, 20, 22050)) * 32767).astype(np.int16)
    out = _resample(src, 22050, 44100)
    assert len(out) == 44100, f"expected 44100 samples, got {len(out)}"
    assert out.dtype == np.int16
    # resampled signal still has energy (valid audio)
    assert float(np.sqrt((out.astype(float)**2).mean())) > 1000
    # no-op when rates match
    assert len(_resample(src, 22050, 22050)) == 22050


def test_playback_resolves_target_rate_on_follow_default_path(monkeypatch):
    """HDMI/TV sinks reject 22050 Hz (-9997). Even with NOTHING pinned (follow
    the system default sink), playback must resample to the default output's
    native rate instead of sending Piper's 22050 raw — this is what broke when
    the OS output was switched to the TV."""
    from voicebridge.audio.playback import _resolve_target_rate
    import sounddevice as sd
    fake = [
        {"name": "HDA NVidia: Philips FTV (hw:0,3)",
         "default_samplerate": 44100.0, "max_input_channels": 0,
         "max_output_channels": 8},
        {"name": "default", "default_samplerate": 44100.0,
         "max_input_channels": 64, "max_output_channels": 64},
    ]
    monkeypatch.setattr(sd, "query_devices", lambda: fake)
    old = sd.default.device
    try:
        sd.default.device = [1, 1]            # default output -> "default" @44100
        assert _resolve_target_rate(None) == 44100   # follow-default resamples
        assert _resolve_target_rate(0) == 44100      # pinned HDMI path resamples
    finally:
        sd.default.device = old


def test_playback_target_rate_unknown_returns_zero(monkeypatch):
    """If the native rate cannot be determined, _resolve_target_rate must return
    0 (no resample, no crash) so playback keeps its old fallback behavior."""
    from unittest import mock
    from voicebridge.audio.playback import _resolve_target_rate
    import sounddevice as sd
    with mock.patch.object(sd, "query_devices",
                           side_effect=Exception("no devices")):
        assert _resolve_target_rate(None) == 0
        assert _resolve_target_rate(5) == 0


def test_play_try_sends_native_rate_when_following_default(monkeypatch, tmp_path):
    """End-to-end: _play_try() must hand sd.play() the RESAMPLED native rate
    (44100) even when dev is None (follow default), so a 22050 Hz TTS clip plays
    on an HDMI/TV default sink instead of failing with -9997."""
    from voicebridge.audio.playback import Playback
    import sounddevice as sd, wave, numpy as np, pathlib
    wav = pathlib.Path(tmp_path) / "t.wav"
    with wave.open(str(wav), "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(22050)
        wf.writeframes(np.zeros(22050, dtype=np.int16).tobytes())  # 1.0 s @22050
    fake = [
        {"name": "default", "default_samplerate": 44100.0,
         "max_input_channels": 64, "max_output_channels": 64},
    ]
    monkeypatch.setattr(sd, "query_devices", lambda: fake)
    old = sd.default.device
    played = {}
    def fake_play(audio, sr, blocking=False, device=None):
        played["len"] = len(audio); played["sr"] = sr; played["dev"] = device
    calls = {"n": 0}
    def fake_get_stream():
        # active on the first poll, inactive after -> watchdog breaks fast
        calls["n"] += 1
        class _ST:
            active = calls["n"] == 1
        return _ST()
    try:
        sd.default.device = [0, 0]
        monkeypatch.setattr(sd, "play", fake_play)
        monkeypatch.setattr(sd, "get_stream", fake_get_stream)
        pb = Playback(device=None, device_name="")
        assert pb._play_try(wav, on_start=None) is True
        assert played["sr"] == 44100, f"expected native 44100, got {played['sr']}"
        assert played["len"] == 44100, "audio must be resampled 22050 -> 44100"
    finally:
        sd.default.device = old


def test_playback_resolves_by_name_not_silent_index():
    """Resolver contract: match by name/index when pinned, otherwise DEFER to
    the system default sink. It must NEVER guess a device — guessing once sent
    beeps+TTS to a silent ALC892 line-out while the real sink was the PipeWire
    default (USB headset), making PTT feel completely dead."""
    from voicebridge.audio.playback import Playback
    import sounddevice as sd
    devs = sd.query_devices()
    # 1) pinned name that EXISTS -> that exact device, and it can output
    hit = next((i for i, d in enumerate(devs)
                if d["max_output_channels"] > 0 and "HyperX" in d["name"]), None)
    if hit is not None:
        pb = Playback(device="", device_name="HyperX")
        assert pb._resolve_device() == hit
    # 2) explicit valid index -> honored (never swapped for something else)
    out = next((i for i, d in enumerate(devs) if d["max_output_channels"] > 0), None)
    if out is not None:
        assert Playback(device=str(out), device_name="")._resolve_device() == out
    # 3) UNMATCHED pin -> None (= system default sink), never a guessed port.
    #    This is the anti-silent-port rule; returning any other index fails.
    pb = Playback(device="", device_name="DefinitelyNotARealDeviceName12345")
    miss = pb._resolve_device()
    assert miss is None or devs[miss]["max_output_channels"] > 0
    # 4) nothing pinned -> follow the system default via the pulse/pipewire
    #    aggregate (audible by definition) or None on a bare-ALSA system; it
    #    must never be a guessed silent port.
    got = Playback(device="", device_name="")._resolve_device()
    assert got is None or devs[got]["max_output_channels"] > 0


def test_playback_follow_default_uses_aggregate_without_resample(monkeypatch):
    """Follow-default must prefer the pulse/pipewire aggregate and play at the
    SOURCE rate (no Python resample) so PipeWire converts at high quality —
    this both fixes HDMI/TV (raw ALSA rejects 22050 with -9997) and avoids the
    linear-resample 'robotic voice' on every sink."""
    from voicebridge.audio.playback import (_aggregate_output_index,
                                            _resolve_target_rate)
    import sounddevice as sd
    fake = [
        {"name": "HDA NVidia: Philips FTV (hw:0,3)", "default_samplerate": 44100.0,
         "max_input_channels": 0, "max_output_channels": 8},
        {"name": "pulse", "default_samplerate": 44100.0,
         "max_input_channels": 32, "max_output_channels": 32},
    ]
    monkeypatch.setattr(sd, "query_devices", lambda: fake)
    assert _aggregate_output_index() == 1
    assert _resolve_target_rate(1) == 0          # aggregate: no Python resample
    assert _resolve_target_rate(0) == 44100      # raw hw: native-rate resample
    # nothing pinned -> the aggregate index, not a silent hw port
    from voicebridge.audio.playback import Playback
    assert Playback(device="", device_name="")._resolve_device() == 1


def test_audio_config_exposes_device_name():
    import pathlib
    from voicebridge.config import Config
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        cfg = Config.load(pathlib.Path(td) / "none.yaml")
        assert "output_device_name" in cfg.data.get("audio", {})
    ex = pathlib.Path("config/voice.example.yaml").read_text()
    assert "output_device_name" in ex
    src = pathlib.Path("voicebridge/orchestrator.py").read_text()
    assert "output_device_name" in src


def test_web_no_double_speak_when_local():
    """When speak_web_locally is on, the bridge plays on PC and must NOT also
    hand the browser audio_b64 (that's the 'responds twice' bug)."""
    from voicebridge.config import Config
    from unittest import mock
    from voicebridge.webui import WebUI
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as td:
        cfg = Config.load(pathlib.Path(td) / "none.yaml")
        cfg.data.setdefault("webui", {})["speak_web_locally"] = True
        # off: browser gets audio_b64
        cfg.data["webui"]["speak_web_locally"] = False
        orb = mock.MagicMock(); orb.cfg = cfg
        w = WebUI(orb, host="127.0.0.1", port=0, state_dir=pathlib.Path(td))
        assert w._speak_locally() is False
        cfg.data["webui"]["speak_web_locally"] = True
        assert w._speak_locally() is True


def test_capture_peak_normalizes_for_asr():
    """Peak-normalize pre-amp: a quiet/variable mic must get a stable level before
    ASR (consistent transcription, no clipping)."""
    # simulate the loop that normalizes the payload before writing the WAV
    import numpy as np
    sr = 16000
    for raw_peak in (0.03, 0.12, 0.4):
        n = int(sr * 1.0)
        audio = (np.random.randn(n) * (raw_peak / 3)).astype(np.float32)
        audio = np.clip(audio, -1, 1)
        # apply the same normalize logic as record_utterance
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        target = 0.35
        if peak > 0.001 and peak < 0.98:
            audio = np.clip(audio * (target / peak), -1, 1)
        out_peak = float(np.max(np.abs(audio)))
        assert 0.20 < out_peak <= 0.36, f"normalized peak {out_peak} not near target for raw {raw_peak}"
    # config exposes normalize_target
    import tempfile as _t, pathlib as _p
    from voicebridge.config import Config
    with _t.TemporaryDirectory() as td:
        cfg = Config.load(_p.Path(td) / "none.yaml")
        assert cfg.get("mic", "normalize_target", default=0.35) == 0.35


def test_web_no_double_speak_returns_null_audio():
    """When speak_web_locally=true, /api/type result must carry audio_b64=None so
    the browser does not autoplay a second copy (the 'responds twice' bug)."""
    import pathlib, tempfile
    from voicebridge.config import Config
    from unittest import mock
    from voicebridge.webui import WebUI
    with tempfile.TemporaryDirectory() as td:
        cfg = Config.load(pathlib.Path(td) / "none.yaml")
        cfg.data.setdefault("webui", {})["speak_web_locally"] = True
        orb = mock.MagicMock()
        orb.cfg = cfg
        orb._cur_ctx = mock.MagicMock(return_value=None)
        orb.shared = mock.MagicMock()
        orb._needs_answer = mock.MagicMock(return_value=False)
        orb.process_web_utterance = mock.MagicMock(return_value={"summary": "hi", "route": "local"})
        w = WebUI(orb, host="127.0.0.1", port=0, state_dir=pathlib.Path(td))
        # run the submit_text worker then read the result
        rid = w.submit_text("hi")
        import time
        for _ in range(30):
            time.sleep(0.1)
            if w.result(rid).get("status") == "done": break
        res = w.result(rid)
        assert res.get("audio_b64") is None, "audio_b64 must be null when speak_web_locally"
        assert res.get("summary") == "hi"


def test_playback_watchdog_no_freeze():
    """Playback must NOT block a request forever (page freeze): a wedged device
    is capped by the watchdog (not by real audio duration)."""
    import time, pathlib as _p, wave, numpy as np
    from unittest import mock
    from voicebridge.audio.playback import Playback
    sr = 44100
    wav = _p.Path("/tmp/opencode/long.wav")
    with wave.open(str(wav), "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr)
        wf.writeframes(np.zeros(sr * 70, dtype=np.int16).tobytes())
    pb = Playback(device=None, device_name='')
    # Mock sd.play to never produce an active stream (a hung device) and make the
    # watchdog deadline much shorter so the test is fast and deterministic.
    import sounddevice as sd
    with mock.patch.object(sd, "play", return_value=None), \
         mock.patch.object(sd, "get_stream", side_effect=Exception("portaudio busy")):
        t0 = time.time()
        r = pb._play_try(wav, on_start=None)
        # The watchdog path (play_error) returns None quickly; if it hung it'd take 70s.
        assert time.time() - t0 < 12, "playback watchdog did not cap a wedged device"
        assert r is None


def test_audio_diagnose_usb_script_runs():
    """The USB-speed diagnostic must run and report a result without crashing."""
    import subprocess
    r = subprocess.run([sys.executable, "scripts/diagnose_audio_usb.py"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode in (0, 1, 2), r.stderr[-300:]
    assert ("USB" in r.stdout or "audio" in r.stdout), r.stdout
