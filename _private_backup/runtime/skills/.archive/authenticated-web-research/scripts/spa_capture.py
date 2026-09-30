#!/usr/bin/env python3
"""Click-driven capture for SPA sites (g2g). Boots at the sidebar page, then
clicks each sidebar link by href to navigate client-side, dumps structure and
screenshots. Direct goto of deep SPA routes 404s, so navigation must be
client-side clicks.

Usage:
  python3 spa_capture.py <boot_url> <tab_index> <output.md> href1::name1 href2::name2 ...
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
    r = post({"action": "goto", "url": boot_url, "tab": tab})
    time.sleep(6)
    if r.get("status") != "ok":
        print("BOOT FAIL", r.get("error"))
        return
    lines.append(f"# Boot: {boot_url}\n")
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
            time.sleep(4)
            d = post({"action": "dump", "name": name})
            post({"action": "shot", "name": name})
            path = d.get("path")
            data = json.load(open(path)) if path else {}
            lines.append(f"## {name}\n")
            lines.append(f"- **URL:** {data.get('url')} | **Title:** {data.get('title')}\n")
            for h in ("h1", "h2", "h3"):
                if data.get(h):
                    lines.append(f"- {h.upper()}: " + " | ".join(str(x)[:70] for x in data[h][:10]) + "\n")
            nav = [f"{l['text']} -> {l['href']}" for l in data.get("nav_links", []) if l.get("text")]
            if nav:
                lines.append("- **Nav:**\n" + "\n".join(f"  - {n[:130]}" for n in nav[:30]) + "\n")
            btns = [b["text"] for b in data.get("buttons", []) if b.get("text")]
            if btns:
                lines.append("- **Buttons:** " + " | ".join(btns[:35]) + "\n")
            ins = [f"{i.get('name') or i.get('placeholder') or i.get('text')} ({i.get('type')})" for i in data.get("inputs", []) if (i.get('name') or i.get('placeholder') or i.get('text'))]
            if ins:
                lines.append("- **Inputs:** " + " | ".join(ins[:35]) + "\n")
            if data.get("table_headers"):
                lines.append("- **Table headers:** " + " | ".join(str(x)[:40] for x in data["table_headers"][:35]) + "\n")
            if data.get("badges"):
                lines.append("- **Badges:** " + " | ".join(str(b)[:45] for b in data["badges"][:25]) + "\n")
            lines.append("\n")
            print(f"OK {name} -> {data.get('url')}", flush=True)
        except Exception as e:
            lines.append(f"## {name}\n- ERROR: {e!r}\n")
            print(f"ERR {name}: {e!r}", flush=True)
        # navigate back to sidebar to reset
        post({"action": "goto", "url": boot_url, "tab": tab})
        time.sleep(4)
    with open(out_path, "w") as f:
        f.write("".join(lines))
    print(f"DONE wrote {out_path}", flush=True)


if __name__ == "__main__":
    main()
