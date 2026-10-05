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

import subprocess
import re
import sys
import time
from pathlib import Path

DDG_MIN_RESULTS = 2   # fewer than this -> fall back to Perplexity
#: DDG layer bound (Lane 4): the browser layer is 25 s-bounded, so the fast
#: layer gets 10 s — a stalled DDG must fall through loudly, never hold a
#: voice turn hostage.
DDG_TIMEOUT_S = 10
#: Browser-Pro layer bound (Slice 2): the CDP-driven Perplexity session must
#: never hold a voice turn hostage — 25 s, then a loud degrade. The old
#: 120 s timeout is unusable for live voice rhythm (CDP DOWN stalls it).
BROWSER_PRO_TIMEOUT_S = 25
# Portable resolution: bridge-local script first, then the Hermes skill copy
# (migration compat). Never a bare system python: the bridge venv python runs
# this script, so the current interpreter is the launcher.
BRIDGE_PYTHON = sys.executable
_LOCAL = Path(__file__).resolve().parent / "perplexity_search.py"
_HERMES = (Path.home() / ".hermes/skills/research/perplexity-web-search"
           / "scripts" / "perplexity_search.py")
PERPLEXITY_SCRIPT = str(_LOCAL if _LOCAL.is_file() else _HERMES)


def ddg_search(query: str, max_results: int = 5) -> list:
    """DuckDuckGo text search via the ddgs library (bridge venv)."""
    from ddgs import DDGS
    with DDGS(timeout=DDG_TIMEOUT_S) as d:
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
        if title:
            lines.append(f"{title}: {body}" if body else title)
    if not lines:
        return ""
    print(f"ddg took {time.time()-t0:.1f}s", file=sys.stderr)
    return "\n".join(lines)


def perplexity_answer(query: str, max_chars: int) -> str:
    """Deep fallback via the logged-in Perplexity Pro browser session.

    Bounded to ``BROWSER_PRO_TIMEOUT_S`` (Slice 2) and loud on every
    degrade path: CDP down (exit 2 from the probe script), timeout, or
    empty — each prints its reason to stderr so a skipped layer is never
    a silent empty. Returns "" on any miss (caller falls through).
    """
    cmd = [BRIDGE_PYTHON, PERPLEXITY_SCRIPT, query, str(max_chars)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=BROWSER_PRO_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        print(f"perplexity browser probe timed out after "
              f"{BROWSER_PRO_TIMEOUT_S}s (CDP path degraded) — "
              f"falling through", file=sys.stderr)
        return ""
    except Exception as exc:
        print(f"perplexity error: {exc} — falling through",
              file=sys.stderr)
        return ""
    if proc.returncode == 2:
        print("perplexity browser probe: CDP not reachable on "
              "127.0.0.1:9222 (browser search unavailable, using API) — "
              "falling through", file=sys.stderr)
        return ""
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()
        reason = tail[-1][:160] if tail else f"exit {proc.returncode}"
        print(f"perplexity browser probe missed ({reason}) — "
              f"falling through", file=sys.stderr)
        return ""
    answer = (proc.stdout or "").strip()
    if not answer:
        print("perplexity browser probe returned empty — falling through",
              file=sys.stderr)
        return ""
    return answer


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
            import pathlib
            weather = pathlib.Path(__file__).resolve().parent / "weather.py"
            r = subprocess.run([sys.executable, str(weather), query],
                               capture_output=True, text=True, timeout=12)
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
