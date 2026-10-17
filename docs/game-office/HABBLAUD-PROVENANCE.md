# Habblaud — vendoring provenance and licence position

**Status: ACCURATE — reproducibility CONFIRMED (A9).** Re-checked 2026-10-16: the
commit (`4ccce2537472`), MIT licence, and the vendored/not-vendored split all
still hold, and the tree is now proven **reproducible bit-for-bit** from the
pinned commit (`scripts/verify_habblaud_revendor.py` → `REPRODUCIBLE`, 77 files
byte-identical; see `docs/VENDOR-REPRODUCIBILITY.md`). Two figures in the ORIGINAL
text were drift-prone: the recorded byte total (current real value from
`scripts/vendor_habblaud.py --check` is **2147631**, and it legitimately changes on
re-vendor) and the serving function name (it is `_serve_plugin_app`, not
`_serve_qubit_plugin`). Assert **exit code + file count**, never the byte total.

Audit date: 2026-10-14. Scope: the vendored Habblaud client under
`frontend/qubit-app/plugins/qubit-office/habblaud/`. This is a provenance
record, not legal advice; genuine uncertainty is flagged in §8.

## 1. What was vendored

The **built client only** — the static output of the upstream Vite build
(`dist/client/`), remapped to plugin-relative paths and served from
`/plugin-apps/qubit-office/habblaud/` by
`voicebridge/webui.py::_serve_qubit_plugin`.

- Local path: `frontend/qubit-app/plugins/qubit-office/habblaud/`
- Files: 78 on disk (77 tracked in `PROVENANCE.json`, which excludes itself;
  plus `PROVENANCE.json`)
- Total: 2,147,057 bytes over the 77 recorded files
- Contents: `bundle/` (minified JS/CSS chunks + woff fonts), `assets/` (PNG art,
  brand marks, illustrations, `manifest.json`), `index.html`,
  `manifest.webmanifest`, `LICENSE`, `PROVENANCE.json`
- Vendoring method: `scripts/vendor_habblaud.py` (runs the upstream build,
  copies `dist/client/bundle` + `client/public`, rewrites paths, writes the
  manifest). No `node_modules` and no git submodule are vendored.

## 2. From where, at which commit, under which licence

| Field | Value |
|---|---|
| Upstream repository | https://github.com/marmottajr/habblaud |
| Pinned commit | `4ccce2537472fe5a8d432f06974494d2e778ff15` (tag/version 0.11.0, 2026-10-13) |
| Source path | `dist/client/` (built Vite output) + `client/public/` |
| Licence | MIT |
| Copyright line | `Copyright (c) 2026 Márcio Junior` |

The upstream `LICENSE` at the pinned commit was read verbatim. It is the
standard MIT License; the second line is exactly `Copyright (c) 2026 Márcio
Junior`. The copy vendored at
`frontend/qubit-app/plugins/qubit-office/habblaud/LICENSE` is byte-identical to
the upstream file (sha256
`5de137c2c07bd58a24cfaa423221fee7ef4a5955b140f4858c8e8169ae1dee9a`).

`PROVENANCE.json` records `source`, `commit`, `licence: "MIT"`, a scope note,
per-file sha256 + byte size, and `total_bytes`. It does **not** currently carry
the copyright holder name; the notice in §7 is the authoritative record.

## 3. What was NOT vendored

Nothing from the upstream repository other than the built client is present.
Confirmed absent from the vendored tree:

| Upstream path | Vendored? | Notes |
|---|---|---|
| `client/` (TypeScript **source**: `client/src/*.ts`, `client/index.html`) | No | Only its **build output** is vendored, not the source tree |
| `client/public/` | Copied (fixed-name public assets) | These are shipped as-is into the vendored root |
| `dist/client/` | Yes | This is what is actually vendored |
| `server/` | No | Upstream Node server (accounts, pty, codex, messages, …) not vendored |
| `mod/` | No | Claude Code mod trees not vendored |
| `scripts/` | No | Upstream build scripts not vendored |
| `shared/` | No | Upstream shared TS not vendored |
| Docker (`Dockerfile`, `docker-compose.yml`, `.dockerignore`) | No | Not vendored |
| Root docs/READMEs/CHANGELOG, `.claude-plugin`, `docs/`, `.github` | No | Not vendored |

The distinction that matters: **the vendored tree is build output, not source.**
`client/src/` (the human-readable TypeScript) is *not* present anywhere in Qubit.
The vendored JS is minified bundles.

## 4. The key legal question — is a build artifact of MIT source distributable, and must the notice travel?

Stated from the licence text itself, not as legal advice:

- MIT grants, to anyone obtaining a copy: the right to **"use, copy, modify,
  merge, publish, distribute, sublicense, and/or sell copies of the Software,
  and to permit persons to whom the Software is furnished to do so"**.
- The condition is: **"The above copyright notice and this permission notice
  shall be included in all copies or substantial portions of the Software."**

The licence says "the Software" — it does not limit this to source form. A
compiled/minified bundle is a copy (indeed a *substantial portion*) of the
Software, so the distribution right applies to it on the same terms and the
condition attaches to it too. **A build artifact of MIT source is distributable
under MIT, and the MIT copyright notice + permission text must travel with it.**

The vendored `LICENSE` file is present in the tree, which satisfies "included
in all copies" for the file at rest. The remaining requirement — the notice
being reachable *in distribution* — is recorded in §7 and should also be added
to the repository-level `THIRD_PARTY_NOTICES.md` (see §8).

## 5. Local changes already made to the vendored output

`scripts/vendor_habblaud.py` makes four kinds of change. HTML, CSS and JS
references are rewritten for the app route, and the specific EventSource call
is redirected to the Qubit stream with an upstream fallback.

1. **`index.html` path rewrite** (`rewrite_index`): every absolute
   `href`/`src`/`content="/…"` is prefixed with the plugin prefix
   `/plugin-apps/qubit-office/habblaud`. Rewritten references:
   `/bundle/main-DND_b4_E.js`, `/assets/brand/favicon-32.png`,
   `/assets/brand/favicon-16.png`, `/assets/brand/apple-touch-icon.png`,
   `/manifest.webmanifest`. `data:` / `http:` / `//` / already-prefixed paths
   are left alone.
2. **`bundle/app-BW61bUik.css` URL rewrite** (`rewrite_css`): every
   `url(/…)` (the `@font-face` `Pixelify Sans` webfont references) is prefixed
   with the same plugin path. No other CSS file differs.
3. **JS runtime paths + stream call** (`rewrite_js`): absolute `/assets/` and
   `/bundle/` literals in each generated JS chunk are prefixed with the app
   route. The upstream `openStream('/api/stream')` call is changed to
   `openStream(window.__QUBIT_OFFICE_STREAM_URL__||'/api/stream')`. Qubit leaves
   that global unset and the upstream fallback is Qubit's same-origin `/api/stream`;
   the server's dedicated route handles the frame's stream request. No token is
   serialized into the stream URL.
4. **Added files** not present in `dist/client/`: `LICENSE` (copied verbatim
   from upstream) and `PROVENANCE.json` (generated manifest). `client/public/*`
   fixed-name assets are copied into the vendored root.

No artwork bytes or upstream UI components were replaced. The generated app JS
has a narrowly scoped stream-call edit in addition to URL relocation; these
modifications are in the pinned MIT-derived build output and recorded by its
per-file hashes in `PROVENANCE.json`.

**Permitted by MIT?** Yes. MIT expressly grants the right to "modify" and
"distribute" copies; path rewriting for relocation is an exercise of that
grant. There is no licence obligation violated by the rewrite — the only
obligation is notice retention, unaffected by these edits.

**Correctly documented?** The generator and this record describe the local
changes; `PROVENANCE.json` records the source,
commit, licence, scope note and `index_rewritten_from`, and the script's
docstring explains the rewrites. But per the AGENTS.md rule ("record the exact
upstream repository, commit, source path, license, and local changes"), the
local changes are described only inside the generator script and a one-line
`index_rewritten_from` field; before this document there was **no standalone
record of the local changes**, none in `THIRD_PARTY_NOTICES.md`, and no
copyright-holder name in `PROVENANCE.json`. This file closes that gap. The
recommended follow-up in §8 would make it durable at repo level.

## 6. Required notice text

MIT requires the copyright notice and the full permission notice to be included
in all copies/substantial portions. Required text to ship with the vendored
client (also add to `THIRD_PARTY_NOTICES.md`):

> Habblaud — client (vendored build output)
> https://github.com/marmottajr/habblaud @ commit 4ccce2537472fe5a8d432f06974494d2e778ff15
>
> MIT License
>
> Copyright (c) 2026 Márcio Junior
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

The in-tree `frontend/qubit-app/plugins/qubit-office/habblaud/LICENSE` already
carries the copyright + permission text verbatim.

## 7. Integrity check

`scripts/vendor_habblaud.py --check` re-hashes every file listed in
`PROVENANCE.json` against the tree. Real output at audit time:

```
$ .venv/bin/python scripts/vendor_habblaud.py --check
ok: 77 files, 2147631 bytes, commit 4ccce2537472
```

Exit 0. The manifest matches the tree: every recorded path exists and its
sha256 is unchanged.

## 8. Uncertainties and what still needs a human decision

1. **The AGENTS.md-mandated licencing documents do not exist at the cited
   paths.** The root `AGENTS.md` ("Licensing and upstream reuse") directs that
   `docs/archive/2026-09-21/LICENSING.md` and
   `docs/archive/2026-09-21/UPSTREAM_REUSE.md` be read *before* copying external
   source and that provenance be "recorded in `docs/UPSTREAM_REUSE.md`". Neither
   file exists anywhere in the repository (the archive dir contains only JSON
   ledgers). Only `THIRD_PARTY_NOTICES.md` (repo root) exists. This audit
   therefore could not follow the mandated procedure as written, and there is
   **no `UPSTREAM_REUSE.md` ledger entry for Habblaud.** This is a genuine
   process gap, flagged rather than guessed at.
2. **Habblaud is not yet in `THIRD_PARTY_NOTICES.md`.** The repository's own
   third-party notice file has no Habblaud record. The §6 notice text should be
   added there; until it is, the licence obligation is satisfied only by the
   in-tree `LICENSE` file.
3. **Namespace of the notice.** Other notices in `THIRD_PARTY_NOTICES.md` use
   "Adapted behavior / Local divergence" wording for source adaptations. Habblaud
   is *verbatim build output with only path rewrites*, a different category —
   the record should say so explicitly so no one mistakes it for a source
   rebrand.
4. **Rebranding scope is a fresh decision.** Nothing here authorises modifying
   the vendored bundle's branding. MIT permits modification, but this repo's
   convention is that rebranded/modified vendored files carry a recorded patch
   entry; the vendored Habblaud tree has no such per-file patch ledger. Any
   rebrand must first (a) add the §6 notice to `THIRD_PARTY_NOTICES.md`, and
   (b) state whether the tree stays a verbatim build or becomes a modified copy.

**Verdict: position is CLEAN for the vendoring as it stands** — MIT permits
copying, modifying and distributing the build output, the upstream `LICENSE`
travels verbatim with the tree, and the only local changes (path rewrites) are
MIT-permitted and now documented here. It is **not** clean as a *process*: the
mandated `UPSTREAM_REUSE.md`/`LICENSING.md` records are absent and Habblaud is
missing from `THIRD_PARTY_NOTICES.md`.

**BLOCKED — LICENSE REVIEW REQUIRED** for any *proposed rebrand of the vendored
bundle*: that is a modification to third-party MIT code, and until the notice is
recorded in `THIRD_PARTY_NOTICES.md` (or an `UPSTREAM_REUSE.md` equivalent) the
AGENTS.md recording rule is unmet. The vendoring itself is not blocked; the
rebrand is.
