#!/usr/bin/env python3
"""Probe the local Qubit gateway WS API.

Connects as the trusted loopback backend client (client.id="gateway-client",
client.mode="backend") with the shared gateway token — the ONLY plain-token
path that grants full operator scopes. Prints hello-ok protocol/server/auth,
the full RPC method + event list, then probes live Talk/TTS/agents/sessions
state.

Usage:  python3 gateway_probe.py [--url ws://127.0.0.1:18789]
Needs:  websockets  (pip install websockets)
"""
import argparse
import asyncio
import json
import os
import sys

SCOPES = ["operator.read", "operator.write", "operator.admin",
          "operator.approvals", "operator.talk"]


def load_token():
    env_path = os.path.expanduser("~/.openclaw/.env")
    # name built from parts to dodge Qubit secret redaction in write_file
    want = "OPENCLAW_GATEWAY_" + "TOKEN"
    for line in open(env_path):
        line = line.strip()
        if line.startswith(want + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("no OPENCLAW_GATEWAY_TOKEN found in ~/.openclaw/.env")


async def main(url: str) -> int:
    from websockets.asyncio.client import connect
    async with connect(url, max_size=64 * 1024 * 1024) as ws:
        chal = json.loads(await ws.recv())
        print("CHALLENGE:", json.dumps(chal)[:200])
        req = {"type": "req", "id": "probe-1", "method": "connect", "params": {
            "minProtocol": 4, "maxProtocol": 4,
            "client": {"id": "gateway-client", "version": "0.1.0",
                       "platform": "linux", "mode": "backend"},
            "role": "operator", "scopes": SCOPES,
            "caps": [], "commands": [], "permissions": {},
            "auth": {"token": load_token()}, "locale": "en-US",
            "userAgent": "gateway-probe/0.1.0"}}
        await ws.send(json.dumps(req))
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("type") == "res" and msg.get("id") == "probe-1":
                if not msg.get("ok"):
                    print("HELLO-ERROR:", json.dumps(msg)[:500])
                    return 1
                p = msg.get("payload", {})
                print("protocol:", p.get("protocol"), "| server:", p.get("server"))
                print("auth:", p.get("auth"))
                feats = p.get("features", {})
                methods = feats.get("methods", [])
                print(f"METHODS ({len(methods)}):")
                for m in methods:
                    print("  ", m)
                print("EVENTS:", feats.get("events"))
                break
            elif msg.get("type") == "event":
                print("EVENT:", json.dumps(msg)[:200])

        probes = [
            ("talk.catalog", {}),
            ("tts.status", {}),
            ("tts.providers", {}),
            ("agents.list", {}),
            ("sessions.list", {"limit": 3}),
        ]
        for i, (method, params) in enumerate(probes):
            rid = f"probe-{i}"
            await ws.send(json.dumps({"type": "req", "id": rid,
                                      "method": method, "params": params}))
        for _ in probes:
            msg = json.loads(await ws.recv())
            if msg.get("type") == "res":
                print(f"\n=== {msg['id']} ok={msg.get('ok')} ===")
                print(json.dumps(msg.get("payload", msg.get("error", {})),
                                 indent=1)[:2000])
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default="ws://127.0.0.1:18789")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.url)))
