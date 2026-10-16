"""Read-only authenticated cross-page smoke; captures failed loads, never mutates state.

Use --out under the runtime scratch tree. Browser-intercepted 503s are local to
this disposable context; they do not change the server or the operator browser.
"""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def routes(source: str) -> list[str]:
    return sorted({p for p in re.findall(r'"(/[^"\s]*)"\s*:\s*\w+', source)
                   if p not in {"/", "/chat", "/qubit-chat"} and ":" not in p})


def classify(text: str, errors: list[str], intercepted: int = 0) -> str:
    if errors:
        return "render-error"
    if not text.strip():
        return "blank"
    if intercepted and not re.search(r"error|failed|unavailable|retry|try again", text, re.I):
        return "missing-error-recovery"
    return "rendered"  # Not a capability or parity verdict.


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--browser", default="/usr/bin/google-chrome")
    parser.add_argument("--fault", action="store_true")
    parser.add_argument("--path", action="append", dest="paths", help="Limit to a built-in route (repeatable)")
    parser.add_argument("--wait-ms", type=int, default=5000)
    args = parser.parse_args()
    from playwright.sync_api import sync_playwright
    rows = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=args.browser)
        try:
            discovered = routes((ROOT / "frontend/qubit-web/src/App.tsx").read_text())
            if args.paths and any(path not in discovered for path in args.paths):
                parser.error("--path must name a discovered built-in route")
            for path in args.paths or discovered:
                page = browser.new_page()
                errors: list[str] = []
                intercepted: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                if args.fault:
                    def fail(route):
                        intercepted.append(route.request.url)
                        route.fulfill(status=503, content_type="application/json",
                                      body='{"error":"SMOKE_BACKEND_UNAVAILABLE"}')
                    page.route("**/api/**", fail)
                try:
                    page.goto("http://127.0.0.1:8890" + path, wait_until="domcontentloaded")
                    page.wait_for_timeout(args.wait_ms)
                    token = page.evaluate("Boolean(window.__QUBIT_SESSION_TOKEN__)")
                    # Exclude global navigation so it cannot hide a blank page.
                    content = page.locator("main").inner_text()
                    rows.append(dict(path=path, authenticated=token, errors=errors,
                                     intercepted=len(intercepted), text=content,
                                     classification=classify(content, errors, len(intercepted))))
                finally:
                    page.close()
        finally:
            browser.close()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rows, indent=2))
    print(json.dumps({"pages": len(rows), "out": str(args.out)}))
    return int(not rows or any(not r["authenticated"] or r["classification"] != "rendered" for r in rows))


if __name__ == "__main__":
    raise SystemExit(main())
