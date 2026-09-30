# Qubit Model/Provider Switching Workflow

## When to use this
- Switching between OpenCode Go, OpenCode Zen, OpenRouter, DeepSeek, Kimi Coding, etc.
- Changing the default model (e.g., `ox-alpha-free` → `laguna-s-2.1-free`)
- Updating an expired or changed API key
- Resolving "model not supported" / 401 errors after model changes

## Quick reference
```bash
# Switch provider and model
qubit config set model.provider opencode-go
qubit config set model.default ox-alpha-free
qubit config set model.base_url https://opencode.ai/zen/v1
qubit config set model.api_key 'sk-...'

# Verify
qubit config show | head -15
grep -A6 "^model:" $QUBIT_HOME/config.yaml

# Apply changes — must reset, NOT just /reload
# /reset in an active session, or exit + restart qubit
```

## Key points

1. **Four config keys to set together:**
   - `model.provider` — the provider name (opencode-go, opencode-zen, openrouter, deepseek, kimi-coding, etc.)
   - `model.default` — the model identifier (e.g., `ox-alpha-free`, `laguna-s-2.1-free`, `deepseek-v4-flash`)
   - `model.base_url` — the API endpoint for the provider
   - `model.api_key` — the API key for the provider

2. **`/reset` is required, not `/reload`.**  The model/provider is resolved at session startup. `/reload` only reloads `.env` variables — it does NOT pick up config.yaml model changes. After changing `model.provider` or `model.default`, the user must `/reset` (or exit and restart) for changes to take effect.

3. **API key via config or env.**  Setting `model.api_key` in `config.yaml` works directly — the `OPENCODE_GO_API_KEY` env var in `.env` is NOT required if the key is in config.yaml. However, for portability, you can also set the env var. Do NOT set both to different values.

4. **`.env` file protection.**  The file `$QUBIT_HOME/.env` is protected: `read_file` refuses to open it, but `terminal` (shell) can read/write it. API keys in `.env` are redacted as `***` in tool output, but the full value is stored. Use `grep -n` to locate specific lines.

5. **Model/provider compatibility.**  Not all models are available on all providers. For example, `ox-alpha-free` and `laguna-s-2.1-free` are served by OpenCode Go/Zen, but `deepseek-v4-flash` is NOT — it will 401 with "Model not supported". Check the provider's model catalog. See `references/opencode-model-routing-diagnosis.md` for the credential-pool diagnosis recipe.

6. **Common base URLs:**
   - OpenCode Zen: `https://opencode.ai/zen/v1`
   - OpenCode Go: `https://opencode.ai/zen/v1` (same endpoint, different key/plan)
   - OpenRouter: `https://openrouter.ai/api/v1`
   - DeepSeek: `https://api.deepseek.com/v1`
   - Kimi Coding: `https://api.kimi.com/coding/v1`

## Pitfalls

- **Don't sed-replace blindly** in `.env` — comment lines contain `KEY=***` patterns that look like real lines. Use line-number-aware edits or Python scripts to avoid matching comment lines. The `scripts/fix_github_token.py` script in this skill directory demonstrates the Python approach.

- **The `model.api_key` in config.yaml is redacted in output.** When you run `qubit config show` or `grep` the config, the key displays as `sk-BIX...A52p`. The full key is still stored and used — the truncation is display-only redaction. Don't re-set the key thinking it was truncated.

- **Setting `model.base_url` for a provider that has a built-in default.** Some providers have their base URL hardcoded. If you set it incorrectly (e.g., Zen URL for Go provider), API calls will fail. When in doubt, omit `model.base_url` and let Qubit use the provider's default.

- **`/config` slash command vs `qubit config`.** The in-session `/config` shows runtime values. `qubit config show` and editing `config.yaml` directly are for persistent changes. They can diverge between sessions.

## Verification

After resetting with the new config:
```bash
qubit config show  # should show the new provider and model
qubit chat -q "What model are you?"  # confirm in a test query
```
