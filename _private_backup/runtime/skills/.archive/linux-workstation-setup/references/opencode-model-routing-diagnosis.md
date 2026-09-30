# OpenCode Model Routing Diagnosis

## Symptom

A model works on one provider but fails on another. Example:
`deepseek-v4-flash` is rejected by `opencode-zen` / `opencode-go` while
`ox-alpha-free` and `laguna-s-2.1-free` succeed.

## Root Cause

Each Qubit provider has its own **curated model catalog**. The OpenCode Go
and OpenCode Zen endpoints are flat-namespace resellers with a fixed set of
served models — they do **not** proxy arbitrary aggregator or native-provider
model IDs.

| Provider | Serves DeepSeek V4? | Catalog source |
|---|---|---|
| `opencode-zen` | ❌ No | `models.py` `_PROVIDER_MODELS["opencode-zen"]` |
| `opencode-go` | ❌ No | `_PROVIDER_MODELS["opencode-go"]` |
| `deepseek` | ✅ Yes | `_PROVIDER_MODELS["deepseek"]` |
| `openrouter` | ✅ Yes (as `deepseek/deepseek-v4-flash`) | live `/v1/models` |
| `qubit` | ✅ Yes (as `deepseek/deepseek-v4-flash`) | live catalog |

The OpenCode catalogs (2026-08):
- **Zen**: `gpt-5.*`, `claude-sonnet-4-*`, `gemini-3-*`, `glm-5`,
  `kimi-k2.5`, `minimax-m2.*`, `qwen3-coder`, `big-pickle`
- **Go**: `kimi-k2.6`, `kimi-k2.5`, `glm-5.1`, `glm-5`,
  `mimo-v2.*`, `minimax-m2.*`, `qwen3.*`

## Diagnosis Steps

### 1. Check the credential pool error history

```python
import json, os
with open(os.path.expanduser('$QUBIT_HOME/auth.json')) as f:
    auth = json.load(f)
pool = auth.get('credential_pool', {})
for provider in ['opencode-go', 'opencode-zen', 'deepseek', 'openrouter']:
    creds = pool.get(provider, [])
    for i, c in enumerate(creds):
        print(f'[{provider} #{i}]')
        print(f'  status:      {c.get("last_status")}')
        print(f'  error_code:  {c.get("last_error_code")}')
        print(f'  error_msg:   {c.get("last_error_message")}')
        print(f'  base_url:    {c.get("base_url")}')
        print()
```

Interpret the error codes:
- **401 + "Model … is not supported"** → the model is not in the provider's
  catalog. Switch provider or pick a supported model.
- **402 + "Insufficient Balance"** → the API key is valid but the account
  needs funding. Top up or switch to a different provider/credential.
- **429/529** → rate-limited or overloaded, retry with backoff.

### 2. Check the provider's supported model list

```python
# In the Qubit venv:
from hermes_cli.models import _PROVIDER_MODELS
zen = _PROVIDER_MODELS.get('opencode-zen', [])
go  = _PROVIDER_MODELS.get('opencode-go', [])

target = 'deepseek-v4-flash'
print(f'{target} in opencode-zen: {target in zen}')  # → False
print(f'{target} in opencode-go:  {target in go}')   # → False
print(f'{target} in deepseek:     {target in _PROVIDER_MODELS.get("deepseek", [])}')  # → True
```

### 3. Verify model normalization

```python
from hermes_cli.model_normalize import normalize_model_for_provider

# For OpenCode providers, vendor prefixes are stripped:
print(normalize_model_for_provider('deepseek/deepseek-v4-flash', 'opencode-go'))
# → 'deepseek-v4-flash'  (stripped, but OpenCode still doesn't serve it)

# For aggregators, vendor prefixes are preserved:
print(normalize_model_for_provider('deepseek/deepseek-v4-flash', 'openrouter'))
# → 'deepseek/deepseek-v4-flash'  (preserved, OpenRouter accepts it)
```

## Fix

### Option A: Switch to the `deepseek` provider

```bash
qubit config set model.provider deepseek
qubit config set model.default deepseek-v4-flash
qubit config set model.base_url ""  # clears OpenCode base_url
qubit config set model.api_key ""   # uses DEEPSEEK_API_KEY from .env
```

**Prerequisite**: Ensure `DEEPSEEK_API_KEY` is set in `$QUBIT_HOME/.env` and
the account has sufficient balance.

### Option B: Use OpenRouter with the DeepSeek model

```bash
qubit config set model.provider openrouter
qubit config set model.default deepseek/deepseek-v4-flash
qubit config set model.base_url ""
```

**Prerequisite**: Set `OPENROUTER_API_KEY` in `$QUBIT_HOME/.env`.

### Option C: Use a model the OpenCode endpoint actually serves

```bash
qubit model  # interactive picker — only shows live-supported models
```

## Pitfall: OpenCode base_url mismatch

The `opencode-go` credential in the pool may report `base_url:
https://opencode.ai/zen/v1` (a Zen URL) even though the provider is
`opencode-go`. This happens when credentials were imported from an OpenCode
Zen config and reused for the Go endpoint. The model catalog mismatch
still applies regardless of which sub-endpoint is hit.
