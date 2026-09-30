# Switching the primary LLM model (Qubit Gateway + Qubit CLI)

## Goal
Switch the default model used by both the Qubit Gateway (port 18789)
and the Qubit CLI from `opencode-go/laguna-s-2.1-free` to
`opencode-go/ox-alpha-free` (Zen 0x Alpha, 1M context window).

## Why two configs?

The voice bridge routes tasks through two different paths:

1. **Qubit Gateway** (`~/.openclaw/openclaw.json`) — handles full agent tasks,
   tool use, coding, etc. This is the "deep work" path.
2. **Qubit CLI** (`$QUBIT_HOME/config.yaml`) — handles quick local replies,
   fallback tasks, and `/ask Qubit` requests.

Both independently resolve their model, so **both** must be updated.

## Config locations

### 1. Qubit Gateway config
Path: `~/.openclaw/openclaw.json`

The model config lives under `agents.defaults.model`:
```json
"model": {
  "primary": "opencode-go/ox-alpha-free",
  "fallbacks": ["opencode-go/laguna-s-2.1-free", "deepseek/deepseek-v4-flash"]
}
```

Key points:
- `primary` is the model ref: `<provider>/<model-id>`
- `fallbacks` is tried in order if primary fails
- The model must also be in the allowlist at `agents.defaults.models`

### 2. Qubit CLI config
Path: `$QUBIT_HOME/config.yaml`

```yaml
model:
  default: ox-alpha-free
  provider: opencode-go
  base_url: https://openc.ai/zen/v1
  api_key: <redacted>
  api_mode: chat_completions
```

⚠️ **The Qubit config file (`/home/trick/.hermes/config.yaml`) is write-protected.**
Use the CLI instead:
```
qubit config set model.default ox-alpha-free
```

## Steps

### 1. Back up configs
```bash
cp ~/.openclaw/openclaw.json ~/.openclaw/openclaw.json.pre-oxalpha-$(date +%Y%m%d-%H%M%S)
```

### 2. Edit Qubit config
```python
import json
with open('/home/trick/.openclaw/openclaw.json') as f:
    cfg = json.load(f)
cfg['agents']['defaults']['model']['primary'] = 'opencode-go/ox-alpha-free'
cfg['agents']['defaults']['model']['fallbacks'] = [
    'opencode-go/laguna-s-2.1-free',
    'deepseek/deepseek-v4-flash'
]
with open('/home/trick/.openclaw/openclaw.json', 'w') as f:
    json.dump(cfg, f, indent=2)
```

### 3. Edit Qubit config
Either edit `$QUBIT_HOME/config.yaml` directly (requires approval), or:
```bash
qubit config set model.default ox-alpha-free
```

### 4. Restart services
```bash
systemctl --user restart openclaw-gateway.service
systemctl --user restart openclaw-voice-bridge.service
```

### 5. Verify
```bash
curl -s http://127.0.0.1:18789/health | python3 -m json.tool
curl -s http://127.0.0.1:8890/api/status | python3 -m json.tool
```

## Model reference

`ox-alpha-free` on the `opencode-go` provider:
- **Context window**: 1,000,000 tokens
- **Reasoning**: supported (compat: `supportsReasoningEffort: true`)
- **Input types**: text only
- **maxTokens**: 128,000
- **API**: `openai-completions` at `https://openc.ai/zen/go/v1`

The model catalog source:
`srcodes/openclaw/extensions/opencode-go/provider-catalog.ts`

Note: the static catalog in `provider-catalog.ts` does NOT include `ox-alpha-free`
— it is discovered at runtime from the `https://openc.ai/zen/go/v1/models` endpoint.
The runtime config at `~/.openclaw/openclaw.json` may have a stale cached entry
with a different `contextWindow` (e.g. 262144) from an older discovery. The
source-of-truth is the live catalog, not the cached config.

## Pitfalls

- **Both configs must change.** Updating only `~/.openclaw/openclaw.json` leaves
  Qubit CLI on the old model. The voice bridge routes to Qubit for tasks,
  but casual chat goes through Qubit (line: `hermes.enabled: true` in voice.yaml).
- **Qubit config is write-protected.** Don't try to `patch` the YAML file
  directly — use `qubit config set` which handles the approval flow.
- **Gateway restart required.** The gateway reads `~/.openclaw/openclaw.json`
  at startup; a SIGHUP does NOT reload model config.
- **Voice bridge restart recommended.** The bridge auto-discovers the gateway's
  model at startup; restart to ensure it picks up the new model ref.
- **Model must be in the allowlist.** `agents.defaults.models` in
  `openclaw.json` is an allowlist. Adding a new model requires adding it there
  too. If the model isn't listed, `/model` picker won't show it and task routing
  may fail with an unresolvable model error.