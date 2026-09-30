"""Plugins hub must serve the real fleet on a fresh interpreter.

Regression: ``plugins_hub`` imported the vendored dashboard module without
putting the fork root on ``sys.path`` first, so any process whose first
compat call was the hub (fresh tests, early dashboard loads after a
restart) got ``plugins: []`` plus ``hub_error: No module named
'qubit_cli'`` instead of the real fleet. The test below runs the dispatch
in a clean interpreter with an isolated home, proving the first call works.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_PROBE = (
    "import sys, os, json, tempfile, pathlib;"
    "sys.path.insert(0, {root});"
    "tmp = pathlib.Path(tempfile.mkdtemp());"
    "os.environ['XDG_CONFIG_HOME'] = str(tmp / 'c');"
    "os.environ['XDG_STATE_HOME'] = str(tmp / 's');"
    "os.environ['XDG_DATA_HOME'] = str(tmp / 'd');"
    "os.environ['QUBIT_HOME'] = str(tmp / 'qh');"
    "open(tmp / 'voice.yaml', 'w').write('logging:\\n  jsonl: false\\n');"
    "from voicebridge import config as C, qubit_compat as HC;"
    "cfg = C.Config.load(tmp / 'voice.yaml');"
    "code, out = HC.dispatch(cfg, 'GET', '/api/dashboard/plugins/hub', {{}}, {{}});"
    "print(json.dumps({{'code': code, 'hub_error': out.get('hub_error'),"
    " 'plugins': len(out.get('plugins', [])),"
    " 'orphans': len(out.get('orphan_dashboard_plugins', []))}}))"
)


def test_hub_serves_fleet_on_first_call_in_fresh_interpreter():
    import json as _json

    probe = _PROBE.format(root=repr(str(REPO_ROOT)))
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True, text=True, timeout=120, cwd=str(REPO_ROOT))
    assert proc.returncode == 0, proc.stderr[-2000:]
    got = _json.loads(proc.stdout.strip().splitlines()[-1])
    assert got["code"] == 200, got
    assert not got["hub_error"], got
    assert got["plugins"] > 0, got


def test_plugin_enable_disable_round_trip_through_dispatch(tmp_path,
                                                           monkeypatch):
    from voicebridge import config as C
    from voicebridge import qubit_compat as HC

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qhome"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    cfg = C.Config.load(p)

    code, out = HC.dispatch(
        cfg, "POST", "/api/dashboard/agent-plugins/browser/browserbase/enable",
        {}, {})
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)
    code, out = HC.dispatch(
        cfg, "POST", "/api/dashboard/agent-plugins/browser/browserbase/enable",
        {}, {"confirm": True})
    assert code == 200 and out.get("ok") is True, (code, out)
    code, out = HC.dispatch(
        cfg, "POST",
        "/api/dashboard/agent-plugins/browser/browserbase/disable",
        {}, {"confirm": True})
    assert code == 200 and out.get("ok") is True, (code, out)
