"""Detached warm-up for the local ``qubit-brain`` model.

A gaming unload can be followed by "wake up the brain". This module runs in
its own short-lived process so the load survives the caller (the bridge turn
that acknowledged it) and never blocks the bridge loop. It sends one token at
the voice context and exits.
"""
from __future__ import annotations

import json
import os
import urllib.request

MODEL = "qubit-brain"
KEEPALIVE = "10m"
NUM_CTX = 32768


def payload() -> dict:
    return {
        "model": MODEL,
        "prompt": "hi",
        "stream": False,
        "keep_alive": KEEPALIVE,
        "options": {"num_predict": 1, "num_ctx": NUM_CTX},
    }


def base_url() -> str:
    host = str(os.environ.get("OLLAMA_HOST") or "127.0.0.1:11434").strip()
    if "://" not in host:
        host = "http://" + host
    return host.rstrip("/")


def main() -> int:
    try:
        req = urllib.request.Request(
            base_url() + "/api/generate",
            data=json.dumps(payload()).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=90) as resp:
            resp.read()
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
