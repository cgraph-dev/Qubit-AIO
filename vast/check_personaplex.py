#!/usr/bin/env python3
"""PersonaPlex connectivity check (run on the Vast box or your PC).

Usage:
    python3 check_personaplex.py [wss://host:8998/api/chat] [voice_prompt]

Exits 0 and prints handshake RTT on success. Uses wss + insecure TLS (the
server ships self-signed certs); traffic rides Tailscale anyway.
"""
import asyncio
import ssl
import sys
import time
import urllib.parse

import websockets

URL = sys.argv[1] if len(sys.argv) > 1 else "wss://127.0.0.1:8998/api/chat"
VOICE = sys.argv[2] if len(sys.argv) > 2 else "NATF2.pt"


async def main() -> int:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    q = urllib.parse.urlencode({"voice_prompt": VOICE, "text_prompt": "hi", "seed": "-1"})
    sep = "&" if "?" in URL else "?"
    t0 = time.time()
    try:
        async with websockets.connect(f"{URL}{sep}{q}", ssl=ctx,
                                      max_size=None, open_timeout=10) as ws:
            rtt = (time.time() - t0) * 1000
            print(f"OK  handshake in {rtt:.0f} ms -> {URL} (voice={VOICE})")
            return 0
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
