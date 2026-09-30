# Voice bridge — 9 new local PC actions (2026-08-22)

Added to `voicebridge/local/actions.py` (now 19 actions total). All are **offline, zero-token, no gateway** — they run in the bridge process via `pactl`/`subprocess`/`find`/etc. and are routed by `intent.classifier` as `kind: local` before any `conversation`/`coding` check.

| Name | Trigger example | Backend |
|------|----------------|---------|
| `brightness_up/down` | "brightness up" / "dim the screen" | `brightnessctl` → `light` → `xrandr --brightness` fallback |
| `system_stats` | "system stats" / "battery level" | `psutil` CPU/RAM/disk/battery or `os.getloadavg` fallback |
| `quick_note` | "note that buy milk tomorrow" | appends `~/.config/openclaw-voice-bridge/quick-notes.md` |
| `timer` | "set a timer for 10 minutes" | `threading.Timer` + `notify-send` + alarm sound |
| `window_close` | "close this window" | `xdotool key Alt+F4` |
| `find_file` | "find file named report" | `find ~ -maxdepth 4 -iname` |
| `empty_trash` | "empty the trash" | clears `~/.local/share/Trash/files` |
| `bluetooth_toggle` | "toggle bluetooth" | `bluetoothctl power on/off` → `rfkill` |
| `lock` (existing) | "lock the screen" | `loginctl lock-session` |

## Params wiring (`match()`)

New actions extract params in `match()`:
- `quick_note`: `note` from `r"(?:note (?:that|this)|remember (?:that|this)|write down|jot down|save (?:a )?note|remind me (?:to |that )?)(.*)"` → `params["note"]`.
- `timer`: `minutes` from `r"(\d+)\s*(minute|minutes|min|mins|second|seconds)"` with seconds→minutes ceiling; default `5`.
- `find_file`: `query` from `r"(?:find|locate|search for|where is) (?:a |the )?file\s*(?:named |called )?(.+)"`.

All other new actions take no params.

## Pitfall
`local/actions.py` pattern order matters: `local` is checked **before** `coding`/`conversation` in `classifier.py`. A local pattern that is too broad will steal tasks that should go to Qubit. Keep triggers specific (use `\b…\b`).
