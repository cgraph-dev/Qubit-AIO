#!/usr/bin/env python
"""Rebuild the resident brain alias from the repo Modelfile (Packet 4B form).

Packet 4B removed the baked private biography: Modelfile.qubit4 now carries a
minimal persona/safety SYSTEM plus the pinned no-think template. Personal
facts are injected at request time by the bounded context compiler
(voicebridge/context_compiler.py) from user-local storage — never baked into
the model artifact.

This script only: (1) runs `ollama create qubit-brain4` from the repository
Modelfile and (2) smoke-tests identity/style. It no longer composes or
injects any memory block, and it never prints private values.
"""
from __future__ import annotations

import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEFILE = ROOT / "Modelfile.qubit4"


def json_dumps(o):
    import json
    return json.dumps(o).encode()


def json_loads(b):
    import json
    if isinstance(b, bytes):
        b = b.decode()
    return json.loads(b)


def ollama_create() -> bool:
    r = subprocess.run(["ollama", "create", "qubit-brain4", "-f", str(MODEFILE)],
                       capture_output=True, text=True)
    ok = r.returncode == 0
    print(f"[bake] ollama create: {'ok' if ok else r.stderr[-400:]}")
    return ok


def _baked_system() -> str:
    """Extract the SYSTEM text from the repo Modelfile (artifact truth)."""
    import re
    m = re.search(r'SYSTEM """(.*?)"""', MODEFILE.read_text(), re.S)
    return m.group(1) if m else ""


def smoke_test() -> bool:
    """Identity/style probes only (Packet 4B): no private-fact assertions.

    Payload mirrors deployment shape: the persona system message is ALWAYS
    present in production calls; the /no_think soft switch is appended
    to each user probe (the only form this quant honors).
    Runner options come from the canonical contract; a missing personal fact
    must be answered with an honest unknown, never invented.
    """
    sys.path.insert(0, str(ROOT))
    from voicebridge.runner_contract import resolve
    rc = resolve()
    system_text = _baked_system()
    probes = [
        ("who are you?", ["qubit"]),
        ("hello! what can you do for me today?", []),
    ]
    all_ok = True
    for q, needles in probes:
        messages = [{"role": "user", "content": q}]
        if system_text:
            messages.insert(0, {"role": "system", "content": system_text})
        payload = {"model": rc.alias, "stream": False,
                   "keep_alive": rc.keep_alive,
                   "options": {"num_ctx": rc.num_ctx},
                   # Packet 4B: soft no-think switch (user-turn literal)
                   "messages": [{"role": m["role"],
                                 "content": m["content"] + (
                                     " /no_think" if m["role"] == "user"
                                     else "")}
                                for m in messages]}
        req = urllib.request.Request(rc.url + "/api/chat",
                                     data=json_dumps(payload),
                                     headers={"Content-Type": "application/json"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = json_loads(resp.read().decode())
            ans = (body.get("message", {}).get("content") or "").lower()
        except Exception as exc:
            print(f"[bake] {q!r} -> transport failure: {str(exc)[:120]}")
            all_ok = False
            continue
        hit = all(n in ans for n in needles)
        nonempty = bool(ans.strip())
        all_ok &= nonempty and hit
        print(f"[bake] {q!r} -> {len(ans)}ch ({time.time()-t0:.2f}s) "
              f"{'OK' if (nonempty and hit) else 'MISS: ' + ans[:80]}")
    return all_ok


def main() -> int:
    text = MODEFILE.read_text()
    for forbidden_marker in ("<<<BAKED-MEMORY>>>", "<<<END-MEMORY>>>"):
        if forbidden_marker in text:
            print(f"[bake] REFUSED: {MODEFILE.name} still contains "
                  f"{forbidden_marker!r}; Packet 4B forbids baking memory.")
            return 1
    if not ollama_create():
        return 1
    time.sleep(0.5)
    return 0 if smoke_test() else 2


if __name__ == "__main__":
    sys.exit(main())
