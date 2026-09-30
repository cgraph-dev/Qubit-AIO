# TUI Node.js Version Debug

Full reproduction recipe for the "Invalid flags supplied to RegExp constructor 'v'" crash.

## Error Signature

```
hermes-tui lifecycle uncaughtException: SyntaxError: Invalid flags supplied to RegExp constructor 'v'
    at new RegExp (<anonymous>)
    at ../node_modules/string-width/index.js (dist/entry.js:65275:29)
```

The `string-width` npm package (transitive dep of `ink`) uses ES2024's `v` RegExp flag
(unicodeSets mode). This requires Node.js 20+.

## Diagnosis (confirm the problem)

```bash
# Find ALL node binaries on the system
which -a node

# Expected output when two Node versions exist:
#   /home/user/.local/share/mise/installs/node/22/bin/node
#   /usr/bin/node

# Check which one the shell-resolved `node` points to:
node --version
# If output is "v18.x.x", that's the problem.

# Prove v18 fails:
/usr/bin/node -e "new RegExp('\\\\p{RGI_Emoji}', 'v')"
# → SyntaxError: Invalid flags supplied to RegExp constructor 'v'

# Prove v22 works:
# (adjust path to your Node 20+ binary)
~/.local/share/mise/installs/node/22/bin/node -e "new RegExp('\\\\p{RGI_Emoji}', 'v')"
# → /^\\p{RGI_Emoji}$/v
```

## Fix

```bash
# Pin Qubit TUI to the correct Node (add to ~/.zshrc / ~/.bashrc)
export HERMES_NODE="$HOME/.local/share/mise/installs/node/22/bin/node"
```

The `HERMES_NODE` env var is checked first in `_node_bin()` — it bypasses PATH lookup
entirely, so it works regardless of how the TUI is launched (terminal, desktop, systemd).

## How the TUI launch works

1. `_make_tui_argv()` calls `_node_bin("node")`
2. `_node_bin()` checks `$HERMES_NODE` first (skips PATH if set and valid)
3. Falls through to `shutil.which("node")` which follows `$PATH`
4. Runs: `node --expose-gc dist/entry.js`

Source: `hermes_cli/main.py`, function `_make_tui_argv` (around line 1643).
