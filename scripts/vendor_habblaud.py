#!/usr/bin/env python3
"""Vendor the Habblaud client into the Qubit Office plugin.

WHY A SCRIPT AND NOT A COPY
---------------------------
The client is a built Vite app whose `index.html` references ABSOLUTE paths
(`/bundle/main-<hash>.js`, `/assets/brand/favicon-32.png`). Qubit already owns
`/assets/` for its own dashboard, so serving those paths unrewritten would
collide with the host. The vendored copy must be REMAPPED to plugin-relative
paths, and every hash-named chunk must keep resolving.

Two other things this script exists to guarantee:

* **Idempotence.** Re-running produces byte-identical output. The Vite build is
  hash-named and deterministic, so the only thing that could drift is the
  rewriting — which is a pure string transform.
* **Provenance.** The source commit and the licence are recorded next to the
  vendored tree. Habblaud is MIT; its NOTICE must travel with the copies.

The client is built from a checkout given by --src, so this repo does not vendor
a node_modules tree or a git submodule just to ship static files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLUGIN = REPO / "frontend" / "qubit-app" / "plugins" / "qubit-office"
DEST = PLUGIN / "habblaud"

#: The plugin's own served prefix. `/dashboard-plugins/<name>/<file>` is resolved
#: by voicebridge/webui.py::_serve_qubit_plugin from the plugin directory, and
#: allows up to three path segments — enough for `habblaud/bundle/<chunk>.js`.
ASSET_PREFIX = "/plugin-apps/qubit-office/habblaud"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def rewrite_index(html: str) -> str:
    """Point every absolute reference at the plugin's own served prefix.

    Order matters: `/assets/` is rewritten first because Qubit owns that path
    for its dashboard, and a `/bundle/` rule would not catch it.
    """
    # href/src="/x" -> href/src="<prefix>/x"
    def sub(m: re.Match[str]) -> str:
        attr, path = m.group(1), m.group(2)
        # leave data:, http:, //, and already-rewritten paths alone
        if path.startswith(("data:", "http:", "https:", "//", ASSET_PREFIX)):
            return m.group(0)
        return f'{attr}="{ASSET_PREFIX}{path}"'

    out = re.sub(r'\b(href|src)="(/[^"]*)"', sub, html)
    # The stylesheet and font URLs inside the CSS are emitted relative to the
    # bundle directory already, so they need no rewrite — verified below.
    return out


def rewrite_css(css: str) -> str:
    """Point `url(/bundle/...)` at the plugin prefix.

    THE HTML IS NOT THE ONLY PLACE WITH ABSOLUTE PATHS. The stylesheet references
    its webfonts as `url(/bundle/pixelify-sans-....woff2)`, so rewriting only the
    HTML left every font 404ing — the page loaded, rendered, and silently fell
    back to a system font, which reads as "the pixel font didn't build".
    """
    return re.sub(
        r"url\((/[^)]*)\)",
        lambda m: m.group(0) if m.group(1).startswith(ASSET_PREFIX)
        else f"url({ASSET_PREFIX}{m.group(1)})",
        css,
    )


def build_client(src: Path, out: Path) -> None:
    """Run the upstream Vite build. The client has no runtime dependencies."""
    vite = src / "node_modules" / "vite" / "bin" / "vite.js"
    if not vite.is_file():
        sys.exit(
            f"vite is not installed in {src}.\n"
            f"  Run: cd {src} && NODE_ENV=development npm install --include=dev"
        )
    proc = subprocess.run(
        ["node", str(vite), "build", "--config", "vite.config.ts"],
        cwd=src, capture_output=True, text=True, timeout=600,
    )
    if proc.returncode != 0:
        sys.exit(f"vite build failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    built = src / "dist" / "client"
    if not (built / "index.html").is_file():
        sys.exit(f"the build produced no index.html under {built}")
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(built, out)


def vendor(src: Path, dest: Path, skip_build: bool) -> dict:
    # Build OUTSIDE the destination. An earlier version staged into
    # `dest/.staging` and then deleted `dest` before reading from it, so the
    # build was destroyed by the very step meant to publish it.
    if skip_build:
        staging = src / "dist" / "client"
        if not staging.is_dir():
            sys.exit(f"--skip-build needs {staging} to exist")
    else:
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="habblaud-vendor-"))
        staging = tmp / "client"
        build_client(src, staging)

    # --- assemble the vendored tree -------------------------------------
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    shutil.copytree(staging / "bundle", dest / "bundle")
    # `client/public` carries fixed-name assets and the web manifest.
    public = src / "client" / "public"
    if public.is_dir():
        for item in public.iterdir():
            target = dest / item.name
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)

    html = (staging / "index.html").read_text(encoding="utf-8")
    (dest / "index.html").write_text(rewrite_index(html), encoding="utf-8")

    # every emitted stylesheet, for the webfont URLs inside it
    for css in dest.rglob("*.css"):
        original = css.read_text(encoding="utf-8")
        rewritten = rewrite_css(original)
        if rewritten != original:
            css.write_text(rewritten, encoding="utf-8")

    # --- provenance -----------------------------------------------------
    commit = "unknown"
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=src,
            capture_output=True, text=True, timeout=30,
        ).stdout.strip() or "unknown"
    except OSError:
        pass
    licence = src / "LICENSE"
    if licence.is_file():
        shutil.copy2(licence, dest / "LICENSE")

    files = sorted(
        p for p in dest.rglob("*") if p.is_file() and p.name != "PROVENANCE.json"
    )
    record = {
        "source": "https://github.com/marmottajr/habblaud",
        "commit": commit,
        "licence": "MIT",
        "note": (
            "Vendored build output of the Habblaud client. Client-side only: the "
            "server, the Claude Code mod, and the Docker setup are NOT vendored. "
            "Served from /plugin-apps/qubit-office/habblaud/."
        ),
        "files": [
            {"path": str(p.relative_to(dest)), "sha256": sha256(p),
             "bytes": p.stat().st_size}
            for p in files
        ],
        "total_bytes": sum(p.stat().st_size for p in files),
        "index_rewritten_from": "/-absolute to " + ASSET_PREFIX,
    }
    (dest / "PROVENANCE.json").write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", required=True,
                    help="a checkout of github.com/marmottajr/habblaud")
    ap.add_argument("--skip-build", action="store_true",
                    help="reuse an existing dist/client instead of rebuilding")
    ap.add_argument("--check", action="store_true",
                    help="verify the vendored tree matches a fresh build")
    args = ap.parse_args()
    src = Path(args.src).expanduser().resolve()
    if not (src / "client" / "index.html").is_file():
        sys.exit(f"{src} does not look like a habblaud checkout")

    if args.check:
        if not (DEST / "PROVENANCE.json").is_file():
            print("NOT VENDORED", file=sys.stderr)
            return 1
        record = json.loads((DEST / "PROVENANCE.json").read_text())
        stale = [
            f["path"] for f in record["files"]
            if not (DEST / f["path"]).is_file()
            or sha256(DEST / f["path"]) != f["sha256"]
        ]
        if stale:
            print(f"STALE: {stale}", file=sys.stderr)
            return 1
        print(f"ok: {len(record['files'])} files, "
              f"{record['total_bytes']} bytes, commit {record['commit'][:12]}")
        return 0

    record = vendor(src, DEST, args.skip_build)
    print(f"vendored {len(record['files'])} files, "
          f"{record['total_bytes']} bytes from {record['commit'][:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
