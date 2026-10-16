#!/usr/bin/env python3
"""Prove the doubled-URL defect: the vendor preload-helper rewrite is broken.

The upstream (Vite) helper is:
    (d,m,f)=>i.map(i=>new URL(d[i], import.meta.url).href)
where `d` holds RELATIVE names (e.g. "bundle/app-XXXX.js") that resolve correctly
against the importing module's own URL.

The transform replaces the resolver with:
    (d,m,f)=>i.map(i=>new URL(d[i], location.origin+`/plugin-apps/qubit-office/habblaud/`).href)

`location.origin` has NO trailing slash, so the base becomes
`http://127.0.0.1:8890/plugin-apps/qubit-office/habblaud/` ONLY by accident of the
literal ending in `/` -- but the real break is that `d[i]` is later handed to
`new URL(import.meta.resolve(e))`-style absolute resolution first.
"""
import subprocess, sys, re, json, pathlib
sys.path.insert(0, "/tmp/wt-office-accept/scripts")
import importlib.util
spec = importlib.util.spec_from_file_location("vh", "/tmp/wt-office-accept/scripts/vendor_habblaud.py")
vh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vh)

# The exact upstream resolver shape as it appears in the served bundle.
UPSTREAM = (
    "const __vite__mapDeps=(i,m=__vite__mapDeps,d=(m.f||(m.f=[\"bundle/app-U4Pwh2XA.js\"])))=>"
    "i.map(i=>new URL(d[i],import.meta.url).href);"
)
out = vh.rewrite_js(UPSTREAM)
print("REWRITTEN:", out)

# What the browser actually requests, given location.origin and the chunk's own URL.
chunk_url = "http://127.0.0.1:8890/plugin-apps/qubit-office/habblaud/bundle/main-DND_b4_E.js"
import urllib.parse
origin = "http://127.0.0.1:8890"
base = origin + "/plugin-apps/qubit-office/habblaud/"

def resolve(target, base):
    return urllib.parse.urljoin(base, target)

# 1) A RELATIVE name (what the upstream helper is handed under Vite's own build):
print("\n[relative name]", resolve("bundle/app-U4Pwh2XA.js", base), "-> OK")

# 2) An ABSOLUTE name (what the rewritten resolver is handed, because the client's
#    own loader already turned the relative name absolute via import.meta.resolve):
doubled = resolve("http://127.0.0.1:8890/plugin-apps/qubit-office/habblaud/bundle/app-U4Pwh2XA.js", base)
print("[absolute name]", doubled, "-> DOUBLED" if doubled.count("127.0.0.1:8890") > 1 else "-> ok")

# 3) The served page really contains the bad base:
served = subprocess.run(["curl", "-s", "-m", "10",
    "http://127.0.0.1:8890/plugin-apps/qubit-office/habblaud/bundle/main-DND_b4_E.js"],
    capture_output=True, text=True).stdout
m = re.search(r"i\.map\(i=>new URL\([^)]*\)\.href\)", served)
print("\nSERVED HELPER:", m.group(0) if m else "(not found)")
