# Qwen3.8-27B → Qubit wiring session (2026-08-16)

Concrete numbers and transcripts from the first local-model wiring pass on this machine.

## Model facts (verified from HF + Ollama registry)

- Official repo: `Qwen/Qwen3.8-27B`, lastModified 2026-08-14, 267,725 downloads, 10,144 likes, license apache-2.0, pipeline_tag `image-text-to-text` (native vision-language: images + video).
- Specs from model card: 27B dense, 64 layers, hidden 5120, vocab 248,320 (padded), hybrid Gated DeltaNet (linear attention) + Gated Attention architecture, MTP trained, context 262,144 native (extensible to 1M), thinking mode on by default with `reasoning_effort` + `preserve_thinking`.
- Ollama library: `qwen3.8:27b` (default = Q4_K_M), plus `27b-q4_K_M`, `27b-q8_0` tags.

## Ollama manifest layer sizes

Default `27b` tag (Q4_K_M):
- projector: 931,146,016 bytes (~931MB) — vision encoder
- model: 16,810,714,464 bytes (~16.8GB)
- license: 11,345; params: 114 (JSON: `{"draft_num_predict":4,"min_p":0,"presence_penalty":0,"repeat_penalty":1,"temperature":1,"top_k":20,"top_p":0.95}`)

`27b-q8_0` tag: model = 29,047,084,384 bytes (~29GB) — too heavy for 8GB VRAM.

Total pull size shown by `ollama list`: 17.74 GB (model + projector).

## The 412 version gate (root cause + fix)

Old Ollama (0.30.7) on `ollama pull qwen3.8:27b`:

```
Error: pull model manifest: 412:
The model you are attempting to pull requires a newer version of Ollama.
Please download the latest version at: https://ollama.com/download
```

Fix: `curl -fsSL https://ollama.com/install.sh | sudo -S sh` → installed v0.32.13 (published 2026-08-14, same day as model release). Existing models (glm-4.7-flash 19GB, deepseek-r1:7b 4.7GB) survived the update. Version check: `curl -s https://api.github.com/repos/ollama/ollama/releases/latest`.

## Storage location gotcha

systemd service runs as user `ollama`; model blobs at `/usr/share/ollama/.ollama/models/blobs`. `~/.ollama` only contains `history/` — `du -sh ~/.ollama` shows 8KB while the download is actively streaming to `/usr/share/ollama`. Confirm with `ls -la /usr/share/ollama/.ollama/models/blobs` (large sha256-* files) or `curl -s http://127.0.0.1:11434/api/tags`.

## Qubit registration (exact entry)

Added to `models.providers.ollama.models[]` (6 existing entries + 1 new = 7):

```json5
{
  "id": "qwen3.8:27b",
  "name": "qwen3.8:27b",
  "reasoning": true,
  "input": ["text", "image"],
  "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
  "contextWindow": 262144,
  "maxTokens": 8192,
  "compat": {"supportsTools": true, "supportsUsageInStreaming": true},
  "params": {"num_ctx": 65536}
}
```

Applied via `openclaw config patch --file /tmp/openclaw_qwen38.patch.json5` (dry-run first, then apply). Result: "Applied 1 config update(s). Change will apply without restarting the gateway." `openclaw models list` shows `ollama/qwen3.8:27b  text+image  262k  yes  yes`.

Set default: `openclaw models set ollama/qwen3.8:27b` → "Default model: ollama/qwen3.8:27b" (backup at openclaw.json.bak).

## Verification results

- Direct: `ollama run qwen3.8:27b "Reply with exactly: MODEL_ALIVE"` → `MODEL_ALIVE`
- Gateway: `openclaw agent --agent main --message "Say exactly: OPENCLAW_ON_QWEN38"` → `OPENCLAW_ON_QWEN38`
- Perf on RTX 3070 Ti 8GB / 62GB RAM, Q4_K_M: prompt eval 7.91 tok/s, eval rate 6.95 tok/s, GPU mem 6346/8192 MiB, ~27% util. CPU-bound — fine for chat, slow vs cloud models.
