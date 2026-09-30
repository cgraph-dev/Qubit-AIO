#!/usr/bin/env python3
"""Batch capture helper for non-SPA sites. POSTs commands to the research
launcher for a list of (name, url) pairs, saving structure dumps + screenshots,
then emits a compact markdown summary.

Usage:
    python3 batch_capture.py <output.md> <tab_index> [name::url pairs...]
"""
import json
import os
import sys
import time
import urllib.request

PORT = 8791
BASE = f"http://127.0.0.1:{PORT}/"


def post(cmd, timeout=60):
    data = json.dumps(cmd).encode("utf-8")
    req = urllib.request.Request(BASE, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    out_path = sys.argv[1]
    tab = int(sys.argv[2])
    pairs = sys.argv[3:]
    lines = []
    for pair in pairs:
        name, url = pair.split("::", 1)
        try:
            r = post({"action": "goto", "url": url, "tab": tab})
            time.sleep(1.0)
            if r.get("status") != "ok":
                lines.append(f"## {name}\n- GOTO FAILED: {r.get('error')}\n")
                print(f"FAIL goto {name}: {r.get('error')}", flush=True)
                continue
            d = post({"action": "dump", "name": name})
            if d.get("status") != "ok":
                lines.append(f"## {name}\n- DUMP FAILED: {d.get('error')}\n")
                print(f"FAIL dump {name}: {d.get('error')}", flush=True)
                continue
            post({"action": "shot", "name": name})
            path = d["path"]
            data = json.load(open(path))
            lines.append(f"## {name}\n")
            lines.append(f"- **URL:** {data.get('url')} | **Title:** {data.get('title')}\n")
            for h in ("h1", "h2", "h3"):
                if data.get(h):
                    lines.append(f"- {h.upper()}: " + " | ".join(str(x)[:70] for x in data[h][:12]) + "\n")
            nav = [f"{l['text']} -> {l['href']}" for l in data.get("nav_links", []) if l.get("text") and l["text"] not in ("9", "0", "1", "2")]
            if nav:
                lines.append("- **Nav links:**\n" + "\n".join(f"  - {n[:140]}" for n in nav[:40]) + "\n")
            btns = [b["text"] for b in data.get("buttons", []) if b.get("text") and b["text"] not in ("9", "0", "1", "2")]
            if btns:
                lines.append("- **Buttons:** " + " | ".join(btns[:40]) + "\n")
            ins = [f"{i.get('name') or i.get('placeholder') or i.get('text')} ({i.get('type')})" for i in data.get("inputs", []) if (i.get('name') or i.get('placeholder') or i.get('text'))]
            if ins:
                lines.append("- **Inputs:** " + " | ".join(ins[:40]) + "\n")
            if data.get("table_headers"):
                lines.append("- **Table headers:** " + " | ".join(str(x)[:40] for x in data["table_headers"][:40]) + "\n")
            if data.get("badges"):
                lines.append("- **Badges/status:** " + " | ".join(str(b)[:50] for b in data["badges"][:30]) + "\n")
            lines.append("\n")
            print(f"OK {name} -> {data.get('url')}", flush=True)
        except Exception as e:
            lines.append(f"## {name}\n- ERROR: {e!r}\n")
            print(f"ERR {name}: {e!r}", flush=True)

    with open(out_path, "w") as f:
        f.write("".join(lines))
    print(f"DONE wrote {out_path}", flush=True)


if __name__ == "__main__":
    main()
