# Qubit Browser Extension — how it is wired here

Verified live on this machine, 2026-11-07. Every value below was read from the
running gateway, not from a plan.

## The thing that was actually broken

The extension, the plugin and the 27 tools were all present and correct. What
was missing was the **transport**: the `api_server` platform was never
connected, so the plugin's eight HTTP routes were never mounted. A browser
extension with nothing to talk to reports "can't connect" no matter how healthy
the plugin looks.

## Two home directories — the trap that cost an hour

This machine runs two Qubit homes:

| Home | What uses it |
|---|---|
| `~/.qubit` | interactive CLI, the `qubit` command you type |
| `/home/trick/.local/state/qubit-voicebridge/runtime` | **the gateway daemon** (see `QUBIT_HOME` in `systemctl --user show qubit-gateway.service`) |

Config edits must go to the **gateway's** home or the daemon never sees them.
`qubit config set` here is the *voice* config command, not the agent's; use
`qubit_cli.config.atomic_config_write(path, data)` for the agent config.

## Live wiring

```
api_server platform : connected
listen              : http://127.0.0.1:8642   (NOT 9120 — read the gateway log)
model id            : qubit-agent
plugin              : browser-companion — 27 tools, 3 hooks
plugin routes       : /api/plugins/browser-companion/{status,profiles,...}
CORS                : API_SERVER_CORS_ORIGINS=* (loopback listener, key-auth still required)
```

Secrets live in the gateway home's `.env` (mode 0600): `API_SERVER_KEY`,
`API_SERVER_CORS_ORIGINS`.

## Loading the extension in Chromium

Source folder (already ported and rebranded, 474 files):

```
/home/trick/Projects/Repos/Qubit/frontend/qubit-app/plugins/qubit-browser-ext/extension
```

1. `chrome://extensions` → enable **Developer mode**.
2. **Load unpacked** → in the dialog press `Ctrl+L` and paste the path above.
   (The picker opens in *Recent* search mode by default and shows an empty
   list there — that is the search box, not your folder.)
3. Open the extension popup and point it at `http://127.0.0.1:8642`.
4. Paste the value of `API_SERVER_KEY` from the gateway `.env`.

A manifest `key` is deliberately absent, so the extension ID is assigned at
load time — that is why CORS is a wildcard rather than a pinned origin.

## Verifying without a browser

```bash
GH=/home/trick/.local/state/qubit-voicebridge/runtime
KEY=$(grep '^API_SERVER_KEY=' $GH/.env | cut -d= -f2)
curl -s http://127.0.0.1:8642/v1/models -H "Authorization: Bearer $KEY"
curl -s http://127.0.0.1:8642/api/plugins/browser-companion/status -H "Authorization: Bearer $KEY"
```

Both must answer 200 before blaming the extension.

## Restarting after a plugin change

```bash
qubit gateway restart --confirm
```

Plugin routes mount in the adapter's `connect()`, so a plugin enabled *after*
the gateway started registers its tools and hooks but does **not** get its
routes until the gateway restarts.
