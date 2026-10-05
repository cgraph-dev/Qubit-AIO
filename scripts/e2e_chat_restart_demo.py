"""Standalone demo server for the curl transcript in docs/E2E_CHAT_RESTART_PROOF.md.

Starts the REAL dashboard listener on port 8891 with a throwaway home, so the
doc can reproduce the restart-survival story with plain curl:

  boot 1  -- a chat turn runs; the model call parks forever, so the turn never
             concludes. The doc kills boot 1 mid-turn (SIGKILL), exactly what an
             operator restart does. The crash marker survives on disk.
  boot 2  -- a fresh process over the same home. The doc curls the session-show
             route and sees ``interrupted_turn`` with the original prompt.

The model call is the ONLY stub; persistence, the marker, and both routes are
the shipped code.

Usage (see the doc for the full transcript):
    .venv/bin/python scripts/e2e_chat_restart_demo.py boot1   # then kill -9
    .venv/bin/python scripts/e2e_chat_restart_demo.py boot2
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for p in (str(REPO), str(REPO / "qubit_runtime")):
    if p not in sys.path:
        sys.path.insert(0, p)

PORT = int(os.environ.get("QUBIT_E2E_DEMO_PORT", "8891"))
BASE = Path(os.environ.get("QUBIT_E2E_DEMO_BASE", "/tmp/qubit-e2e-demo"))
HOME = BASE / "runtime"
TOKEN = os.environ.get("QUBIT_E2E_DEMO_TOKEN", "demo-token")


def build_cfg():
    from voicebridge.config import Config

    BASE.mkdir(parents=True, exist_ok=True)
    HOME.mkdir(parents=True, exist_ok=True)
    os.environ["QUBIT_HOME"] = str(HOME)
    os.environ["XDG_STATE_HOME"] = str(BASE / "xdg")
    cfg_path = BASE / "voice.yaml"
    if not cfg_path.exists():
        cfg_path.write_text(
            "logging:\n"
            f"  jsonl_path: '{BASE / 'state'}'\n"
            "webui:\n"
            f"  token: {TOKEN}\n"
            "paths:\n"
            f"  home: '{HOME}'\n"
        )
    return Config.load(cfg_path)


def build_server(cfg):
    from voicebridge.webui import WebUI

    class O:
        def __init__(self):
            self.cfg = cfg
            self.task = type("T", (), {"active": False, "run_id": None})()
            self._route = "gateway"
            self._web_active = 0

    ui = WebUI(O(), host="127.0.0.1", port=PORT, state_dir=cfg.state_dir())
    return ui


def stub_model(park_forever: bool):
    from voicebridge.providers import gateway as GW
    from voicebridge.providers.contracts import ProviderResult

    FOREVER = threading.Event()

    def fake(prompt, cfg, *, on_sentence=None, should_stop=None, role="chat", **kw):
        if role != "chat":
            return ProviderResult(ok=False, code="MODEL_UNAVAILABLE",
                                  message="demo: no classify model")
        if on_sentence:
            on_sentence("working...")
        if park_forever:
            FOREVER.wait(timeout=300)  # boot 1 is killed while parked here
        return ProviderResult(ok=True, text="working... done", provider="local",
                              model="demo", identity="local/demo")

    GW.run_role = fake


def main() -> int:
    mode = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    if mode not in ("boot1", "boot2"):
        print("usage: e2e_chat_restart_demo.py boot1|boot2", file=sys.stderr)
        return 2
    cfg = build_cfg()
    stub_model(park_forever=(mode == "boot1"))
    ui = build_server(cfg)
    ui.start()
    print(json.dumps({"mode": mode, "listening": f"http://127.0.0.1:{PORT}",
                      "token": TOKEN, "home": str(HOME)}))
    sys.stdout.flush()
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        ui.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
