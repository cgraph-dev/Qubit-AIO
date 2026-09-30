---
name: social-inbox-check
description: "Read latest messages from the user's logged-in social accounts (Gmail, Facebook Messenger, Instagram DMs) via deterministic CDP scripts against the Qubit Chromium profile — zero LLM tokens, works with a small local model."
version: 1.0.0
author: qubit
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [gmail, messenger, instagram, social, messages, cdp, browser, zero-token]
---

# Social Inbox Check (zero-token)

Read the user's latest messages from Gmail, Messenger, and Instagram using
deterministic DOM extraction over Chrome DevTools Protocol (CDP). No LLM
involved in reading — the scripts extract JSON directly, so even a tiny
local model only needs to summarize 3 short lines.

## When to use

- User asks "check my email / whats new on messenger / latest instagram messages"
- Any "latest messages from last N people" request
- Daily inbox sweep

## Requirements

- Qubit Chromium profile logged into the accounts (once):
  - Profile: `/home/trick/snap/chromium/common/chromium` (snap Chromium
    redirects `--user-data-dir` there)
  - The browser MUST be running with remote debugging:
    ```
    DISPLAY=:0 /snap/bin/chromium --user-data-dir=/home/trick/snap/chromium/common/chromium \
      --profile-directory=Default --remote-debugging-port=9222 --remote-allow-origins=*
    ```
  - Verdict check: `curl -s http://127.0.0.1:9222/json` lists tabs; logged-in
    cookies live in `Default/Cookies` (Gmail: SID/__Secure-1PSID;
    Facebook: c_user/xs; Instagram: sessionid/ds_user_id)
- Python venv with `websocket-client`:
  `/home/trick/Projects/Repos/openclaw-voice-bridge/.venv/bin/python` (has it)

## Scripts (in scripts/)

- `read_gmail.py [N]` — top N inbox rows: sender, subject, snippet, time
- `read_messenger.py [N]` — top N chats: name, preview, time, href
- `read_instagram.py [N]` — top N DMs: name, preview, time, unread flag
- `browser_ensure.py` — (helper) start Chromium with CDP if not running

All target the tab URL fragment (`gmail`/`messenger`/`instagram`), navigate to
the list URL, wait for render, extract via JS selectors, print JSON.

## Voice bridge integration

`task-templates.yaml` should route:
- "check my email" / "latest email" → agent task that runs
  `read_gmail.py 3` and speaks a 2-line summary
- "check (my )?(messenger|facebook) (messages|chats)" →
  `read_messenger.py 3`
- "check (my )?instagram (messages|dms|direct)" → `read_instagram.py 3`

Because scripts return JSON, the speaking prompt is tiny: "Summarize in
2 sentences who messaged and when."

## Pitfalls

- Snap Chromium redirects `--user-data-dir`: pass the SNAP path
  (`~/snap/chromium/common/chromium`), not `$QUBIT_HOME/browser_profiles/*`
  — those dirs stay empty; the real profile is under `~/snap/...`.
- Chrome 150+ rejects CDP websockets unless launched with
  `--remote-allow-origins=*`.
- Only ONE Chromium may use a profile dir; kill the old instance before
  relaunching with different flags (`pkill -f 'remote-debugging-port'`).
- Messenger may auto-redirect into an e2ee thread; the extractor navigates
  to `/` first, then reads `a[href*="/t/"]` links (skips marketplace/
  requests/archived).
- Instagram unread badge is in the row text ("Unread" line) and the
  tab title count ("(5) Instagram • Messages").
- Facebook/IG/WhatsApp web can show a "login verification" once per
  session — user clicks it once; afterwards cookies persist.
- If a tab is missing, create it: `curl -X PUT
  "http://127.0.0.1:9222/json/new?https://<url>"`

## Browser launch one-liner

```bash
DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000 /snap/bin/chromium \
  --user-data-dir=/home/trick/snap/chromium/common/chromium \
  --profile-directory=Default --remote-debugging-port=9222 \
  --remote-allow-origins=* --new-window "https://www.messenger.com/"
```
