# Firefox parity — the Habblaud office page in Firefox

Measured 2026-10-15. HEAD `1f1931af5`. Every result below is from a real run.

## Verdict: PASS

The page **works in Firefox**. A previous attempt reported this UNKNOWN because it
tried to launch `/snap/bin/firefox`, which is a **shell wrapper script, not a
binary** — geckodriver rejects it with `binary is not a Firefox executable`.

**The fix is to point geckodriver at the real ELF binary:**

```
/snap/firefox/current/usr/lib/firefox/firefox     # 635512 bytes, ELF 64-bit LSB pie executable
```

and to give the snap a profile path it can actually see. The snap's `/tmp` is a
**private tmpfs**, so a host `/tmp` profile is invisible to it; use
`~/snap/firefox/common/<name>` instead. `/snap/bin/geckodriver` works as-is.

## Evidence

Firefox 157.0.1 (snap, `firefox/current`, rev 9036). Driven via geckodriver
WebDriver, headless, dedicated profile, `-no-remote` so the user's running Firefox
is never touched (verified still running afterwards, untouched).

| Check | Command | Observed | Verdict |
|---|---|---|---|
| Page reaches ready | `POST /session` + navigate `http://127.0.0.1:8890/qubit-office?token=…` | `data-state: "ready"` | PASS |
| The Habblaud app mounts | `document.querySelectorAll('iframe')` | 1 iframe, src `/plugin-apps/qubit-office/habblaud/` | PASS |
| The canvas paints | `getImageData` over the largest canvas, threshold 30/channel | **651608 / 652525 = 99.86%** non-background | PASS |
| Canvas size | `canvas.width/height` | 1075×607 (viewport-limited) | PASS |
| English locale | `iframe.contentDocument.documentElement.lang` | `"en"` | PASS |
| Real data flowing | iframe `body.innerText` | `Habblaud Connected` + rooms `boardroom`/`desk-a`/`desk-b`, agents `lead-quality`, `ceo`, `lead-content` | PASS |
| SSE route reachable | `curl -m5 "…/api/stream?token=…"` | `200 text/event-stream; charset=utf-8` | PASS |
| App console errors | Firefox console during load | **none from the app** | PASS |

## Console noise — Firefox's own, not the app's

The Firefox log carried these, none of them from Qubit or Habblaud:

```
[GFX1-]: RenderCompositorSWGL failed mapping default framebuffer, no dt
console.error: "Experiment <name> has unknown featureId: <id>"   (×4, telemetry/Nimbus)
JavaScript error: resource://gre/modules/PrivateBrowsingUtils.sys.mjs, line 50:
  TypeError: can't access property "QueryInterface", aWindow.docShell is null
```

The `GFX1` line is the expected software-rendering fallback in headless; the
Nimbus and PrivateBrowsingUtils lines are stock Firefox internals. **Zero
application errors.**

## Reproduce

```sh
# geckodriver must exist (/snap/bin/geckodriver)
FF=/snap/firefox/current/usr/lib/firefox/firefox
PROF=~/snap/firefox/common/ffprobe        # NOT /tmp — the snap's /tmp is a private tmpfs
/snap/bin/geckodriver --port 4444 &
# then POST /session with:
#   browserName=firefox,
#   moz:firefoxOptions.binary=/snap/firefox/current/usr/lib/firefox/firefox,
#   moz:firefoxOptions.args=["-headless","-profile",PROF,"-no-remote"]
# navigate to http://127.0.0.1:8890/qubit-office?token=<webui.token>
```

## What this does NOT prove

- It is **headless** Firefox. Real-window GPU rendering was not exercised (the
  `GFX1` fallback line is a headless artifact).
- The **mobile/phone** viewport was not re-tested in Firefox; the Chrome runs
  covered 390×844 and 360×800.
- Firefox's own `EventSource` delivery was inferred from the rendered iframe text
  plus the server-side `200 text/event-stream`; the WebDriver session did not
  capture per-request network frames.
