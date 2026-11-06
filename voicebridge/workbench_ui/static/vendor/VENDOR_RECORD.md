# Vendored third-party assets

Every file in this tree that is not hermes-webui source, copied rather than
CDN-loaded. Qubit's CSP is `connect-src 'self'` / `script-src 'self'`, so a
CDN `<script>` is blocked outright — each of these replaced a
`cdn.jsdelivr.net` tag in the upstream `index.html`.

| Path | Package | Version | License | sha256 |
|---|---|---|---|---|
| `vendor/katex/0.16.22/*` | katex | 0.16.22 | MIT | (shipped by upstream at this pin) |
| `vendor/smd.min.js` | streaming-markdown | 0.2.15 | MIT | `20c0a39e6ec48b99e2f505f8488d2342fb3000d03523a7ced6cd94c14b15e8c5` |
| `vendor/js-yaml/4.1.0/js-yaml.min.js` | js-yaml | 4.1.0 | MIT | (shipped by upstream at this pin) |
| `vendor/xterm/xterm.js` | @xterm/xterm | 6.0.0 | MIT | `14903579ff54664cd72f8e8699e6961a6272c21863ec1c3b118cdc8af5d4a972` |
| `vendor/xterm/xterm.css` | @xterm/xterm | 6.0.0 | MIT | `854a7c0fb70e8b1a083c16797ab827299fb18744f5ad34f227b48337e33293c6` |
| `vendor/xterm-addon-fit/addon-fit.js` | @xterm/addon-fit | 0.11.0 | MIT | `ba3ea256ce0620a0992a197d6c9baea64823fc93d8da07a9e366ca9943c18527` |
| `vendor/xterm-addon-web-links/addon-web-links.js` | @xterm/addon-web-links | 0.12.0 | MIT | `b74864125ef3889753b94e61eb4dabb99b2a27f920f0c9a7bd53cbe6d10f032a` |

`xterm`, `addon-fit` and `addon-web-links` were copied from the repo's own
npm cache (`frontend/qubit-web/node_modules/@xterm/*`), not downloaded. The
classic UMD builds are used because `terminal.js` only reads
`window.Terminal` and `window.FitAddon.FitAddon` — the ESM `.mjs` builds
would need import maps the upstream page does not use.

**Version drift from upstream is deliberate and recorded:** upstream's
`index.html` pinned `xterm@5.3.0`, `xterm-addon-fit@0.8.0`,
`xterm-addon-web-links@0.9.0`. The local cache has 6.0.0 / 0.11.0 / 0.12.0.
`terminal.js` uses only `new Terminal({...})`, `term.open()`,
`term.write()`, `term.onData()` and `FitAddon.FitAddon()` — all present and
unchanged across those majors. Recorded here so a future reader knows the
delta is not an accident.
