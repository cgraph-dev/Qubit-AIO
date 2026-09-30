---
name: graphify-knowledge-graph
description: "Build and query knowledge graphs of codebases with graphify (@sentropic/graphify) — AST extraction, semantic enrichment, DeepSeek integration via fence proxy, MCP/CLI querying."
version: 1.0.0
author: qubit
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [graphify, knowledge-graph, codebase, deepseek, ast]
---

# Graphify Knowledge Graphs

Turn a codebase into a queryable knowledge graph (nodes/edges/communities
with LLM descriptions). Used for CGraph (web+backend+packages): 12,201
nodes, 24,526 edges, 620 communities, 12,199 described.

## Keeping the graph current (watch mode / auto-update)

The graph is a SNAPSHOT — it does NOT auto-update on file changes by
default. Two rebuild speeds, be explicit about which one you offer:

| Rebuild | What updates | Speed | Cost |
|---|---|---|---|
| `graphify update` / `graphify watch` (AST-only) | files, functions, calls, imports, communities | seconds | 0 LLM |
| full `extract` + `describe` | node descriptions, semantic docs | ~20 min / 12k nodes | ~$0.30 DeepSeek |

- `graphify watch "<repo>" --debounce 3` — watch a folder, auto-rebuild
  graph.json on code changes (structure only; new nodes lack descriptions).
- `graphify update "<repo>"` — one-shot code-only rebuild (same AST tier).
- Full re-describe is a periodic task, not per-save. New nodes from watch
  mode get descriptions only after a full extract + fill_descriptions pass.
- Recommend watch as a systemd user service if the user wants the browser
  graph always current.

## Viewing the visual studio in a browser

```bash
# export the static studio (uses --state, NOT --graph — "unknown option
# --graph" means you used the wrong flag):
$GRAPHIFY studio export OUT/.graphify/studio --state OUT/.graphify

# serve it:
cd OUT/.graphify/studio && python3 -m http.server 8800 --bind 127.0.0.1

# open in the user's browser: the agent shell usually has NO DISPLAY, so
# find the X session first (who / loginctl / ls /tmp/.X11-unix), then:
DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/$(id -u) xdg-open http://127.0.0.1:8800
```
The studio dir also contains `studio.html` — a self-contained single-file
bundle (9MB) that opens via file:// without a server.

## Prerequisites

- Node 20+ — system Node 18 is too old. Use mise: `mise use -g node@22`
  (installs to `~/.local/share/mise/installs/node/22/bin`; prepend to PATH)
- `npm install @sentropic/graphify` in a workspace dir (NOT global — global
  npm install fails on EACCES with system npm prefix /usr/local)

## Core commands

```bash
GRAPHIFY=~/path/to/workspace/node_modules/.bin/graphify
REPO="/path/to/repo"

# detect scope (respects .graphifyignore in the repo root)
$GRAPHIFY detect "$REPO" --scope all

# AST-only extraction (fast, 0 LLM): parses files into nodes/edges
$GRAPHIFY extract "$REPO" --scope all --no-cluster --out /tmp/out

# full extraction with semantic backend (see proxy below)
$GRAPHIFY extract "$REPO" --scope all --backend openai --model deepseek-chat --out /tmp/out

# build graph.json + communities + studio export
$GRAPHIFY extract ... # full run writes graph.json at end
$GRAPHIFY studio export /tmp/out/.graphify/studio --state /tmp/out/.graphify

# query
$GRAPHIFY query "question" --graph graph.json
$GRAPHIFY path "nodeA" "nodeB" --graph graph.json   # note: takes LABELS not ids
$GRAPHIFY explain "label.tsx" --graph graph.json    # label, not id!
$GRAPHIFY summary graph.json
```

## THE CRITICAL BIT: DeepSeek + AI SDK v5 (fence_proxy.py)

graphify v1 uses AI SDK v5. Its `openai()` provider defaults to the
**Responses API** (`{input:[...]}` request, `{output:[...]}` response) even
when the target (DeepSeek) only speaks `/chat/completions`
(`{messages:[...]}` / `{choices:[...]}`). Symptoms: every semantic chunk
fails "Invalid JSON response" with NO useful graphify output.

Fix: run a tiny HTTP proxy that translates both directions — the known-good
copy lives at `scripts/fence_proxy.py` in this skill (portable; the version
in the CGraph-graphify repo may drift). It:

1. Request: `input` -> `messages`; flatten content-part arrays to strings
   (DeepSeek 400s on array content).
2. Response: `choices[].message.content` -> Responses `output[]` shape.
   **Required fields that will bite you** (Zod schema validation):
   - `output[0].content[0].annotations: []` (array, not optional)
   - `output[0].content[0].logprobs: []`
   - `output[0].id` (string)
   - `output[0].status: "completed"`
   - top-level `created_at` (number), `status`, `object: "response"`
   - `usage.input_tokens` / `usage.output_tokens` (numbers)
3. Strip ```json fences from content (models wrap JSON; graphify's own
   parse handles fences but the SDK response schema does not).

Proxy usage:
```bash
FENCE_PROXY_UPSTREAM=https://api.deepseek.com/v1 python3 fence_proxy.py
# proxy reads DEEPSEEK_API_KEY from $QUBIT_HOME/.env automatically
export OPENAI_API_KEY=x OPENAI_BASE_URL=http://127.0.0.1:18099/v1
$GRAPHIFY extract "$REPO" --scope all --backend openai --model deepseek-chat --out /tmp/out
```

Ollama backend: AI SDK v5 ollama provider requires model spec v2 — ollama
0.32 fails "Unsupported model version v1". Local 3B/phi-4-mini are also too
weak for the strict extraction schema (return prose or off-schema JSON).
DeepSeek-chat through the proxy is the reliable path. qwen3:4b is broken
(empty content). Note: on this user's machine the big ollama models
(27b/30b/phi-4-mini/llama3.2:3b/qwen3:4b/deepseek-r1:7b) have been DELETED —
only `huihui_ai/llama3.2-abliterate:3b` (voice router, pinned forever),
`nomic-embed-text` (Qubit gateway memory, CPU), and `qwen3-vl:8b`
(kept-unloaded for vision) remain. Qubit/Qubit use the DeepSeek API, so
a graphify build never contends with big resident models; ollama's
OpenAI-compat /v1 endpoint is still available for any local LLM need.

## Descriptions pass (optional enrichment, ~$0.30 for 12k nodes)

`graphify describe` with `--description-mode direct` STILL fails via the SDK.
Instead use assistant mode + fill answers yourself:

1. `graphify describe /tmp/out` (assistant mode, no backend flags) — emits
   `description-instructions/batch-NNN.md` files, ~40 nodes each.
2. `fill_descriptions.py` (in CGraph-graphify repo): reads each .md, calls
   DeepSeek via the proxy, writes `batch-NNN.json` (node_id -> description).
3. Re-run `graphify describe /tmp/out` — it INGESTS the answer files in
   assistant mode (this is the only path that ingests), prints
   "ingested N description(s)", writes graph.json.

## Ignore file

`.graphifyignore` in repo root (gitignore-style, bare dir name matches at
any depth): exclude node_modules, dist, build, .turbo, infra, and deferred
apps. Detection counts git-tracked files; node_modules already excluded by
.gitignore. Binary assets (*.png etc.) must be excluded or extraction
demands the OCR/vision pipeline.

## Auto-update watcher (systemd, low-resource)

The canonical graph lives at `<repo>/.graphify/graph.json` (gitignored —
add `.graphify/` to the repo .gitignore). The systemd user service
`cgraph-graphify-watch.service` runs `graphify watch --debounce 60` with:

- `Nice=19`, `IOSchedulingClass=idle`, `CPUWeight=10`, `CPUQuota=25%`,
  `MemoryMax=1G` — rebuilds can never starve the desktop
- `GRAPHIFY_WATCH_STRUCTURE_ONLY=1` — REQUIRES the two patches in
  `apply-watch-patches.sh` (in the CGraph-graphify repo) on graphify's
  dist/cli.js:
  1. rebuildCode() must copy node `description` attrs from the previous
     graph.json — stock graphify wipes ALL descriptions on every watch
     rebuild (it only preserves communities). Symptom: graph.json drops
     from 12k described to 0 after a rebuild.
  2. watch command honors GRAPHIFY_WATCH_STRUCTURE_ONLY=1 to skip the
     per-node description/label LLM passes (re-emits ~300 instruction
     files per rebuild otherwise and never writes).

Verified end-to-end: file change → rebuild → new node lands in graph +
12,070 descriptions preserved. Rebuild takes ~3.5 min under the CPU cap
(git history scan dominates — extractGit rev-list on the 2.2GB repo).

Pitfalls: changes made DURING a rebuild are dropped (rebuilding lock +
pending cleared) — acceptable; debounce batches rapid saves. Full
re-describe is a manual weekly pass (`graphify describe` assistant mode +
fill_descriptions.py + re-ingest). If node_modules is reinstalled, re-run
`apply-watch-patches.sh` or descriptions get wiped.

## Query via voice bridge

Template `graphify-query` in the voice bridge routes architecture questions
("query the cgraph knowledge graph", "what connects the web app to the
backend", "what does the Stripe module do in the CGraph codebase") to a
wrapper script (graphify-query.sh: query/path/explain/summary). Note
path/explain take node LABELS (file names like stripe.tsx), not ids.

**As of Aug 2026 the template is `kind: local-script` (summarize-first)**:
the bridge runs `graphify-query.sh "{question}"` itself, feeds the raw graph
output (thousands of chars of NODE lines) to the LOCAL 3B model
(`summarizer.provider: ollama`, `summarizer.ollama_model:
huihui_ai/llama3.2-abliterate:3b`), and speaks ONLY the ≤40-word summary.
Verified live: a 6,038-char query result became "Updated several files in
apps/web… review before committing." The raw output goes to the webui
activity feed as reference, never to the ears. Pitfalls that bit during
setup:
- **summarizer model must EXIST in ollama** — pointing at the purged
  phi-4-mini gives `/api/generate` 404 (`summarizer.ollama.unavailable`)
  and silently falls back to speaking the RAW graph output verbatim.
- **template regex must be broad**: "what does the Stripe module do…"
  misses `stripe (do|work|connect)`; use
  `(what does|how does|what is|how is) (the )?(stripe|webhook|…)[ a-z]* (do|work|connect|handle|use)`.

## Pitfalls

- `graphify path/explain` use labels; `query` is fuzzy full-text.
- `graphify studio export` takes `--state <dir>` (the .graphify state dir),
  NOT `--graph <path>` — passing `--graph` fails with "unknown option".
- The graph is a snapshot; watch mode rebuilds structure but not
  descriptions. Say so explicitly when the user asks "does it update?".
- Opening the browser from the agent shell: DISPLAY is unset; resolve the
  user's X session (loginctl / who / /tmp/.X11-unix) and pass
  DISPLAY + XDG_RUNTIME_DIR explicitly.
- graph.json is 17MB for CGraph — don't commit to the app repo; keep in the
  graphify workspace, add to .gitignore.
- Editing node_modules cli.js for debugging: use process.stderr.write, not
  require("fs") (ESM bundle).
- .graphifyignore only trims at extraction; detect count may still show
  ignored files (it counts git-tracked before ignore applied) — check
  `excluded_ignored_count` moves at extract time.
