# Browser CDP access to logged-in accounts (Gmail, socials) + Grok-bot research

Verified 2026-08-21 on this box. How to read a user's Gmail (or any logged-in
web account) with ZERO API tokens, plus what "Grok bot / its own computer"
actually means and how it compares to a local agent.

## The technique: drive the logged-in Qubit browser profile over CDP

Qubit keeps persistent Chromium profiles at `$QUBIT_HOME/browser_profiles/<name>/`.
If Chromium is the **snap build** (`/snap/bin/chromium`), snap REDIRECTS
`--user-data-dir` to `~/snap/chromium/common/chromium/` — the real
`Cookies`/`Login Data`/`History` live there, NOT in the dir you passed.
Always check `~/snap/chromium/common/chromium/Default/` for cookie/DB files.

### Launch with remote debugging (Chrome 150+)

```bash
DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000 /snap/bin/chromium \
  --user-data-dir=$HOME/snap/chromium/common/chromium \
  --profile-directory=Default \
  --remote-debugging-port=9222 \
  --remote-allow-origins=* \
  --new-window "https://mail.google.com"
```

- `--remote-allow-origins=*` is MANDATORY on Chrome 150+: without it the CDP
  WebSocket handshake 403s with "Rejected an incoming WebSocket connection
  from the http://127.0.0.1:9222 origin."
- Use `terminal(background=true)` for the browser process (it's long-lived).
- `--user-data-dir` must point at the SNAP path so the login cookies load.
- After launch: `curl -s http://127.0.0.1:9222/json` lists tabs; pick the
  page tab whose URL contains `mail.google.com`, grab `webSocketDebuggerUrl`.

### Drive it with websocket-client (no Playwright needed)

Install: `.venv/bin/python -m pip install websocket-client`

```python
import json, time, urllib.request, websocket
tabs = json.load(urllib.request.urlopen("http://127.0.0.1:9222/json"))
ws_url = next(t["webSocketDebuggerUrl"] for t in tabs
              if t.get("type") == "page" and "mail.google.com" in t.get("url",""))
ws = websocket.create_connection(ws_url, timeout=30)
_id = 0
def cmd(method, params=None):
    global _id; _id += 1
    ws.send(json.dumps({"id": _id, "method": method, "params": params or {}}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == _id: return msg.get("result", {})
def js(expr):
    return cmd("Runtime.evaluate", {"expression": expr, "returnByValue": True}) \
             .get("result", {}).get("value")
```

### Gmail inbox DOM selectors (standard web Gmail)

```js
document.querySelectorAll('tr.zA')          // inbox rows, newest first
row.querySelector('.yW span')?.textContent  // sender
row.querySelector('.bog')?.textContent      // subject
row.querySelector('.y2')?.textContent       // snippet
row.querySelector('.xT .xW')?.getAttribute('title') // full timestamp
```

Wait for `document.querySelectorAll('tr.zA').length > 0` before reading
(poll 1s up to ~20s for the inbox to render).

### Verifying the session before driving

- Cookies present = logged in: SID, SSID, SAPISID, APISID, `__Secure-1PSID`,
  `__Secure-3PSID` on `.google.com` / `accounts.google.com`.
- Chrome's `Login Data` sqlite may be locked while Chromium runs — the
  Cookies DB read works fine.
- `accounts.google.com` iframe `RotateCookiesPage` may appear on fresh login
  — normal, not a blocker.

## Why this beats API tokens for THIS user

- 4 Gmail accounts in the main Chrome Default profile (cgraphdev, lucusor222,
  gosuow, lucusor111) + valutrank seen in security alerts.
- No Gmail API token, no IMAP app passwords, no himalaya config existed —
  browser session cookies were the only working path, and they worked
  instantly.
- Same approach works for WhatsApp Web, Messenger, Instagram web, etc. —
  any site the user logs into in that profile.

## Pitfall: `qubit chat -q -t browser` (CLI agent) is NOT reliable for this

The CLI agent with the browser toolset spent its turns exploring skills
(`skill_view browser-playwright`) and running `ls` instead of navigating.
For a deterministic read, drive the CDP script directly. Use the Qubit CLI
agent for open-ended tasks, the CDP script for "read the inbox / report the
3 newest emails".

## Grok bot ("it has its own computer") — Perplexity sonar-pro research, Aug 2026

- Grok Bot = always-on agents with their OWN cloud computer (persistent VM):
  sign into SaaS tools, run scheduled/unattended workflows, keep working when
  your laptop is off. Beta since 2026-08-11, macOS/iOS clients, high tiers.
- All bots on one account SHARE the cloud computer (cookies, files, terminal
  creds) — one bot's login is reusable by another. Takeover mode lets the
  human remote-control the cloud browser to enter passwords/2FA/captchas,
  then hand control back.
- What it CANNOT do (docs, not marketing): no captcha-breaking (hard stop →
  asks the human), no arbitrary login to random sites (connector-based auth
  only), no fully-autonomous shopping.
- Comparison: Grok's power = always-on cloud + shared state + unattended
  scheduling. A LOCAL agent (Qubit/Qubit on the user's machine) = real
  browser profiles, real cookies, real files, zero cloud mediation — better
  for personal-account work; lacks only the "runs while the PC is off" part.
- User's stance: "it has its own computer but we are a computer as well" —
  the local stack already matches Grok's browser/account abilities; the
  missing pieces are scheduled-unattended runs (already have cron) and the
  one-time browser-profile pairing steps.

## Browser engine facts (this box)

- Qubit browser engine accepts `auto | chromium | firefox | camofox`
  (`qubit config set browser.engine <engine>`). Firefox installed at
  `/usr/bin/firefox`. Logins are PER-PROFILE — each engine needs its own
  login pass.
- Qubit browser plugin (Chrome/Chromium ONLY — no Firefox): profiles
  `openclaw` (isolated CDP 18800), `user` (chrome-mcp attach to running
  Chrome), `chrome` (extension relay 18799). The `chrome` extension needs
  one-time pairing: `openclaw browser extension pair` → load unpacked from
  `<npm>/openclaw/dist/extensions/browser/chrome-extension` →
  chrome://extensions → Developer mode → Load unpacked → paste pairing string.
  The `user` (chrome-mcp) profile needs Chrome started with remote debugging
  enabled + attach approval.
