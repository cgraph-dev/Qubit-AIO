# 2026-08-18 qwen3-VL ollama import saga

Machine: Ryzen 7 5700X, 62GB RAM, RTX 3070 Ti 8GB, ollama as systemd service (user `ollama`),
models at `/usr/share/ollama/.ollama/models`. Two models imported this session:
`qwen3:30b-a3b` (Huihui-Qwen3-VL-30B-A3B-Instruct-abliterated Q3_K_M + mmproj-Q8_0, 14.7G)
and `qwen3-vl:8b` (Huihui-Qwen3-VL-8B-Instruct-abliterated Q4_K_M + mmproj-F16, 4.7G).

## Symptom chain (what went wrong)

1. `ollama create` with a Modelfile containing `RENDERER qwen3-vl` SUCCEEDED
   ("writing manifest success", `ollama list` shows the model) but the first
   `ollama run`/`/api/generate` fails: `Error: 500 Internal Server Error: unknown renderer "qwen3-vl"`.
   The llama-server itself loads the GGUF fine (`srv llama_server: model loaded`) — the
   500 comes from ollama's server-side template selection, BEFORE generation.
2. ollama 0.32.13's renderer registry lacks `qwen3-vl` (strings in the binary contain
   the name but the template registration doesn't exist). `ollama pull qwen3-vl:8b`
   (official) would hit the same wall — it's a version gap, not a GGUF problem.
3. Workaround attempts: patch the config-layer renderer to `qwen3.8` (the 27B's,
   which exists) → model generates FAST but every token lands in the `thinking` field,
   `response` is empty ("thinking-swallow"). Same with `qwen3.5`. `qwen3vl` → zero tokens.
4. Root cause of the swallow: my Modelfile OVERRODE the template with the passthrough
   `TEMPLATE {{ .Prompt }}`. The qwen3-VL GGUF needs its native chat template to know
   how to end a thinking block and emit the answer.

## The fix (validated)

FROM-only Modelfile — no TEMPLATE, no RENDERER, no PARSER:

```
FROM /path/to/Huihui-Qwen3-VL-8B-Instruct-abliterated-Q4_K_M.gguf
FROM /path/to/mmproj-F16.gguf
```

ollama then auto-detects the GGUF's embedded chat template. Result: `response` filled,
`thinking` empty, 8B VL = 40.7-44.4 tok/s (fully GPU-offloaded, 4.7G fits in 8GB VRAM).
The 30B-A3B MoE (13.7G, partial offload) should follow the same recipe (~30-40 tok/s).
The same recipe worked for the dense 27B (`qwen3.8:27b-q3`) with explicit
TEMPLATE/RENDERER qwen3.8 — but for qwen3-VL models NEVER set them.

## Config-layer surgery technique (patch a model WITHOUT re-import)

Renderer name lives in the model's CONFIG layer (a small JSON blob), not the template
layer. Manifests at `manifests/registry.ollama.ai/library/<name>/<tag>` are plain JSON
and reference content-addressed blobs by `sha256:hex`; blob FILES are named
`sha256-<hex>` (dash). To change renderer/params/template without re-running the
30-min re-quantize:

1. Read the config blob: `cat blobs/sha256-<hex>` → JSON with `"renderer"`,
   `"model_family"`, `"parser"` etc.
2. Edit fields, `json.dumps(..., separators=(',',':'))`, sha256 the bytes, write
   `blobs/sha256-<newhex>`, `chown ollama:ollama` (match the old blob's uid/gid).
3. Edit the manifest: update `config.digest`/`config.size` (or the template/params
   layer entries the same way). No server restart needed for unloaded models.

Used to try renderers `qwen3-vl`→`qwen3.8`→`qwen3vl`→`qwen3.5` in minutes each.

## Other gotchas hit

- **Re-running `ollama create` re-runs llama-quantize** (~30 min / 13.7G write for the
  27B/MoE class) even when the output blob will dedupe ("using existing layer ...").
  It writes the temp COPY file, computes the digest, THEN dedupes. Don't re-create
  just to change a template — use layer surgery, or expect the write. Kill safety:
  watch `df`; the write peaks ~+14G.
- **Source blob vs requantized blob**: the upload copy (digest == the GGUF file) is
  orphaned once the manifest references the requantized output. `ollama rm` removes
  referenced blobs only; hunt the orphan with a digest diff (manifests `sha256:hex`
  vs blob filenames `sha256-hex`) and delete it to reclaim ~14G.
- **ollama upgrade**: v0.32.14 release assets are `.tar.zst` (the `.tgz` name 404s).
  `sudo tar --zstd -C /usr/local -xf` + restart service preserves all models/blobs.
- **`ollama run ollama/<name>`** treats the slash as a registry ref → pull error.
  Bare name for the ollama CLI; `ollama/` prefix only for Qubit ids.

## Benchmarks (same box, /api/generate, think:false)

| Model | Size | Speed |
|---|---|---|
| qwen3-vl:8b (8B VL Q4_K_M, full VRAM) | 4.7G | 40-44 tok/s |
| qwen3:30b-a3b (MoE Q3_K_M, partial offload) | 13.7G | ~31-37 tok/s (before template fix; regen pending) |
| qwen3.8:27b-q3 (dense Q3_K_M) | 12.6G | ~7 tok/s |
