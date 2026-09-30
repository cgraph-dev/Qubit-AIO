#!/usr/bin/env python3
"""SPA click-driven capture. Boots at the sidebar page, then clicks each
sidebar link by href to navigate client-side (direct deep-URL loads often 404
on SPAs like g2g), waits for hydration, and dumps body text + structure +
screenshot.

Usage:
  python3 deep_capture.py <boot_url> <tab_index> <output.md> href1::name1 href2::name2 ...
"""
import json
import os
import sys
import time
import urllib.request

PORT = 8791
BASE = f"http://127.0.0.1:{PORT}/"


def post(cmd, timeout=90):
    data = json.dumps(cmd).encode("utf-8")
    req = urllib.request.Request(BASE, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    boot_url = sys.argv[1]
    tab = int(sys.argv[2])
    out_path = sys.argv[3]
    pairs = sys.argv[4:]
    lines = []
    post({"action": "goto", "url": boot_url, "tab": tab})
    time.sleep(6)
    for pair in pairs:
        href, name = pair.split("::", 1)
        try:
            expr = (
                "(() => { const href = " + json.dumps(href) + "; "
                "const links = Array.from(document.querySelectorAll('a')); "
                "const target = links.find(a => a.getAttribute('href') === href || (a.href||'').endsWith(href)); "
                "if (!target) return 'no-link'; target.click(); return 'clicked'; })()"
            )
            post({"action": "eval", "expr": expr})
            time.sleep(7)
            body = post({"action": "eval", "expr": "(() => { const t = document.body ? document.body.innerText.replace(/\\n+/g,' | ').slice(0,2500) : ''; return location.href + ' >>> ' + t; })()"})
            d = post({"action": "dump", "name": name})
            post({"action": "shot", "name": name})
            lines.append(f"## {name}\n")
            lines.append(f"- **Result:** {str(body.get('result',''))[:2500]}\n\n")
            print(f"OK {name}", flush=True)
        except Exception as e:
            lines.append(f"## {name}\n- ERROR: {e!r}\n")
            print(f"ERR {name}: {e!r}", flush=True)
        post({"action": "goto", "url": boot_url, "tab": tab})
        time.sleep(5)
    with open(out_path, "w") as f:
        f.write("".join(lines))
    print(f"DONE {out_path}", flush=True)


if __name__ == "__main__":
    main()
