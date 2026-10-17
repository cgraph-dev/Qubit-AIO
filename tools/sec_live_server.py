#!/usr/bin/env python3
"""Boot the real WebUIHandler from THIS worktree on a free port.

A minimal stub orchestrator supplies only `cfg` (a real voicebridge Config).
Everything else is the production code path: the real HTTP handler, the real
office gates, the real document serving. Used to prove the forged-header close
and the app bootstrap against a live socket.
"""
import socket
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge import config as C  # noqa: E402
from voicebridge import webui as W  # noqa: E402


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Orch:
    def __init__(self, cfg):
        self.cfg = cfg


def build(*, token: str = "test-session-token-abc"):
    import os
    tmp = Path(tempfile.mkdtemp(prefix="sec-office-"))
    os.environ["XDG_CONFIG_HOME"] = str(tmp / "c")
    os.environ["XDG_STATE_HOME"] = str(tmp / "s")
    os.environ["XDG_DATA_HOME"] = str(tmp / "d")
    cfgfile = tmp / "voice.yaml"
    cfgfile.write_text("logging:\n  jsonl: false\nwebui:\n  host: 127.0.0.1\n"
                       "  token: %s\n" % token)
    cfg = C.Config.load(cfgfile)
    port = free_port()
    ui = W.WebUI(_Orch(cfg), host="127.0.0.1", port=port,
                 state_dir=tmp / "state")
    ui.api_token = token
    return ui, port, token


def main():
    ui, port, token = build()
    ui.start()
    print(f"PORT={port}")
    print(f"TOKEN={token}")
    import time
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
