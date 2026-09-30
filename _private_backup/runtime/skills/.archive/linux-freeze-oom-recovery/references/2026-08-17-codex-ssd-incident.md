# 2026-08-17 codex / Kingston SSD incident — full case

Machine: Ryzen 7 5700X (16T), 62GB RAM, RTX 3070 Ti 8GB. Ubuntu 24.04 root on
Kingston SA400S37480G 480GB (DRAM-less SATA SSD, root LV on LUKS), Intenso 119GB
SSD = Windows NTFS (no second Linux disk). Swap 8GB file on root disk.

## Symptom timeline
- User: "PC keeps freezing and crashing when hard and ram start working, need to
  force turn off each time." 3 hard boots in 3h on Aug 17; several 8-20min boots.
- Freezes also during ANY download/update/Steam game update — this predated qwen.

## Root causes (two independent)
1. **codex session-bloat RAM bomb**: `~/.codex/sessions/2026/07/30/rollout-...jsonl`
   = 45GB single file (plus 6.3GB from Jun 30, 123MB Aug 1). The VS Code ChatGPT
   extension's bundled `codex app-server` re-loaded it every launch: RSS climbed
   ~1GB/5s (measured 19→30GB in 86s, 57GB on Aug 16) → OOM killer → swap thrash
   on slow disk → system-wide freeze. journal smoking gun:
   `Out of memory: Killed process 679782 (codex) total-vm:58GB, anon-rss:57GB`.
2. **DRAM-less SSD GC stalls**: sustained writes (downloads, model swaps) →
   Kingston garbage collection → multi-second system-wide stalls. 84% full made
   it worse. Hard reboot mid-download lost a 12GB in-flight file (page cache
   never flushed).

## Fixes applied
- Deleted the 3 old codex rollout files + stale `session_index.jsonl` (freed 51GB,
  disk 84%→72%). Codex rebuilds the index; new sessions stay small.
- `apt install earlyoom` + `/etc/default/earlyoom`:
  `EARLYOOM_ARGS="-r 0 --prefer (^|/)codex$"` (shipped default `-r 3600` removed).
  Verified active; note it never got to fire in-session (codex died first).
- Throttled downloads: `ionice -c3 wget -c --limit-rate=10m --tries=10 --timeout=60`
  — zero freezes during a 13.8GB Q3 GGUF download vs full-speed curls that
  froze/crashed the machine.
- Sudo password rotation: stored in `~/.hermes/.env`; discovered write_file
  redaction mangles `SUDO_PASSWORD=` literals — read via Python key-split.

## Model routing outcome (OpenClaw)
- `ollama/deepseek-r1:7b` was in OpenClaw's registry but NOT in Ollama (model
  gone) → `FailoverError: The selected model was not found by the provider` →
  re-pulled (4.7GB).
- r1:7b (7B) passes raw `ollama run` tests but FAILS agent tasks (hallucinates
  tool docs inside the agent prompt) — too small for OpenClaw's system prompt.
- `deepseek/deepseek-v4-flash` (cloud plugin) verified: clean "WORKING" marker
  with `--session-key <fresh> --model deepseek/deepseek-v4-flash`.
- Final: OpenClaw default = `deepseek/deepseek-v4-flash`; r1:7b = chat only;
  `qwen3.8:27b-q3` (unsloth Q3_K_M GGUF, 13.8GB) being created as faster local
  qwen (num_ctx 16384, thinking off, keeps vision projector).
- r1:7b num_ctx tuned 131072 → 32768 (131k ctx = ~7GB KV cache on a 4.7GB model).

## Episode 2 — same day, 22:36-23:21 boot (download-completion freeze)
- 13.8GB Q3 GGUF downloaded throttled (`ionice -c3 wget --limit-rate=10m`) over
  ~28min, completed 23:19-23:20, then the box froze at ~23:21 → force-off
  (boot -1 ended with NO hung_task/OOM lines in journal — pure SSD stall, kernel
  watchdog never tripped on short stalls). Boots that day: 18:15→19:35 hard,
  20:19→20:35 (16min!) hard, 21:02→22:35 clean, 22:36→23:21 hard, 23:52+ ok.
- Throttling alone is NOT enough once disk ≥80%: the +14GB (model + mmproj +
  Modelfile) pushed 72% → 83% (335G/429G), and the FINAL write burst of a
  completed download is what triggered GC stall. Keep disk <70-75% as the hard
  rule; the 10MB/s cap just buys time.
- systemd-oomd also killed snap.code (VS Code) at 18:41 in the 18:15 boot:
  `/user.slice` memory pressure 68.48% > 50% for >20s. earlyoom never fired
  (mem stayed >10% + swap ≤10% gate). Two guards now: earlyoom (RAM floors) +
  systemd-oomd (pressure) — neither helps SSD stalls.
- Disk space inventory on the 480G root: ollama models 48G, ~/.cache 9.2G,
  Downloads 14G, Projects/ (node_modules across monorepos) unknown-large,
  15 disabled snap revisions (~1-3G), journald vacuumed 420M→200M. SMART:
  Kingston SA400 0 reallocated/0 pending — healthy, just DRAM-less.
- Ollama import caveat — CORRECTED by episode 3: `ollama create` FROM a GGUF
  ALWAYS runs `/usr/local/lib/ollama/llama-quantize --allow-requantize` → full
  ~same-size rewrite of the model blob into blobs/COPY<rand> (then renamed).
  An existing blob (sha256 of the source already in blobs/) only skips the
  UPLOAD — NOT the requantize. There is NO trick to avoid the full-size write;
  the earlier "hardlink the blob to skip the copy" note was WRONG, remove it.
  The create flow: parse FROM file (needs the source path to exist) → upload
  source blob if missing → llama-quantize (13G write) → manifest. Interrupted
  imports leave multi-GB junk: COPY<rand> temps AND completed-but-unreferenced
  blobs (manifest never written). Cleanup: orphan check must compare
  manifests' "sha256:hex" digests against blob FILENAMES' "sha256-hex" hex.
  Live freeze signature of the import write: llama-quantize stuck STAT=D,
  unkillable even with SIGKILL (pending kill lands when the I/O completes),
  load spikes to 10, systemd kills journald ("Failed with result 'timeout'"),
  disk used climbs as the write grinds at ~6MB/s. Nothing is lost on
  force-off: the completed source blob + projector are safe; only the
  requantize temp dies. Correct import procedure on this box: free disk to
  <70% FIRST (rm old models, purge COPY junk), keep the source GGUF until
  create succeeds, run create with nothing else active.

## Resolution (same night) — disk 84%→69%, import done, routing live
- Cleaned: `ollama rm qwen3.8:27b` (Q4, 15.7G), regenerable caches (codex-runtimes,
  ms-playwright, google-chrome, pnpm, uv ≈7.5G), 15 disabled snap revisions,
  13G of interrupted-import COPY junk, Downloads copies after import (14G).
  Disk: 84% → 69% (129G free). Reboot count today: 4 hard freezes, all tied to
  multi-GB writes at ≥80% fullness. Rule held: import at 72% = success, no freeze.
- Import of qwen3.8:27b-q3 (huihui abliterated Q3_K + mmproj): succeeded at 72%
  disk. Create DID run llama-quantize --allow-requantize (~30 min, ~7MB/s) but
  the canonical-copy output deduped to the EXISTING source blob ("using existing
  layer sha256:a82fe3") → no new net space. Source GGUF is NOT needed during
  create once the blob exists, but keep it until "success" anyway (parse step
  opens the FROM path). Verified: `ollama run` → MODEL_ALIVE, uncensored prompt
  answered, and agent-layer turn returned WORKING.
- OpenClaw routing: `openclaw config patch --replace-path <path>` replaces
  arrays (the old `--replace` flag doesn't exist; error text lies). Registered
  ollama/qwen3.8:27b-q3 (text+image, 262k ctx, num_ctx 65536) in the ollama
  provider models array, removed stale qwen3.8:27b from providers AND from
  `agents.defaults.models` (agent allowlist — a stale entry there survived the
  provider patch), `openclaw models set ollama/qwen3.8:27b-q3`,
  `openclaw models fallbacks add deepseek/deepseek-v4-flash`. Fallback list
  fires on model ERRORS (not-found/API failure), NOT on answer quality — no
  semantic difficulty routing exists; manual switch = `openclaw models set`.

## Episode 4 — MoE 30B-A3B saga: renderer gap + verification-churn freeze
- qwen3-VL MoE import at 73-76% disk SUCCEEDED (no freeze during the 13.7G
  requantize) — but it did NOT dedupe this time: source blob + requantized
  blob = 2x model size (disk 76%→79%). Requantize-dedupe is NOT guaranteed.
- Freeze #5 came AFTER the import, during verification churn: multiple 15G
  model loads + 500-error retries back-to-back while the 27B was still loaded
  (load hit 42, journald got killed again). Lesson: verify ONE big model at a
  time; `ollama stop` other models first; a single sustained write is safer
  than load/error churn at high fullness.
- Root cause of the 500s: ollama 0.32.13 lacks the `qwen3-vl` renderer
  ("unknown renderer" at template selection). Config-layer surgery (renderer →
  qwen3.8) made it generate but the qwen3vlmoe arch swallowed output into the
  `thinking` field (empty `response`) — durable fix is an OLLAMA UPGRADE; MoE
  cut for the night. Full repair technique → openclaw-operations skill.
- Envelope data points on the SA400: import at 72% = clean; 73-79% + verify
  churn = freeze; peak ≥80% = don't. Cleanup pattern that saved the night:
  `ollama stop` heavy models → delete Downloads copies → orphan-check + delete
  source blob → re-verify at each step.

## Episode 4 — MoE import saga, disk hunt, Steam throttle (same night)
- MoE (noctrex Qwen3-VL-30B-A3B abliterated) import succeeded at 73% disk, but
  every load → `500 unknown renderer "qwen3-vl"` (ollama v0.32.13 lacks the
  renderer; latest 0.32.14 is a patch — check before assuming). Renderer lives
  in the manifest CONFIG layer (JSON). Config-surgery (new blob + manifest
  digest, no re-import) with renderers qwen3-vl/qwen3vl/qwen3.8/qwen3.5 all
  fail on qwen3vlmoe arch: model generates but EVERYTHING lands in `thinking`
  (empty response) — needs the real qwen3-vl renderer = ollama upgrade.
  Ollama asset gotcha: releases ship `.tar.zst` (not `.tgz`); install by
  extracting to /usr/local + service restart, keeps models.
- Freeze #5 cause: 79% disk + loading 15G MoE while the 18G 27B was still
  loaded + 500-retry loop → load avg 42, journald killed, D-state unkillable.
  Rule: `ollama stop` other models first; disk <70% before multi-GB loads.
- Disk hunt (user: "I deleted all games, where's my space?"): ~/qwen38-full
  59G (full-precision safetensors from a DIY abliteration attempt, superseded
  by the huihui Q3 GGUF) + obliteratus-venv 5.5G + 3× Steam Proton runtimes
  5.5G (CS:GO folder was 8K = games already gone; Steam lives at ~/.steam NOT
  ~/.local/share/Steam). Freed 64G → 54%.
- Steam freeze fix (updates/downloads): ~/.steam/steam/config/config.vdf, add
  `"DownloadThrottleKbps" "10240"` under InstallConfigStore→Software→Valve→
  Steam. `RecentDownloadRate` showed ~11.8MB/s before = the freeze zone.

## Numbers worth remembering
- qwen3.8:27b Q4_K_M on this box: ~7 tok/s gen (82% CPU / 18% GPU, VRAM full at
  6.8/8GB). Q3_K_M (~13.8GB) target: ~10-12 tok/s. deepseek-r1:7b: ~20-30 tok/s
  (46% GPU at 32k ctx).
