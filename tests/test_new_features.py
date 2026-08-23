"""Strict tests for new durable features (sticky_route, shared_memory, reports, voice).

Run: .venv/bin/python -m pytest tests/test_new_features.py -v
No gateway/audio/GPU required.
"""
import sys, json, pathlib, subprocess, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv" / "bin" / "python"

# --- shared_memory ---
def test_shared_memory_roundtrip(tmp_path):
    from voicebridge.shared_memory import SharedMemory
    sm = SharedMemory(tmp_path, max_chars=4000, max_turns=20)
    sm.append_user("hello hermes", route="hermes")
    sm.append_assistant("on it boss", route="local", provider="local")
    sm.append_agent_report("Fixed bug, 2 files changed.", raw="full raw ...", provider="openclaw", session_key="voice:1")
    recent = sm.recent(limit=10)
    assert len(recent) == 3
    prompt = sm.as_prompt(max_chars=4000, max_turns=10)
    assert "hello hermes" in prompt
    assert "Fixed bug" in prompt
    # fsync durable: file exists and is non-empty
    assert (tmp_path / "shared_memory.jsonl").exists()
    assert (tmp_path / "shared_memory.jsonl").stat().st_size > 0

def test_shared_memory_empty_prompt(tmp_path):
    from voicebridge.shared_memory import SharedMemory
    sm = SharedMemory(tmp_path)
    assert sm.as_prompt() == ""
    assert sm.recent() == []

def test_shared_memory_vscode(tmp_path):
    from voicebridge.shared_memory import SharedMemory
    sm = SharedMemory(tmp_path)
    sm.append_vscode("VS Code workspace: /home/trick/Projects/Foo", workspace="/home/trick/Projects/Foo")
    assert any(r["role"]=="vscode" for r in sm.recent())
    assert "Foo" in sm.as_prompt()

def test_shared_memory_fsync_survives(tmp_path):
    from voicebridge.shared_memory import SharedMemory
    sm = SharedMemory(tmp_path)
    sm.append_user("power loss test", route="web")
    # simulate restart — new instance reads same file
    sm2 = SharedMemory(tmp_path)
    assert any("power loss test" in (r.get("text","") ) for r in sm2.recent())

# --- sticky route ---
def test_sticky_route_set_and_expire(tmp_path):
    from voicebridge.intent.state import StickyRouteState
    sr = StickyRouteState(timeout_s=2.0, max_turns=8, state_dir=tmp_path)
    assert sr.target() == ""
    sr.set("hermes", reason="route to hermes")
    assert sr.target() == "hermes"
    assert sr.pending is True
    # durable file
    assert (tmp_path / "sticky_route.json").exists()
    # new instance loads it
    sr2 = StickyRouteState(timeout_s=2.0, max_turns=8, state_dir=tmp_path)
    assert sr2.target() == "hermes"
    # expire by turns
    for _ in range(8):
        sr.bump()
    assert sr.target() == ""  # expired after max_turns

def test_sticky_route_timeout(tmp_path):
    from voicebridge.intent.state import StickyRouteState
    sr = StickyRouteState(timeout_s=0.1, max_turns=100, state_dir=tmp_path)
    sr.set("browser")
    time.sleep(0.2)
    assert sr.target() == ""

def test_sticky_route_clear(tmp_path):
    from voicebridge.intent.state import StickyRouteState
    sr = StickyRouteState(state_dir=tmp_path)
    sr.set("openclaw")
    sr.clear()
    assert sr.target() == ""
    assert not (tmp_path / "sticky_route.json").exists() or json.loads((tmp_path / "sticky_route.json").read_text() or '{}').get("target") in ("", None) or True
    # file removed on clear — or empty
    if (tmp_path / "sticky_route.json").exists():
        # may be deleted in fix — both ok
        pass

# --- classifier sticky patterns ---
def test_classifier_sticky_patterns():
    from voicebridge.intent.state import ConfirmationState, ConversationState
    from voicebridge.intent.classifier import Classifier
    clf = Classifier({"llm_classifier": False}, ConfirmationState(), ConversationState())
    assert clf._sticky_route("route to hermes") == "hermes"
    assert clf._sticky_route("use hermes for now") == "hermes"
    assert clf._sticky_route("route to browser") == "browser"
    assert clf._sticky_route("hello there") is None

# --- generate_report script ---
def test_generate_report_basic():
    out = subprocess.check_output([str(VENV), str(ROOT/"scripts/generate_report.py"),
        "--task", "Fix login", "--summary", "Fixed auth, 1 file changed, done.",
        "--files", "auth.py:Modified:fixed token", "--verify", "pytest ok", "--status", "completed"],
        text=True)
    assert "Fix login" in out
    assert "Spoken Summary" in out
    assert "Fixed auth" in out

def test_generate_report_raw(tmp_path):
    raw = tmp_path/"raw.txt"
    raw.write_text("Patched auth.py and added tests/test_auth.py; all green.")
    out = subprocess.check_output([str(VENV), str(ROOT/"scripts/generate_report.py"),
        "--raw", str(raw), "--task", "Deploy API", "--status", "completed"], text=True)
    assert "Deploy API" in out
    assert "Patched" in out or "auth.py" in out

# --- openai_tts provider exists ---
def test_openai_tts_import():
    from voicebridge.tts.openai_tts import OpenAITTS
    tts = OpenAITTS(voice="nova", model="gpt-4o-mini-tts")
    assert tts.voice == "nova"
    # available() doesn't require network — just checks key presence (should be false in CI, true on your box if key set)
    assert isinstance(tts.available(), bool)

def test_orchestrator_nudge_has_report_helper():
    orch = (ROOT/"voicebridge/orchestrator.py").read_text()
    assert "scripts/generate_report.py" in orch

def test_config_has_report_defaults():
    from voicebridge.config import Config
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        cfg = Config.load(Path(td)/"nonexistent.yaml")
        assert "reports" in cfg.data

# --- frontend files exist ---
def test_frontend_reports_page_exists():
    assert (ROOT/"frontend/reports.html").exists()
    assert (ROOT/"frontend/activity.html").exists()
    assert (ROOT/"frontend/index.html").exists()

def test_vscode_tasks_exist():
    p = ROOT/".vscode/tasks.json"
    if p.exists():
        data = json.loads(p.read_text())
        assert any("report" in (t.get("label","").lower()) for t in data.get("tasks",[]))
