import pytest  # noqa: E402
pytestmark = pytest.mark.dashboard  # live bridge probes (excluded from hermetic default)

"""Live stress probes for the web dashboard (text / voice / Right-Ctrl PTT).

One cycle of scripts/dashboard_stress.py against the RUNNING bridge.
Each probe skips (not fails) when its prerequisite is missing, so this file
stays green on headless/CI boxes while still enforcing health on this box.

Run: .venv/bin/python -m pytest tests/test_dashboard_stress.py -v
"""
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import dashboard_stress as ds  # noqa: E402

# Release acceptance: the live bridge is secured (Packet 1B) — resolve the
# port AND the configured X-Api-Token from the SAME config the bridge uses.
# The token is read at runtime and never printed.
from voicebridge.config import Config as _Cfg
_CFG = _Cfg.load()
BASE = f"http://127.0.0.1:{_CFG.get('webui', 'port', default=8890)}"
TOKEN = str(_CFG.get('webui', 'token', default='') or '')
WAV = Path.home() / ".local/state/openclaw-voice-bridge/tap-utterance.wav"


def _bridge_up() -> bool:
    req = urllib.request.Request(BASE + "/api/status",
                                 headers=({"X-Api-Token": TOKEN} if TOKEN else {}))
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return json.loads(r.read().decode()).get("bridge") == "up"
    except Exception:
        return False


def _has_xdotool() -> bool:
    try:
        return subprocess.run(["which", "xdotool"],
                              capture_output=True).returncode == 0
    except Exception:
        return False


def _cycle_or_skip():
    """Run one full dashboard cycle, RETRYING across the watchdog's ~60s
    cycle instead of skipping: a lock-busy overlap is transient (the user's
    dashboard_stress --watch process), so a bounded retry makes the suite
    deterministically zero-skip. Only if the lock stays held for 3 full
    windows do we fall back to a typed skip."""
    import time as _t
    for attempt in range(3):
        out = ds.run_cycle(BASE, TOKEN, WAV, lock_wait_s=45)
        if not any(r.get("probe") == "lock" for r in out):
            return out
        _t.sleep(5.0)
    pytest.skip("stress lock busy across 3 windows (watchdog mid-cycle)")


@pytest.mark.skipif(not _bridge_up(), reason="live gate: bridge not running")
def test_stress_status():
    out = _cycle_or_skip()
    status = next(r for r in out if r["probe"] == "status")
    assert status["ok"], status


@pytest.mark.skipif(not (_bridge_up() and WAV.exists()),
                    reason="live gate: bridge/sample wav missing")
def test_stress_text_turn_roundtrip():
    out = _cycle_or_skip()
    text = next(r for r in out if r["probe"] == "text")
    assert text["ok"], f"{text} — dashboard text turn must answer with content"
    # responsiveness budget: warm local turn should complete well under 8s
    assert text.get("latency_s", 99) < 8.0, f"text turn slow: {text}"


@pytest.mark.skipif(not (_bridge_up() and WAV.exists()),
                    reason="live gate: bridge/sample wav missing")
def test_stress_voice_turn_roundtrip():
    out = _cycle_or_skip()
    voice = next(r for r in out if r["probe"] == "voice")
    assert voice["ok"], f"{voice} — /api/talk pipeline must reach done"
    assert voice.get("latency_s", 99) < 20.0, f"voice turn slow: {voice}"


@pytest.mark.skipif(not (_bridge_up() and _has_xdotool()),
                    reason="live gate: xdotool/X unavailable")
def test_stress_rctrl_ptt_beep_event():
    out = _cycle_or_skip()
    rctrl = next(r for r in out if r["probe"] == "rctrl")
    assert rctrl["ok"], f"{rctrl} — synthetic Right Ctrl must trigger beep event"
