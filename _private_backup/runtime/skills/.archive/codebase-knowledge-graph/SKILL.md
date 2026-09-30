---
name: codebase-knowledge-graph
description: "Build a queryable knowledge graph of a codebase with graphify (@sentropic/graphify): AST + semantic extraction, backend selection, JSON-schema pitfalls, and MCP serving for agents. Use when the user asks to 'make graphify for <repo>', wants an architecture map agents can query, or a repo index that beats re-reading files."
version: 1.0.0
---

# Codebase Knowledge Graph (graphify)

**graphify** (`npm @sentropic/graphify`, repo `rhanka/graphify`) turns a folder of
code/docs into a persistent, ontology-typed knowledge graph under `.graphify/`:
`graph.json` (queryable), `GRAPH_REPORT.md`, `studio/` (visual), `wiki/`, `cache/`.
It is NOT an LLM — it is a skill/tool an agent (Claude Code, Codex, OpenClaw,
Hermes, ...) uses to reason over a corpus. Claims ~71× fewer tokens per query
than re-reading raw files once built.

## When to use
- User says "make graphify for <repo>" / "build a knowledge graph of <project>"
- User wants the agent to answer architecture questions ("what calls the Stripe
  webhook?", "who depends on api-client?") without scanning the tree
- User names "graphify" as if it were a model — it is a knowledge-graph TOOL
  (verify before promising; see Pitfalls).

## Pipeline (four stages)
1. **detect** — scope the corpus (`--scope all|committed|tracked`; counts
   **git-tracked** files). Writes `.graphify_detect.json`.
2. **extract** — AST parse (fast, zero LLM) + optional semantic pass (LLM
   enriches docs/config/README nodes). This is where the real work happens.
3. **build** — validate fragments, emit `graph.json` (clustering, community
   labels). Also: `cluster-only`, `label`, `describe` for post-hoc enrichment.
4. **query / serve** — `graphify query "..." --graph .graphify/graph.json`,
   `path <a> <b>`, `explain <node>`, `tree <node>`, `summary`, and
   **`graphify serve`** = stdio MCP server (wire into an agent for live queries).
   Export: `graphify export` → wiki, Obsidian, SVG, GraphML, Neo4j/Cypher.

## Install (Linux, this user)
System node is 18; graphify needs 20+ → use mise:
```bash
mise install node@22 && mise use -g node@22   # or per-project
export PATH="$HOME/.local/share/mise/installs/node/22/bin:$PATH"
mkdir -p ~/Projects/Repos/<proj>-graphify && cd "$_"
npm init -y && npm install @sentropic/graphify   # project-local, NOT -g
./node_modules/.bin/graphify --help
```
Global `npm i -g` fails on this box (system /usr prefix, no write) — always
install into a dedicated workspace dir. The graph output lives in the REPO
(`.graphify/`), the tool lives in the workspace.

## Extraction backends (learned the hard way)
- **AST-only**: `graphify extract <repo> --scope all --out <out>` — handles
  thousands of files in seconds, 0 LLM tokens. Produces the structural core
  (files, functions, calls, deps). Do this FIRST, always.
- **Semantic** (`--backend <name>`): runs only on non-code text files (docs,
  config, READMEs — NOT code). Uses the Vercel AI SDK (`ai` npm pkg).
  - `--backend openai` + `OPENAI_API_KEY`/`OPENAI_BASE_URL` — works with any
    OpenAI-compatible endpoint (DeepSeek: `https://api.deepseek.com/v1`,
    model `deepseek-chat`).
  - `--backend ollama` **FAILS on ollama ≤0.32** with
    `Unsupported model version v1 ... AI SDK 5 only supports spec v2`.
    Workaround: use `--backend openai` + `OPENAI_BASE_URL=http://127.0.0.1:11434/v1`
    (ollama's OpenAI-compatible endpoint) + local model name.
  - Local 3B/4B models return prose instead of the schema (weak at structured
    JSON). phi-4-mini produces valid JSON but wraps it in ```json fences.

## Pitfalls (all hit in the field)
1. **Models wrap JSON in ```json fences** (DeepSeek-chat, phi-4-mini) →
   graphify's `JSON.parse` throws `Unexpected token '`'`. Fix: strip fences
   through a tiny proxy — see `scripts/fence_proxy.py` (run it, point
   `OPENAI_BASE_URL=http://127.0.0.1:18099/v1` at it; upstream selectable via
   `FENCE_PROXY_UPSTREAM`, reads `DEEPSEEK_API_KEY` from `~/.hermes/.env`).
2. **"Invalid JSON response" on semantic chunks — REAL root cause is the
   Responses API wire format, not the graph schema.** AI SDK v5's
   `openai()` provider defaults to the RESPONSES API (`{model, input:[...],
   temperature}`), but DeepSeek/ollama only speak chat completions
   (`{messages:[...]}`). A bare pass-through proxy fails with a useless
   `Invalid JSON response`; the hidden cause (visible in the stack, e.g.
   `OpenAIResponsesLanguageModel.doGenerate`, and in the error `cause` as
   `AI_TypeValidationError`) is the request/response shape mismatch. Fix:
   bidirectional proxy — request `input`→`messages` + flatten content
   arrays; response `choices`→`output:[{type:"message",role,id,status,
   content:[{type:"output_text",text}]}]` + top-level `created_at`/`status`.
   CRITICAL: gate the response translation on the request having been
   Responses-API (`responses_mode` flag) — unconditional rewriting corrupts
   valid chat replies. Working proxy: `scripts/fence_proxy.py`. (Secondary
   validation issues — edges want `source`/`target` not `from`/`to`,
   `relation` enum, `confidence` fields — are real but were NOT the actual
   blocker; the wire-format mismatch was.)
3. **Debug technique: dump the full error object.** graphify prints only
   `chunk N failed: Invalid JSON response`; the real Zod/type error is in
   the `cause`. Patch the installed CLI:
   `console.error(JSON.stringify(error, Object.getOwnPropertyNames(error)))`
   inside the chunk catch (dist/cli.js) and run with `FENCE_PROXY_DEBUG=1`
   to log request body shape + response prefix. This is how the
   Responses-vs-chat mismatch was spotted (body_keys showed
   `["model","input","temperature"]` and content was a list). See
   `references/cgraph-session.md` for the full worked debug path.
4. **`.graphifyignore`** is gitignore-style, read from repo root upward
   (falls back to `.gitignore`). Detection reports `included_count` against
   git-tracked files. Exclude `node_modules`, `dist`, `_build`, `deps`,
   `infrastructure`, `scripts`, and ALL media binaries (`*.png *.jpg *.mp4
   *.pdf ...`) — binaries force the OCR/vision pipeline and abort a headless
   text-only run with "detected non-code corpus files that require semantic
   extraction". Keep the ignore file committed to the repo; it encodes the
   graph's scope (e.g. web+backend+packages now, desktop/mobile later).
5. **tree-sitter gaps**: Elixir `.exs` files warn "tree-sitter-elixir not
   available" — AST diagnostics only, run continues. Non-fatal.
6. **Verify user-named "models" before promising**: reasonix = DeepSeek coding
   AGENT (VS Code extension `sivanliu.reasonix-agent`, cache-first Flash —
   user often already has it), graphify = knowledge-graph TOOL, dflash 2 =
   speculative-decoding DRAFTER (not a router/standalone). When the user names
   something that sounds like a model, check via Perplexity/web before
   promising it helps; the honest answer is usually "you already run the
   cheapest option" (DeepSeek Flash) and the real lever is local-first routing.

## Verification
- After `extract` succeeds: `ls <out>/.graphify/manifest.json` (~600K for
  4k files) proves AST core. `graphify build` then `graphify query "..."`.
- `graphify detect` output shows `included_count` — sanity-check it matches
  `git ls-files <slices> | wc -l` minus ignored/sensitive.

## Reference
- `references/cgraph-session.md` — worked example: CGraph monorepo
  (web+backend+packages, desktop/mobile deferred), 4,104-file AST extraction
  done, semantic pass blocked on the AI SDK v5 Responses-vs-chat wire-format
  mismatch (root cause + proxy fix + exact finish paths); exact commands +
  file layout.
