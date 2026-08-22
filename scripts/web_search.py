#!/usr/bin/env python3
"""Fast web search: DuckDuckGo first (~1-3s), Perplexity Pro browser fallback.

The user's daily questions ("latest node version", "weather in X") are
answered by DDG in ~2s — no browser, no API. Complex research that DDG can't
satisfy (or an explicit "use perplexity") falls back to the logged-in
Perplexity Pro browser session for a deep answer.

Usage: web_search.py "<query>" [--perplexity] [max_chars]
Exit 0 with the answer text on stdout. Exit 2 = no answer anywhere.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import re
import sys
import time

DDG_MIN_RESULTS = 2   # fewer than this -> fall back to Perplexity
PERPLEXITY_SCRIPT = ("/home/trick/.hermes/skills/research/perplexity-web-search/"
                     "scripts/perplexity_search.py")
BRIDGE_PYTHON = "/home/trick/Projects/Repos/openclaw-voice-bridge/.venv/bin/python"


def ddg_search(query: str, max_results: int = 5) -> list:
    """DuckDuckGo text search via the ddgs library (bridge venv)."""
    from ddgs import DDGS
    with DDGS() as d:
        return list(d.text(query, max_results=max_results))


def ddg_answer(query: str) -> str:
    """Compose a readable answer from DDG titles + snippets."""
    t0 = time.time()
    try:
        rs = ddg_search(query)
    except Exception as exc:
        print(f"ddg error: {exc}", file=sys.stderr)
        return ""
    if len(rs) < DDG_MIN_RESULTS:
        print(f"ddg thin results ({len(rs)})", file=sys.stderr)
        return ""
    lines = []
    for r in rs[:4]:
        title = (r.get("title") or "").strip()
        body = (r.get("body") or "").strip()
        href = (r.get("href") or "").strip()
        if title:
            lines.append(f"{title}: {body}" if body else title)
    if not lines:
        return ""
    print(f"ddg took {time.time()-t0:.1f}s", file=sys.stderr)
    return "\n".join(lines)


def perplexity_answer(query: str, max_chars: int) -> str:
    """Deep fallback via the logged-in Perplexity Pro browser session."""
    cmd = [BRIDGE_PYTHON, PERPLEXITY_SCRIPT, query, str(max_chars)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return (proc.stdout or "").strip()
    except Exception as exc:
        print(f"perplexity error: {exc}", file=sys.stderr)
        return ""


def main() -> int:
    args = sys.argv[1:]
    force_perplexity = "--perplexity" in args
    args = [a for a in args if a != "--perplexity"]
    query = args[0] if args else ""
    max_chars = int(args[1]) if len(args) > 1 else 1500
    if not query:
        print("usage: web_search.py <query> [--perplexity] [max_chars]", file=sys.stderr)
        return 2

    # Weather: Bucharest (and mangled 'Corest') must return real temps, not DDG titles.
    if re.search(r"weather|temperature|forecast", query, re.I):
        try:
            import subprocess, pathlib
            venv = pathlib.Path.home() / "Projects/Repos/openclaw-voice-bridge/.venv/bin/python"
            weather = pathlib.Path.home() / "Projects/Repos/openclaw-voice-bridge/scripts/weather.py"
            r = subprocess.run([str(venv), str(weather), query], capture_output=True, text=True, timeout=12)
            if r.returncode == 0 and r.stdout.strip():
                print(r.stdout.strip())
                return 0
        except Exception as e:
            print(f"weather fallback err: {e}", file=sys.stderr)
    if not force_perplexity:
        answer = ddg_answer(query)
        if answer:
            print(answer)
            return 0
    answer = perplexity_answer(query, max_chars)
    if answer:
        print(answer)
        return 0
    print("No answer available from DuckDuckGo or Perplexity.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
