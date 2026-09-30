# CGraph knowledge-graph session (2026-08-21) — worked example

Goal: graphify for the CGraph monorepo, scoped to apps/web + apps/backend +
packages (desktop/mobile/landing deferred until those apps exist).

## Setup that worked
```bash
mise install node@22 && export PATH="$HOME/.local/share/mise/installs/node/22/bin:$PATH"
mkdir -p ~/Projects/Repos/CGraph-graphify && cd "$_"
npm init -y && npm install @sentropic/graphify
./node_modules/.bin/graphify --help
```
(System node v18.19.1 too old; global `npm i -g` fails on /usr prefix.)

## Corpus / scope
- git tracks 5,234 files; web+backend+packages = 4,380; deferred apps 285;
  node_modules untracked (101k files, correctly ignored).
- `.graphifyignore` at repo root (gitignore-style): excludes node_modules,
  dist, build, coverage, .turbo, .next, deps, _build, infrastructure, scripts,
  desktop/mobile/mobile-android/landing, and ALL media binaries
  (*.png *.jpg *.jpeg *.gif *.webp *.ico *.mp4 *.mov *.pdf *.woff* *.ttf).
  Media binaries MUST be ignored or headless extract aborts demanding the
  OCR/vision pipeline.
- `detect --scope all` → included_count 4,104 (after ignore); 41 sensitive
  excluded (git-ignored secrets).
- Symlink trick for "only these dirs" FAILS: detect resolves symlinks to
  included_count 0. Use .graphifyignore patterns instead.

## Extraction
- `graphify extract "<repo>" --scope all --out /tmp/cgraph-graph-slice`:
  AST extraction 4,104/4,104 files (100%) in ~30s, 0 tokens.
  Diagnostics: `tree-sitter-elixir not available` for backend/*.exs —
  non-fatal, warns only.
- Semantic pass (157 non-code text files, 8 chunks) — the long debugging
  arc, see below.

## ROOT CAUSE of the semantic failures (found after hours of debugging)
graphify's `--backend openai` uses the **AI SDK v5 `openai()` provider,
which defaults to the RESPONSES API wire format**: it POSTs
`{"model", "input": [...], "temperature"}` to `/chat/completions`-style
endpoints and validates the reply against `openaiResponsesResponseSchema`.
DeepSeek (and ollama) only speak **chat completions** (`{"messages":[...]}`)
and return `choices[].message.content`.

So a raw proxy that just forwards the body fails with a bare
`Invalid JSON response` whose hidden cause is AI_TypeValidationError
(stack shows `OpenAIResponsesLanguageModel.doGenerate`). The fix is a proxy
that translates BOTH directions:
1. request: `input` -> `messages`; flatten Responses content arrays
   ([{type:"text",text}] -> "plain string")
2. response: `choices` -> `output:[{type:"message",role:"assistant",
   id, status:"completed", content:[{type:"output_text",text}]}]`,
   plus top-level `created_at`, `status:"completed"`, `object:"response"`
3. The response translation MUST be gated on the request having been
   Responses-API (`responses_mode` flag) — unconditional rewriting
   corrupts valid chat-completions replies.

Full working proxy: `scripts/fence_proxy.py` in this skill. Point graphify
at it: `OPENAI_API_KEY=*** OPENAI_BASE_URL=http://127.0.0.1:18099/v1
graphify extract ... --backend openai --model deepseek-chat`.

Other dead ends (do NOT repeat):
- `--backend ollama` local: `Unsupported model version v1 ... AI SDK 5
  only supports spec v2` (ollama 0.32.14 lacks the v2 provider spec).
- `--backend openai` + `OPENAI_BASE_URL=http://127.0.0.1:11434/v1`
  (ollama OpenAI-compat): local 3B/4B return prose or weak JSON, not the
  schema. phi-4-mini returns valid JSON but fence-wrapped.
- DeepSeek direct (no proxy): valid content but ```json-fenced AND the
  Responses-vs-chat mismatch — two separate failure modes, both fixed by
  the proxy.

## Debugging technique that cracked it (reusable)
graphify swallows the real error; `chunk N failed: Invalid JSON response`
tells you nothing. Patch the installed CLI to dump the full error object:
```js
// in node_modules/@sentropic/graphify/dist/cli.js, inside the chunk catch:
console.error("[graphify extract] FULL:", JSON.stringify(error, Object.getOwnPropertyNames(error)).slice(0, 1500));
```
The `cause` field then reveals `AI_TypeValidationError: Type validation
failed: Value: {"id":"...","object":"response",...` with the exact value
that failed Zod. Also run the proxy with `FENCE_PROXY_DEBUG=1` to log the
request body shape (body_keys, msgs roles/content types) and response
prefix — this is how the Responses-vs-chat mismatch was spotted.

## Where the build stopped (session end)
- `/tmp/cgraph-graph-slice/.graphify/manifest.json` (608K) holds the AST
  core — the hard structural part is DONE.
- `graph.json` NOT emitted because extract aborts when ALL semantic chunks
  fail. At session end the proxy translation was verified working in
  isolation (manual curl of Responses-shaped request -> clean output with
  id/status/created_at) and the SDK's openaiChatResponseSchema accepts
  DeepSeek's raw response, but graphify's full run still threw
  AI_TypeValidationError on the translated `object:"response"` shape —
  one field still mismatched (likely `created_at`/`created` top-level
  naming vs the Responses schema; error tail was truncated before the
  exact field was read).
- Finish paths (next session):
  1. Re-run with the FULL-error CLI patch + FENCE_PROXY_DEBUG, read the
     complete TypeValidationError tail, fix the one field in
     fence_proxy.py's responses_mode branch, re-run.
  2. Bypass the SDK entirely: hand-build `--semantic <path>.json` from the
     AST output (schema below) and `graphify extract --semantic ...` /
     `graphify build` — no LLM backend needed, guaranteed schema match.
  3. try `--backend gemini`/`claude-cli` (stricter JSON adherence) if keys
     exist.
- Then `graphify build`, `graphify query`, and `graphify serve` (stdio MCP)
  wired into the voice bridge so agents can query CGraph architecture live.

## Schema (from dist/cli.js, the exact target)
```json
{"nodes":[{"id":"filestem_entityname","label":"Human Readable Name",
  "file_type":"code|document|paper|image","source_file":"relative/path",
  "source_location":null,"source_url":null,"captured_at":null,
  "author":null,"contributor":null}],
 "edges":[{"source":"node_id","target":"node_id",
  "relation":"calls|implements|references|cites|conceptually_related_to|shares_data_with|semantically_similar_to|rationale_for",
  "confidence":"EXTRACTED|INFERRED|AMBIGUOUS","confidence_score":1.0,
  "source_file":"relative/path","source_location":null,"weight":1.0}],
 "hyperedges":[{"id":"snake_case_id","label":"...","nodes":[...],
  "relation":"participate_in|implement|form",
  "confidence":"EXTRACTED|INFERRED","confidence_score":0.75,
  "source_file":"relative/path"}],
 "input_tokens":0,"output_tokens":0}
```
