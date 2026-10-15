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
import os
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


#: The tuple of locales the client's own resolver can return, taken from its
#: bundled `[{id:`pt-BR`,...}]` language table. The upstream client DEFAULTS to
#: pt-BR; Qubit authors English only (docs/IDENTITY.md), so the resolver is
#: pinned to `en`.
DEFAULT_LOCALE = "en"

#: The client's own localStorage key for the chosen locale (upstream source:
#: `client/src/i18n/index.ts`), visible in the bundle as `` `habblaud:locale` ``.
LOCALE_STORAGE_KEY = "habblaud:locale"


def pin_locale(js: str) -> str:
    """Force the client's runtime locale to English.

    WHY THIS IS NEEDED AT ALL. The client ships four locales (`pt-BR`, `en`,
    `es`, `fr`) and chooses one AT RUNTIME in `client/src/i18n/index.ts`, built
    into `bundle/main-<hash>.js`. Its resolver `s()` tries, in order:

      1. `localStorage["habblaud:locale"]` (an explicit user choice);
      2. every `navigator.languages` entry;
      3. **`return `pt-BR``** — the hard-coded fallback.

    So on any browser whose preferred languages are not `en`/`es`/`fr`/`pt` —
    including a headless renderer with no `Accept-Language`, and every visitor
    outside those four — the client renders Portuguese. That is the observed
    "chrome is already English" red herring: the live page shows English
    because that browser happens to advertise `en`, not because English is the
    default. Qubit must not depend on a visitor's browser language to author
    English, so the fallback is rewritten to `en`.

    THE NARROWEST POSSIBLE EDIT. The minified resolver is one line:

        function s(){let e=globalThis;if(e.window!==void 0){
          try{let t=a(e.localStorage?.getItem(n));if(t)return t}catch{}
          for(let t of e.navigator?.languages??[e.navigator?.language??``]){
            let e=a(t);if(e)return e}
          return`pt-BR`}          <-- the fallback
          ...}

    Only those terminal `return`s are touched: a localStorage choice and a
    browser that genuinely prefers `en`/`es`/`fr` are left exactly as upstream
    wrote them, so this is a change of DEFAULT, not a removal of the language
    switch. The rewrite is anchored on the full two-token sequence
    `` return`pt-BR`} `` that closes each resolver, not a bare `` `pt-BR` ``,
    because the language TABLE also contains the literal `pt-BR` (as a bare
    identifier, `{id:`pt-BR`,name:...}`) and the locale formatter `d()` maps
    back to `` `pt-BR` `` — replacing those would corrupt the table and the
    `Intl` region.

    THERE ARE TWO FALLBACK SITES, not one — a first cut of this function
    anchored on a single occurrence and the guard refused to ship, which is why
    it counts instead of assuming: `o()` is the non-DOM (Node/`Intl`) resolver
    and `s()` is the DOM (`localStorage`/`navigator`) one. The browser path is
    `s()`, but a build-time/mock path reaches `o()`, so BOTH are pinned to keep
    the default consistent wherever the code runs; each must appear exactly
    once or the vendor run fails.
    """
    marker = "return`pt-BR`}"
    pinned = f"return`{DEFAULT_LOCALE}`}}"
    # IDEMPOTENCE. `--retransform` re-runs this over a tree this function
    # already rewrote, so an already-pinned file must be accepted, not treated
    # as "upstream changed". The two counts must account for the whole file:
    # every resolver must be in ONE state — all upstream, or all pinned. A file
    # with one of each is a genuine half-rewrite and is refused.
    n_pt, n_en = js.count(marker), js.count(pinned)
    if n_pt == 0 and n_en == 2:
        return js  # already pinned: nothing to do
    if n_pt != 2:
        # Do NOT silently no-op: an upstream rebuild that reshapes the resolver
        # must fail the vendor run, not ship a Portuguese fallback.
        raise SystemExit(
            f"pin_locale: expected exactly 2 resolution-fallback sites "
            f"({marker!r}: the DOM resolver and the non-DOM one), found "
            f"{n_pt} unpinned and {n_en} pinned — upstream changed; re-derive "
            f"the anchor before vendoring."
        )
    js = js.replace(marker, pinned)

    # The resolver's second door: a browser whose PRIMARY language is `pt`
    # still wins before the fallback, because `a()` maps `pt*` -> `pt-BR` and
    # `s()` returns the first match. Qubit must serve English regardless of
    # the visitor's browser, and the owner ruling is English-only, so the
    # `pt*` arm of the tag normaliser is rewritten to `en`. `es`/`fr` remain
    # selectable (a visitor who explicitly picks them still gets them); only
    # the Portuguese authoring default is removed.
    pt_arm = "if(t===`pt`||t.startsWith(`pt-`))return`pt-BR`;"
    pt_pinned = f"if(t===`pt`||t.startsWith(`pt-`))return`{DEFAULT_LOCALE}`;"
    if pt_arm in js:
        js = js.replace(pt_arm, pt_pinned, 1)
    elif pt_pinned not in js:
        # Neither form present: the tag normaliser was reshaped upstream. Fail
        # rather than silently shipping a `pt`-preferring browser Portuguese.
        raise SystemExit(
            f"pin_locale: expected exactly 1 pt-tag arm ({pt_arm!r}), found "
            f"none and no pinned arm either — upstream changed; re-derive the "
            f"anchor."
        )
    return js


def fix_ticker_icon(js: str) -> str:
    """Stop the feed ticker rendering a literal `undefined` for a missing icon.

    THE BUG. The mobile/compact feed ticker is one template literal:

        J(this.ticker, c ? `${c.agentName}: ${c.activity.icon} ${c.activity.text}` : ``)

    `activity.icon` is a field the UPSTREAM SERVER sends but Qubit's own SSE
    (`voicebridge/office_stream.py::to_feed`) deliberately OMITS — its docstring
    records the reason: the tower carries no icon, and omitting it is more
    honest than inventing an emoji, so the client is meant to fall back to its
    own per-kind glyph. The feed ROW does cope: it writes `icon` into a
    dedicated span (`J(c,t.activity.icon)`), and an empty span simply shows
    nothing. The TICKER does not: it interpolates the value straight into the
    text, so a missing icon becomes the string `"undefined"` and the reader sees
    `ceo: undefined ceo started a turn`. `agentName` is NOT involved — it
    arrives correct — which is why the earlier probe's `ceo: undefined` looked
    like a naming bug and was not.

    THE FIX. Interpolate the icon only when it is actually present, collapsing
    the gap to a single space otherwise:

        `${name}: ${icon ? `${icon} ` : ``}${text}`

    This is a CLIENT-side fix on purpose: the server's omission is a recorded,
    deliberate contract and is owned elsewhere (voicebridge/), which this
    script must not touch. Rendering is where `undefined` becomes visible, so
    rendering is where it is stopped.

    The anchor is the whole minified ternary, matched only once; a rebuild that
    reshapes it fails the vendor run rather than silently reverting.

    IDEMPOTENCE. `--retransform` re-runs over an already-fixed tree, so the
    already-fixed form is accepted as a no-op.
    """
    buggy = "${c.agentName}: ${c.activity.icon} ${c.activity.text}"
    fixed = "${c.agentName}: ${c.activity.icon ? `${c.activity.icon} ` : ``}${c.activity.text}"
    if fixed in js:
        return js  # already fixed
    if js.count(buggy) != 1:
        raise SystemExit(
            f"fix_ticker_icon: expected exactly 1 ticker interpolation site, "
            f"found {js.count(buggy)} — upstream changed; re-derive the anchor."
        )
    return js.replace(buggy, fixed, 1)


def rewrite_manifest(text: str) -> str:
    """Set the web manifest's `lang` to English.

    THE MANIFEST IS A FOURTH SURFACE. `manifest.webmanifest` carries its own
    `"lang": "pt-BR"` and its own Portuguese `"description"`, and it is what a
    mobile "Add to Home Screen" install reads for the app name and language.
    `rewrite_js` only remaps `manifest.json`-style icon paths, so without this
    the installed app still declares Portuguese while the page says English.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise SystemExit("rewrite_manifest: manifest is not valid JSON")
    changed = False
    if data.get("lang") != DEFAULT_LOCALE:
        data["lang"] = DEFAULT_LOCALE
        changed = True
    desc = data.get("description")
    if isinstance(desc, str) and desc in INDEX_META_COPY:
        data["description"] = INDEX_META_COPY[desc]
        changed = True
    if not changed:
        return text
    # `ensure_ascii=False` keeps the file byte-identical in STYLE to the
    # upstream one (raw UTF-8, not \uXXXX escapes); `json.dumps` otherwise
    # escapes every non-ASCII character, which would rewrite bytes the
    # transform did not actually intend to touch.
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


#: The static Portuguese copy the BUILD BAKES INTO `index.html`, and its English
#: replacement. i18n only covers strings the bundle asks for through `t()`;
#: the document's own `<meta name="description">`, the Open Graph / Twitter
#: preview block, and the PWA title are literal text in the HTML template, so
#: the runtime locale never reaches them. These are document-title and
#: link-preview strings, which is why they are translated here rather than in a
#: locale chunk.
INDEX_META_COPY = {
    "Escritório virtual em pixel art que mostra, em tempo real, o que seus "
    "agentes do Claude Code estão fazendo.":
        "Pixel-art virtual office that shows, in real time, what your Claude "
        "Code agents are doing.",
    "O escritório do Habblaud em pixel art, com salas de projeto e agentes "
    "trabalhando.":
        "The Habblaud office in pixel art, with project rooms and agents at "
        "work.",
    # the web manifest's own description (same English render as the page)
    "O escritório dos seus agentes: quem trabalha, quem precisa de você e o "
    "que foi entregue.":
        "Your agents' office: who is working, who needs you, and what was "
        "delivered.",
}

#: Comment nodes in the template that are Portuguese. Left as-is they ship
#: Portuguese to anyone who views source; the ruling is English-only, so they
#: are translated too. `rewrite_index` already owns this file.
INDEX_COMMENTS = {
    "<!-- Prévia ao compartilhar o link (Open Graph / Twitter). -->":
        "<!-- Link preview when shared (Open Graph / Twitter). -->",
    "<!-- App na Tela de Início (celular): tela cheia, ícone e nome. -->":
        "<!-- Home-screen app (mobile): fullscreen, icon and name. -->",
}


def rewrite_index(html: str) -> str:
    """Point every absolute reference at the plugin's own served prefix.

    Order matters: `/assets/` is rewritten first because Qubit owns that path
    for its dashboard, and a `/bundle/` rule would not catch it.

    `content=` IS AN ATTRIBUTE THAT CARRIES PATHS TOO. The build emits
    `<meta property="og:image" content="/assets/og-image.png">` (and the
    twitter:image twin). Those social-preview images are fetched by absolute
    URL, so leaving them un-remapped 404s them against the host exactly like an
    un-remapped `src` — the earlier href/src-only rule missed them. Only
    rooted (`/...`) content values are touched; arbitrary text content (e.g.
    `content="width=device-width"`) is left alone by the leading-slash anchor.
    """
    # href/src/content="/x" -> <attr>="<prefix>/x"
    def sub(m: re.Match[str]) -> str:
        attr, path = m.group(1), m.group(2)
        # leave data:, http:, //, and already-rewritten paths alone
        if path.startswith(("data:", "http:", "https:", "//", ASSET_PREFIX)):
            return m.group(0)
        return f'{attr}="{ASSET_PREFIX}{path}"'

    out = re.sub(r'\b(href|src|content)="(/[^"]*)"', sub, html)
    # The stylesheet and font URLs inside the CSS are emitted relative to the
    # bundle directory already, so they need no rewrite — verified below.

    # --- language: the document is authored English -----------------------
    # `lang="pt-BR"` is hard-coded by the build template and is NOT reachable
    # by the runtime locale: it is in the static HTML, so a screen reader,
    # a translation prompt, and a search engine all still see Portuguese even
    # after `pin_locale` made the UI English. Rewrite the attribute AND the
    # baked-in Portuguese copy (see INDEX_META_COPY / INDEX_COMMENTS) so the
    # document's static half matches its runtime half.
    out = out.replace('<html lang="pt-BR">', f'<html lang="{DEFAULT_LOCALE}">', 1)
    # `og:locale` is a machine-readable hint with its own spelling: the Open
    # Graph spec wants `xx_YY`, not the BCP-47 `xx-YY` used by `lang=`.
    out = out.replace('<meta property="og:locale" content="pt_BR" />',
                      '<meta property="og:locale" content="en_US" />', 1)
    for pt, en in {**INDEX_META_COPY, **INDEX_COMMENTS}.items():
        out = out.replace(pt, en)
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


def rewrite_js(js: str) -> str:
    """Point absolute `/assets/` and `/bundle/` paths inside a JS chunk at the prefix.

    THE THIRD SURFACE. `rewrite_index` handles HTML attributes and `rewrite_css`
    handles `url(...)`, but a built chunk carries its own absolute paths as
    RUNTIME string literals, and those are fetched by the browser after load:

        src:`/assets/brand/logo-mark.png`     -> an <img> src
        fetch(`/assets/manifest.json`)         -> an XHR
        srcset:`/assets/brand/logo-mark@4x.png 4x`

    Rewriting only HTML and CSS left these 404ing against the host (Qubit owns
    `/assets/`), which is why the office frame showed a broken wordmark, a
    missing empty-state illustration, and a failed manifest fetch — while still
    reporting "Connected", so the failure was invisible in a smoke test.

    THREE QUOTE FORMS, because the bundler is free to pick any. Minified output
    uses BACKTICKS (`/assets/x.png`) far more than quotes; an href/src-only or
    quotes-only rule silently misses them. This rewrites the path inside
    `"..."`, `'...'` and `` `...` `` alike. A path that is part of a longer
    identifier (e.g. `foo/assets/`) is left alone: the opening quote must
    immediately precede the slash.
    """
    def sub(m: re.Match[str]) -> str:
        quote, path = m.group(1), m.group(2)
        if path.startswith(ASSET_PREFIX):
            return m.group(0)
        return f"{quote}{ASSET_PREFIX}{path}{quote}"

    rewritten = re.sub(r"""(["'`])(/(?:assets|bundle)/[^"'`]*)["'`]""", sub, js)

    # Vite emits a module-preload helper that resolves `bundle/...` names by
    # prefixing `/`. A leading slash in a JS string is not a reliable same-origin
    # URL when this client is mounted beneath another document path: browser URL
    # parsing may treat it as protocol-relative (`http://plugin-apps/...`). Anchor
    # paths to this app document instead. `document.baseURI` includes the current
    # Habblaud document path (including a host subpath) and resolves `bundle/...`
    # to the directory containing index.html on the current origin.
    if "__vite__mapDeps" in rewritten:
        # Pass 1 handles source/minifier output. Pass 2 handles repeated
        # rewriting and the app chunk, which can have its own preload helper.
        rewritten = re.sub(
            r"=>i\.map\(i=>[^;]*\)",
            lambda _m: "=>i.map(i=>new URL(d[i],location.origin+`/plugin-apps/qubit-office/habblaud/`).href)",
            rewritten,
            count=1,
        )

    # The SSE URL is constructed in `client/src/net/store.ts` and passed to an
    # EventSource factory, so rewrite the exact factory call with any variable
    # name (the production minifier rewrites `this` to a short local).
    # Use the host's endpoint while retaining the upstream route as fallback.
    # Authentication is bound to the iframe app document at the server route;
    # no credential is placed in EventSource's URL.
    return re.sub(
        r"((?:this\.)?openStream\()(['\"`])/api/stream\2(\))",
        lambda m: m.group(1) + "window.__QUBIT_OFFICE_STREAM_URL__||"
                  + m.group(2) + "/api/stream" + m.group(2) + m.group(3),
        rewritten,
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


def transform_tree(dest: Path) -> None:
    """Apply every string transform to a populated vendored tree, in place.

    Split out of ``vendor`` so the SAME transforms can be re-applied to an
    already-vendored tree (``--retransform``) when upstream is not available to
    rebuild from. The transforms are pure and idempotent, which is what makes
    that safe: re-running ``rewrite_js``/``rewrite_css`` on their own output is
    a no-op (they skip paths already carrying the prefix), and ``pin_locale``
    raises rather than silently skipping if its anchor has already been
    replaced — see its docstring for why that guard exists.
    """
    # every emitted stylesheet, for the webfont URLs inside it
    for css in dest.rglob("*.css"):
        original = css.read_text(encoding="utf-8")
        rewritten = rewrite_css(original)
        if rewritten != original:
            css.write_text(rewritten, encoding="utf-8")

    # every emitted JS chunk, for the runtime asset paths inside it (see
    # rewrite_js — these are fetched after load, so HTML+CSS rewriting misses them)
    for js in dest.rglob("*.js"):
        original = js.read_text(encoding="utf-8")
        rewritten = rewrite_js(original)
        # pin_locale runs on the ENTRY chunk only: the locale resolver lives in
        # `main-<hash>.js` (the module that imports the locale chunk and calls
        # `start()`), and `pin_locale` RAISES when its anchor is absent — which
        # is the guard we want on the one file that must carry it.
        if js.name.startswith("main-"):
            rewritten = pin_locale(rewritten)
        # fix_ticker_icon runs on the APP chunk, where the feed UI lives
        # (`app-<hash>.js`); it is the same "anchor absent = upstream changed"
        # guard applied to the one file that must carry the ticker.
        if js.name.startswith("app-"):
            rewritten = fix_ticker_icon(rewritten)
        if rewritten != original:
            js.write_text(rewritten, encoding="utf-8")

    # the web manifest carries icon paths too, plus its own `lang`/description
    for mf in dest.rglob("*.webmanifest"):
        original = mf.read_text(encoding="utf-8")
        rewritten = rewrite_manifest(rewrite_js(original))
        if rewritten != original:
            mf.write_text(rewritten, encoding="utf-8")


def write_record(dest: Path, commit: str) -> dict:
    """Hash the tree and publish ``PROVENANCE.json`` (see the barrier note below).

    A reader either finds no record (and can skip) or finds the complete new
    one: the record is unlinked first and renamed in last, so it never describes
    a half-written tree.
    """
    files = sorted(
        p for p in dest.rglob("*")
        if p.is_file() and p.name != "PROVENANCE.json"
    )
    record = {
        "source": "https://github.com/marmottajr/habblaud",
        "commit": commit,
        "licence": "MIT",
        "note": (
            "Vendored build output of the Habblaud client. Client-side only: the "
            "server, the Claude Code mod, and the Docker setup are NOT vendored. "
            "Served from /plugin-apps/qubit-office/habblaud/. Locale pinned to "
            "en by scripts/vendor_habblaud.py (English-only authoring ruling)."
        ),
        "files": [
            {"path": str(p.relative_to(dest)), "sha256": sha256(p),
             "bytes": p.stat().st_size}
            for p in files
        ],
        "total_bytes": sum(p.stat().st_size for p in files),
        "index_rewritten_from": "/-absolute to " + ASSET_PREFIX,
    }
    # LAST WRITE: this is the moment the new tree becomes visible as a whole.
    # Write to a temp file and rename, because `write_text` truncates then
    # writes: a reader can catch the file mid-write and see TRUNCATED JSON
    # (measured: a concurrent reader hit `Expecting value: line 1 column 1`, i.e.
    # an empty read of a half-written record). `rename(2)` within one directory
    # is atomic, so a reader sees the old record or the complete new one.
    tmp_record = dest / "PROVENANCE.json.tmp"
    tmp_record.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_record, dest / "PROVENANCE.json")
    return record


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
    # PUBLISH ORDER IS THE CONSISTENCY BARRIER. `PROVENANCE.json` is the record a
    # reader checks the tree against, so it must become visible LAST, after every
    # file it describes is in place. The original version deleted `dest` and then
    # wrote the record mid-assembly, so a concurrent reader could hash a
    # half-generated tree against a record from another generation. That is the
    # recorded intermittent failure -- "5 failed on the first run, green on four
    # re-runs, no root cause found" (docs/OPEN_THREADS.md §4.15) -- and it was a
    # CORRECT detection of an inconsistent tree, not a broken test.
    #
    # WHY NOT A DIRECTORY SWAP. The obvious fix -- build a sibling directory and
    # `os.replace` it into place -- is NOT available here: `dest` is a real,
    # git-tracked, NON-EMPTY directory, and `rename(2)` onto a non-empty
    # directory fails. Measured alternatives and their concurrent-reader failure
    # rates: two renames (old aside, new in) left a window with no `dest` at all
    # (5 of 38 reads saw a missing file); emptying `dest` in place before a
    # single rename was worse (49363 of 49391 reads saw no record at all). So the
    # tree stays where it is and the RECORD is the barrier: unlink it, write the
    # files, write the record last. A reader either finds no record (and can skip)
    # or finds the complete new one.
    record_path = dest / "PROVENANCE.json"
    if record_path.is_file():
        record_path.unlink()
    if not dest.exists():
        dest.mkdir(parents=True)

    shutil.copytree(staging / "bundle", dest / "bundle", dirs_exist_ok=True)
    # `client/public` carries fixed-name assets and the web manifest.
    public = src / "client" / "public"
    if public.is_dir():
        for item in public.iterdir():
            target = dest / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)

    html = (staging / "index.html").read_text(encoding="utf-8")
    (dest / "index.html").write_text(rewrite_index(html), encoding="utf-8")
    transform_tree(dest)

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
        # `copy2` truncates the destination before writing, so a concurrent
        # reader can catch a zero-length LICENSE (measured: the same
        # non-atomic-write class as the record in write_record). Copy aside,
        # rename in.
        tmp_licence = dest / "LICENSE.tmp"
        shutil.copy2(licence, tmp_licence)
        os.replace(tmp_licence, dest / "LICENSE")

    return write_record(dest, commit)


def verify_tree() -> int:
    """Verify the vendored tree against its own PROVENANCE.json.

    Deliberately does NOT need ``--src``: the whole point of the record is that
    drift can be detected on a fresh clone or in CI, where no checkout of the
    upstream client exists. Checks both directions:

    * every recorded file is present with the recorded sha256 (a mutation or a
      deletion fails);
    * no file is present that the record does not name (an undeclared artifact
      — the way a stray file ships unnoticed — fails).
    """
    if not (DEST / "PROVENANCE.json").is_file():
        print("NOT VENDORED", file=sys.stderr)
        return 1
    record = json.loads((DEST / "PROVENANCE.json").read_text())

    stale: list[str] = []
    for f in record["files"]:
        target = DEST / f["path"]
        if not target.is_file() or sha256(target) != f["sha256"]:
            stale.append(f["path"])

    declared = {f["path"] for f in record["files"]} | {"PROVENANCE.json"}
    undeclared = sorted(
        str(p.relative_to(DEST))
        for p in DEST.rglob("*")
        if p.is_file() and str(p.relative_to(DEST)) not in declared
    )

    if stale:
        print(f"STALE: {stale}", file=sys.stderr)
    if undeclared:
        print(f"UNDECLARED: {undeclared}", file=sys.stderr)
    if stale or undeclared:
        return 1
    print(f"ok: {len(record['files'])} files, "
          f"{record['total_bytes']} bytes, commit {record['commit'][:12]}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src",
                    help="a checkout of github.com/marmottajr/habblaud "
                         "(required to vendor; not needed for --check)")
    ap.add_argument("--skip-build", action="store_true",
                    help="reuse an existing dist/client instead of rebuilding")
    ap.add_argument("--check", action="store_true",
                    help="verify the vendored tree against PROVENANCE.json "
                         "without a source checkout")
    ap.add_argument("--retransform", action="store_true",
                    help="re-apply the string transforms to the ALREADY "
                         "vendored tree in place and rewrite PROVENANCE.json. "
                         "Needs no --src and does no build: it is how a "
                         "transform-only change (e.g. the locale/lang pin) is "
                         "shipped when no upstream checkout is available.")
    args = ap.parse_args()

    if args.check:
        # Standalone: no --src, no build, no network. This is what a fresh
        # clone / CI runs to detect drift.
        return verify_tree()

    if args.retransform:
        # Re-run the SAME pure transforms over the existing tree. This is safe
        # because they are idempotent (see transform_tree) and because it
        # recomputes the record from the rewritten bytes, so `--check` still
        # passes. It is a MAINTENANCE path, not a substitute for a real vendor:
        # it cannot pick up upstream source changes, only re-apply this repo's
        # own rewrites. It does refuse to run on a tree with no record — that
        # would mean the tree was never vendored and there is nothing to keep
        # consistent.
        if not (DEST / "PROVENANCE.json").is_file():
            sys.exit(f"{DEST} is not a vendored tree (no PROVENANCE.json); "
                     f"run a full vendor with --src first")
        record = json.loads((DEST / "PROVENANCE.json").read_text())
        # index.html is regenerated from the on-disk file (rewrite_index is
        # idempotent, so a second pass changes nothing), then the rest of the
        # tree is transformed the same way `vendor` does.
        index = DEST / "index.html"
        index.write_text(
            rewrite_index(index.read_text(encoding="utf-8")), encoding="utf-8")
        transform_tree(DEST)
        fresh = write_record(DEST, record["commit"])
        print(f"retransformed {len(fresh['files'])} files, "
              f"{fresh['total_bytes']} bytes, commit {fresh['commit'][:12]}")
        return 0

    if not args.src:
        ap.error("--src is required unless --check is given")
    src = Path(args.src).expanduser().resolve()
    if not (src / "client" / "index.html").is_file():
        sys.exit(f"{src} does not look like a habblaud checkout")

    record = vendor(src, DEST, args.skip_build)
    print(f"vendored {len(record['files'])} files, "
          f"{record['total_bytes']} bytes from {record['commit'][:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
