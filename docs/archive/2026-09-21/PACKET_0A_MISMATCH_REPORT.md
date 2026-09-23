# Packet 0A — Mismatch Report

Status: **inventory baseline** — documentation-only packet; **no runtime behavior was changed**.
Date: 2026-08-24
Repo: `~/qubit-voice-agent` @ `86d1b4a` (+ unrelated uncommitted user work preserved)
Contract: `docs/UNIFIED_AGENT_PLAN.md` §2, §3 (P0-A…P0-Q), §5 Phase 0, packet table row 0A.
Companion record: `docs/UPSTREAM_REUSE.md` (created in this packet).

Method: Graft graph first (`graft map`, `graft ask --source`, `graft grep`), then exact-span file
reads; read-only runtime diagnostics only (`ollama ps/list/show`, `systemctl --user cat/show/
list-timers/is-enabled/is-active`, `ss -tlnp`, `stat`, `pip list`). Five parallel read-only audit
agents were used for deployment/dependencies, model callers/language/private-data,
inbox/reporting/scheduling, and the two upstream provenance sweeps.

Claim labels used below (per plan §2):

- **IMPLEMENTED** — code exists in the repo today (existence fact only).
- **VERIFIED** — reproducible evidence was produced in this packet.
- **DEGRADED** — present but with a known limitation, or check skipped/unavailable.
- **PLANNED** — target state per plan; does not exist yet.

Privacy rule honored: no phone numbers, emails, handles, account IDs, tokens, cookies, message
content, or debugger URLs are reproduced anywhere below (`[REDACTED]` marks inspected-but-withheld
values).

---

## 0. Preflight record

- `AGENTS.md` and the complete 1077-line `docs/UNIFIED_AGENT_PLAN.md` were re-read before any
  search or command.
- `git status --short`: 37 modified tracked files + 8 pre-existing untracked paths. **All are recorded as
  unrelated user work and none was modified, reverted, reformatted, or absorbed.** The two files
  created by this packet are new and additive only:
  `docs/PACKET_0A_MISMATCH_REPORT.md` (this file) and `docs/UPSTREAM_REUSE.md`.
- Graft consulted before opening sources; `graft/` is gitignored (`regenerable`, `.gitignore:13`),
  so no `graft build` was needed for a docs-only change (skipped deliberately).
- No live config/service/model/audio/browser/timer state was altered at any point.

Runtime facts captured (VERIFIED, read-only):

| Fact | Value | Time |
|---|---|---|
| Resident model | `qubit-brain4:latest` id `df22b4d4032c`, 100% GPU, ctx **8192**, until "Forever" | 2026-08-24 |
| Disk models | qubit-brain4, abliterated GGUF base pull, `qubit-brain`, `huihui_ai/llama3.2-abliterate:3b`, `nomic-embed-text`, `qwen3-vl:8b` (6.2 GB, disk-only) | 2026-08-24 |
| Bridge service | enabled + active, NRestarts=0, MemoryCurrent ≈ 1.29 GiB (86% of MemoryHigh=1.5G) | 2026-08-24 |
| Chromium CDP 127.0.0.1:9222 | **not listening** | 2026-08-24 |
| Inbox timers | none exist (only OS snap/tmpfiles timers) | 2026-08-24 |
| Python venv | 3.12.3; systemd 255; bubblewrap 0.9.0; Playwright 1.62.0 installed; himalaya 2.1.0 binary installed | 2026-08-24 |

Note: resident ctx 8192 contradicts the configured `num_ctx: 4096` (MOD-01/MOD-07) — proof the
conflicting-caller problem is live, not theoretical. Re-observed later on 2026-08-24 (independent
verification): runner had reloaded at ctx **4096**, same model/service never restarted — organic
confirmation that the last caller's options decide residency (MOD-07).

---

## A. Configuration precedence and environment overrides (plan P0-A, P0-B)

Precedence chain (**VERIFIED**, `voicebridge/config.py`): built-in `DEFAULTS` → YAML file selected
by `$VOICE_BRIDGE_CONFIG` or default path (`load()`, config.py:247-256, header doc lines 4-6) →
`VOICE_BRIDGE_*` env overrides applied last via `deep_merge` (:205-212).

| ID | Finding | Evidence | Label / owner |
|---|---|---|---|
| CFG-01 | Env override parser splits on every `_`, so `VOICE_BRIDGE_INTENT_LOCAL_LLM_MODEL` becomes `intent.local.llm.model` — silently wrong branch. Underscored keys unreachable by design. | config.py:215-227 (`path = key[...].lower().split("_")` :221) | DEGRADED → fix in **0B** (P0-B) |
| CFG-02 | Repo `config/voice.yaml` describes Scarlett 2i2 + AT2020, `mic.channels: 4`, `min_rms: 0.0008`, `rms_gate: 0.004`, TTS `provider: openai`, no empty output-device pins — all contradict the live tuned setup. | config/voice.yaml:1,20-24,56-58,71 vs diff vs live config | IMPLEMENTED (stale) → reconcile in **0B** |
| CFG-03 | Live user config is authoritative working setup: HyperX pinned by name, channels 1, gain 3.0, min_rms 0.0005, rms_gate 0.0015, hotwords, empty output pins, piper TTS — matches docs/AUDIO_SETUP.md. | `~/.config/openclaw-voice-bridge/voice.yaml` (diff captured), AUDIO_SETUP.md:13-28 | VERIFIED (authoritative) |
| CFG-04 | `config/voice.local.yaml`: channels 4 + gain 8.0 + old 3B model (`huihui_ai/llama3.2-abliterate:3b`) — violates AUDIO_SETUP rules 2–3 and current default. | config/voice.local.yaml:11,13,64 | IMPLEMENTED (stale) → 0B |
| CFG-05 | `config/voice.example.yaml`: documents old 3B model, "Scarlett 4ch" machine-specific comment, gain guidance "4-16" that contradicts AUDIO_SETUP rule 3 (hiss). Not machine-agnostic. | config/voice.example.yaml:7,36,100 | IMPLEMENTED (stale) → 0B |
| CFG-06 | No config-contract test catching stale mic/output/model/`speak_web_locally` values exists yet. | test suite survey (no such assertions found) | PLANNED → 0B |
| CFG-07 | `config doctor` (static/runtime modes) does not exist yet. | cli.py surface survey | PLANNED → 0B |

## B. README and audio documentation (plan P0-A)

| ID | Finding | Evidence | Label / owner |
|---|---|---|---|
| DOC-01 | AUDIO_SETUP.md is correct and current (HyperX mono mic, gain 3.0, EMPTY output pins, speak_web_locally false, USB-port caveat). Treated as protected source of truth. | docs/AUDIO_SETUP.md (full read) | VERIFIED |
| DOC-02 | README tuning table recommends `gain 8.0 / channels 4` (Scarlett) — directly contradicts AUDIO_SETUP rules 2–3. | README.md:219 vs AUDIO_SETUP.md:18,31-40 | IMPLEMENTED (stale) → 0B |
| DOC-03 | README still references old baked `qubit-brain` as the local brain; default is `qubit-brain4`. | README.md:124 vs config defaults + voice.yaml:95 | IMPLEMENTED (stale) → 0B |
| DOC-04 | README claims web/dashboard requests also speak on the PC by default; live rule is `speak_web_locally=false` (browser plays once). | README.md:174-177 vs AUDIO_SETUP.md:43-45 | IMPLEMENTED (stale) → 0B |
| DOC-05 | README suggests swapping to `qwen3:14b`-class local model — unsafe on this 8 GB GPU and against plan §2.1 co-residency constraints. | README.md:282 | IMPLEMENTED (dangerous advice) → 0B |
| DOC-06 | README language claims ("EN/RO") contradict VOICE_BRIDGE_SYSTEM.md and the EN-only code policy (details LANG-08). | README.md:45,:55 vs docs/VOICE_BRIDGE_SYSTEM.md:12,48-51 | IMPLEMENTED (contradiction) → language decision then 0B |
| DOC-07 | VOICE_BRIDGE_SYSTEM.md pipeline text still says `qubit-brain (3.6B)` / "our 3B"; stale vs brain4. | docs/VOICE_BRIDGE_SYSTEM.md:22,97,151-154 | IMPLEMENTED (stale) → 0B |
| DOC-08 | Dashboard security posture documented nowhere: bind `0.0.0.0` default, open API when token empty, CORS `*`, query-string token accepted and stored by frontend. Plan P0-C confirmed in code. | webui.py:937-938 (host default), :572-579 (_api_token_ok), :564/:596/:621 (CORS *), :495-496 (URL-token client) | DEGRADED (works locally, unsafe LAN) → **1B** |

## C. Installed vs repository systemd configuration (plan P0-J)

Installed unit dumped via `systemctl --user cat` (read-only); template read from repo.

| ID | Delta (template → installed) | Class |
|---|---|---|
| SYS-01 | `Restart=on-failure` → `always`; `RestartSec=3` → `2`; StartLimit 60/10 added; `Wants/After ollama.service` dropped (gateway only); CUDA `LD_LIBRARY_PATH` activated; `XAUTHORITY=/run/user/1000/gdm/Xauthority` added; `MemoryHigh=1.5G MemoryMax=2G Nice=-5` added; `__PROJECT_DIR__` → `%h/...` substituted | Mix of PORTABLE DEFAULTS vs MACHINE OVERRIDES — undocumented; reconciliation spec due **0B** |
| SYS-02 | `scripts/install.sh --with-systemd` **unconditionally overwrites** the tuned installed unit (install.sh:177-185) and force-restarts the bridge — running it today would destroy Restart=always, resource guardrails, CUDA/XAUTHORITY env. HIGH risk. | VERIFIED (script logic read) → guard required **0B** |
| SYS-03 | install.sh hardcodes pip list omitting declared deps (playwright, edge-tts missing from its list; install.sh:36-41) and requires `import onnx` unconditionally without installing/declaring onnx (:86-107) → fresh-install abort. | VERIFIED → dependency-contract work **0B/7A** |
| SYS-04 | Template orders a **user** unit against system `ollama.service` (After/Wants) — cross-boundary ordering ineffective on both sides. | VERIFIED → document/deploy contract **0B** |
| SCH-01 | No inbox/communications timer or service exists anywhere (list-timers, unit-files, `~/.config/systemd/user/`). In-process reminder is one-shot `threading.Timer` ≤180 min, non-durable. | VERIFIED — hourly monitor remains PLANNED (Phase 8D) |

## D. Undeclared / incidental dependencies (plan P0-L)

Declared in pyproject.toml: sherpa-onnx, sounddevice, numpy, pyyaml, websockets, piper-tts,
faster-whisper, webrtcvad, playwright (+extras pynput/edge-tts/pytest).

| ID | Finding | Evidence | Owner |
|---|---|---|---|
| DEP-01 | pydantic 2.13.4, fastapi 0.133.1, uvicorn 0.41.0, httpx, python-dotenv installed in venv, **zero imports anywhere** — dead weight masking the real contract. Confirms P0-L premise (with correction: they are unused, not imported). | pip list + rg import scan | prune note → 0B/7A |
| DEP-02 | psutil installed+undeclared; single guarded use (`except ImportError`) | voicebridge/local/actions.py:365 | declare-or-drop → 0B |
| DEP-03 | ddgs used unguarded-at-import in web_search.py; installed+undeclared | scripts/web_search.py:27 | declare → 0B |
| DEP-04 | websocket-client undeclared (guarded use) | scripts/review_tiktok.py:37 | declare → 0B |
| DEP-05 | edge_tts extra: module-level import executed before availability check → confusing ImportError when extra missing | voicebridge/tts/edge.py:27,:34 | guard order → later packet |
| DEP-06 | Cross-module reach-in: orchestrator imports `scripts/browser_auto.py` via sys.path insertion instead of packaging | actions.py:159,:255,:266,:289 | packaging hygiene → 7A |

No production import relies on an undeclared package **without a guard** except DEP-03 (ddgs);
DEP-03 is the fresh-install failure candidate alongside SYS-03's onnx.

## E. Dashboard paths and deployment assumptions (plan P0-J, task 8)

| ID | Finding | Evidence |
|---|---|---|
| PATH-01 | Hardcoded sibling dashboard dir `~/voice-dashboard/frontend` is the PRIMARY frontend source; repo-relative fallback exists and works (frontend/index.html present). Must become configured/repository-relative (0B task 8). Doctor must report which frontend is active. | voicebridge/webui.py:958-962 (hardcode at :960) |
| PATH-02 | Tests read the sibling repo directly → suite fails on any other machine. | tests/test_steal_list_strict.py:411,:482 |
| PATH-03 | Absolute Hermes-skill + venv-python paths in web_search script. | scripts/web_search.py:20-22 |
| PATH-04 | Personal app/dir/screenshot paths hardcoded in local actions. | voicebridge/local/actions.py:72-100,:127 |
| PATH-05 | Absolute-venv shebangs in five review_*.py scripts. | review_gmail/instagram/messenger/tiktok/all_inboxes.py:1 |

## F. Model-caller inventory and runner-option conflicts (plan P0-M, task 12; P0-O)

Full caller inventory (**VERIFIED** by code inspection; residency facts from `ollama ps`).
Effective live config values: `num_ctx: 4096`, `inject_memory: false`, `timeout_s: 8.0`,
`max_tokens: 100` (live voice.yaml intent block; same values mirrored in repo voice.yaml).

| # | Caller | Endpoint | Model source | stream | format json | temp | num_ctx | keep_alive | out cap | system source | context injected |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `local_llm.chat_local` local_llm.py:158-207 | /api/chat | param/cfg `intent.local_llm.model`=brain4 | no | no | 0.4 (:148) | **8192** sig default (:161) | **-1** (:147) | max_tokens=100 | SYSTEM_PROMPT const (:152)+dialogs | `<about-the-user>` ≤2500 chars (:126) |
| 2 | `local_llm.chat_local_stream` :210-281 | /api/chat | same | yes | no | 0.4 | 8192 default | -1 | max_tokens | same | same |
| 3 | `orchestrator._chat_local_reply` :2483-2533 | via #1/#2 | cfg llm model | per `intent.stream_speech` (default true :2497) | no | 0.4 | cfg num_ctx (live pins **4096**) | -1 | 100 | via local_llm | mem_ctx if `inject_memory` else lessons only (:2191-2204) |
| 4 | `orchestrator._progress_loop`→chat_local :1516-1520 | /api/chat | `intent.local_llm.model` :1518 | no | no | 0.4 | **omitted → 8192** ⚠ | -1 | 40 :1519 | SYSTEM_PROMPT | shared.as_prompt(800,6) :1513 |
| 5 | `intent.llm.classify_with_ollama` :100-137 | /api/generate | resolved resident model (classifier.py:193-194) | no | **yes** :109 | **0.0** :112 | **4096** :112 | **ABSENT** ⚠ | unset→128 (Modelfile) | payload system = SYSTEM_PROMPT+TOOL_SCHEMA :107 | user text ≤600 chars :106 |
| 6 | `orchestrator._chat_ollama` :2535-2557 | /api/generate | `intent.ollama_model` fallback chain :2538-2540 | no | no | 0.4 :2547 | **ABSENT → Modelfile 4096** ⚠ | **ABSENT** ⚠ | unset→128 | **Modelfile-baked SYSTEM** (none in payload) | conv_snapshot/conversation :2541-2543 |
| 7 | `summarizer.summarize_ollama` :33-54 | /api/generate | cfg summarize.ollama_model :93-95 | no | yes :38 | 0.2 :40 | **4096** :41 | -1 :39 | 90 :42 | SUMMARY_PROMPT payload :37-38 | agent output ≤900 chars :36 |
| 8 | `terminal._status_text` :57-85 | GET /api/ps | n/a (status probe) | — | — | — | — | — | — | — | — |
| 9 | `bake_brain.smoke_test` bake_brain.py:88-109 | /api/chat | hardcoded brain4 :96 | no | no | model default | none→Modelfile 4096 | ABSENT ⚠ | unset→128 | Modelfile-baked | none |
| 10 | `dashboard_stress._post_chat_warmup` dashboard_stress.py:318-328 | /api/chat | hardcoded brain4 :320 | no | no | default | none→4096 | -1 :321 | unset | Modelfile-baked | "ready" :322 (also recycles runner :254-268) |
| 11 | AirLLMLive test `_chat` test_steal_list_strict.py:188-198 | /api/chat | hardcoded brain4 :189 | no | no | default | none→4096 | -1 :189 | unset | Modelfile-baked | none (skipif-gated) |

Non-callers verified: `learning.py` (local JSONL only), `web_search.py` (DDG/browser only),
`shared_memory.py`, `webui.py` — no direct Ollama HTTP.

### Option conflicts (each pair forces runner reload under Ollama semantics — see UPSTREAM_REUSE R5)

| ID | Conflict |
|---|---|
| MOD-01 | num_ctx **4096** (#5 classifier :112; #7 summarizer :41; live config; Modelfile.qubit4:188) ⇄ **8192** (#1/#2 signature defaults :140,:161; #3 code default :2496 overridden by live cfg to 4096; #4 omission :1516-1520) |
| MOD-02 | keep_alive **-1** (#1,#2,#3,#4,#7,#10,#11) ⇄ **absent → ~5-min TTL eviction** (#5 classifier :104-113; #6 :2546-2547; #9 :96-97) |
| MOD-03 | temperature 0.0 (classifier) vs 0.2 (summarizer :40) vs 0.4 (chat/_chat_ollama/Modelfile) |
| MOD-04 | num_predict 100 (live chat cfg) vs 40 (progress :1519) vs 90 (summarizer :42) vs unspecified→128 (classifier, _chat_ollama, smoke tests, Modelfile :186) |
| MOD-05 | top_p explicit 0.9 only on chat paths (:150); others inherit Modelfile 0.9 coincidentally |
| MOD-06 | System-prompt alternation: payload-supplied prompts (#1-#5,#7) vs Modelfile-baked SYSTEM (#6,#9,#10,#11) — defeats prompt-cache sharing across paths |
| MOD-07 | Observed residency ctx 8192/"Forever" contradicts configured 4096 — conflicting-path usage demonstrably happening (`ollama ps`, 2026-08-24) |

Consequence: alternating classify ↔ chat ↔ progress paths can reload the same model (plan recorded
a measured 3.246 s load transition). **Packet 0A changes nothing; Packet 4B owns the fix.**
Every mismatch above is intentionally made impossible to miss here per plan task 12.

Classifier explanatory-field cost (P0-O): prompt demands `reason` on every LLM classification;
used only for logs/UI (intent/llm.py:68-69,:132-133). Deterministic routing precedes LLM fallback
(classifier.py:271-395); VALID_KINDS = system, confirm, cancel, coding, conversation, clarify,
local, hermes (:73-74).

## G. Language policy and prompt sources (plan task 13, P0-N)

Contradictory statements inventoried (**VERIFIED**). No behavior changed in this packet.

| ID | Source | Stance |
|---|---|---|
| LANG-01 | local_llm.py:183-185 comment "speaks ONLY English and Romanian" vs same-file :25-26 ":LANGUAGE: …ENGLISH ONLY… Never reply in Romanian" | internal contradiction |
| LANG-02 | Drift detector claims diacritic coverage but all Romanian diacritics fall below its codepoint threshold; detection reduces to word-frequency heuristics; <4-token replies always pass | local_llm.py:66-87 — unreliable guard |
| LANG-03 | Tracked config/personal-knowledge.md:25 states EN+RO rule — reaches the model whenever injected/baked | contradicts EN-only code |
| LANG-04/05 | Modelfile.qubit4 header :14-16 ENGLISH ONLY, while baked blocks inside the SAME artifact say EN+RO (:89) and even "reply in Romanian" (:122) | self-contradicting model artifact |
| LANG-06 | Cloud paths instruct SAME-language incl. RO explicitly (orchestrator.py:2335,:2405,:2461) | asymmetric vs local EN-only |
| LANG-07 | ASR stores `_last_spoken_language` (orchestrator.py:3433) but nothing reads it — dead RO plumbing | dead code |
| LANG-08 | README says replies come in EN/RO (:45,:55); VOICE_BRIDGE_SYSTEM says English-only, "all Romanian removed" (:48-51) | doc-vs-doc contradiction |
| LANG-09 | TTS is en_US-only single voice; `language` arg ignored (tts/piper.py:25-26,:74-75) | effective spoken policy: EN-only |

Net: **code+TTS+tests+one doc enforce English-only; runtime-injected personal file, two baked blocks
inside the model artifact, cloud-path prompts, and README assert English+Romanian.** Per plan task
13 the single policy must be chosen and documented in 0B without changing spoken behavior here.

Prompt-source duplication (P0-N): three parallel sources feed persona/facts — Modelfile-baked
biography (direct `ollama run` + callers #6/#9-#11), `local_llm.SYSTEM_PROMPT` + dialogs (#1-#5),
and runtime memory injection (#3 when `inject_memory` true). They disagree (LANG-*, PRIV-*).

## H. Private-data placement (plan tasks 14, rule 29; values REDACTED)

Classification legend: PUBLIC / PRIVATE / SECRET-REFERENCE / VOLATILE / STALE.

| ID | Location | Contents (categories only) | Class | Migration note |
|---|---|---|---|---|
| PRIV-01 | `config/personal-knowledge.md` — **Git-tracked** | phone literal :16 [REDACTED], email :17 [REDACTED], GitHub handle :18, city/country :14-15, hardware :19-21, project paths :30-37 | PRIVATE + VOLATILE in Git HEAD/history — rule-29 violation | remove from tracking + rotate contact data; history rewrite needs user authorization |
| PRIV-02 | `Modelfile.qubit` — **Git-tracked** | full contact/social-ID block :5,:19-29 [REDACTED] | PRIVATE — rule-29 violation, committed | same as PRIV-01 |
| PRIV-03 | `tests/test_stress.py:572` | social handle URL in fixture [REDACTED] | PRIVATE identifier in test fixture | replace with synthetic fixture |
| PRIV-04 | `config/voice.yaml` (tracked) + example | machine hardware specifics (Scarlett/AT2020, 4-ch) | PERSONAL/VOLATILE (no contact PII) | make examples machine-agnostic (0B) |
| PRIV-05 | Untracked `Modelfile.qubit4` SYSTEM block :10-181 (~172 lines) | person name/contact :24-25,:75-76,:114-117 [REDACTED]; social/account numeric IDs :78-81,:115-117 [REDACTED]; location/timezone :73-74 [REDACTED]; PC hardware :84-86 [REDACTED]; audio setup SELF-CONTRADICTING (Scarlett :85 vs HyperX :177-180); tool/capability claims :31-66,:146-160; gateway endpoint/port :146-150; security-posture meta-note (no key values present — verified) :162-163; TODO list :172-175; block truncated mid-sentence :180-181 (bake cap 4200 chars, bake_brain.py:29) | PRIVATE identifiers + VOLATILE + STALE baked into model artifact; exportable via any `ollama show/cp` | Packet 4B replacement (minimal artifact + context compiler); interim: never commit this file |
| PRIV-06 | `scripts/bake_brain.py` :25-39,:54-85 | reads live personal-knowledge.md + MEMORY.md, strips headings, caps 4200 chars, writes composed result INTO repo-root Modelfile.qubit4, then `ollama create`; smoke-test needles embed partial personal handle/business name [REDACTED] :90-92 | mechanism that bakes PII into an artifact adjacent to Git | superseded by 4B context compiler |
| PRIV-07 | Runtime injection `orchestrator._load_memory_context` :2891-2924 | personal-knowledge ≤2500 chars :2907, MEMORY.md ≤1500 :2910, latest daily note ≤800 :2915-2917; loaded once at startup :533; gated by `llm_cfg.inject_memory` :2191 | PRIVATE/VOLATILE bulk injection **currently DISABLED**: live config sets `inject_memory: false` | plan §2 premise corrected: today duplication is avoided by disabling injection, NOT by fixing placement; lessons from learning ledger still prepend (:2197-2200) |

Correction recorded against the plan: "personal memory is still bulk-injected" (§2) is true of the
mechanism but currently inactive via config; the baked-artifact path (PRIV-05) is the ACTIVE
private-data carrier today. Also: one plaintext API key exists in the out-of-repo installed
`openclaw-gateway.service` environment line [REDACTED] — flagged for awareness; untouched.

## I. docs/STEAL_LIST.md status claims (plan tasks 1, 7; P0-K)

| ID | Claim (STEAL_LIST line) | Verdict | Evidence |
|---|---|---|---|
| STEAL-01 | "~2.6 GB qubit-brain resident" :14 | STALE/OVERSTATED | brain4 ≈3.2-3.9 GB; doc's own §5 says ~3.2 GB (:149-150); ollama ps shows 2.5 GB weights/3.9 GB runner |
| STEAL-02 | "cold load 2.3s, warm ~0.6s" :39 | FALSE | plan §2: live gate failed (~1.27 s median) |
| STEAL-03 | "≤0.7s for 40-token chat, within 30%" :73,:77 | FALSE | same; strict test actually enforces best ≤0.7 AND median ≤1.8 (test_steal_list_strict.py:229-232) — silent relaxation |
| STEAL-05 | "Single resident model" :150 | OVERSTATED | residency varies by last caller (MOD-01/02/07) |
| STEAL-07 | "`keep_alive: -1` on ALL local model calls" :135 | FALSE | classifier :104-113, _chat_ollama :2546-2547, bake_brain :96-97 omit it |
| STEAL-08 | "`num_ctx: 4096` universal" :137 | OVERSTATED | 8192 chat defaults vs 4096 classifier (MOD-01) |
| STEAL-09 | Seven items marked "[DONE 2026-08-23]" :146-169 | MIXED | symbols exist (learning.py:82,:94; intent/llm.py:71,:77; delta+ETag webui.py:660-687; stream local_llm.py:210; sandbox.run_tool sandbox.py:30-64) but DONE wording banned by plan §2; latency/residency sub-claims false |
| STEAL-10 | "Sandbox-with-timeout helper (OpenSandbox shape)" :169-172 | OVERSTATED | bounded exec w/ timeout+SIGKILL cleanup only — NOT isolation (P0-F confirmed, sandbox.py:30-64) |
| STEAL-15/16 | "17 cases"/"148 passed" :175 | TRUE / UNVERIFIABLE | 17 test functions counted; full-suite count not reproduced in this packet (see skipped checks) |
| STEAL-17 | provenance lines "CopilotKit MIT, AirLLM/OpenSandbox Apache-2.0" :5-6 | VERIFIED NOW | licenses confirmed at pinned commits (UPSTREAM_REUSE R1-R4) |

Required corrections (wording only) belong to 0B task 7; none applied here because STEAL_LIST.md
carries unrelated uncommitted user edits that must not be absorbed.

## J. Existing Gmail / Instagram / Messenger / CDP / reporting / scheduling paths (plan §2, P0-P/Q)

All findings VERIFIED by code reading; no account touched.

**Inbox adapters** — external dependency: review_gmail.py:12, review_instagram.py:11,
review_messenger.py:11 require `$HOME/.hermes/skills/communication/social-inbox-check/scripts/*.py`
(INBOX-01, not reproducible project deps). Cache TTL 300 s with up-to-7-day stale fallback
(gmail:9,:259; instagram:9,:97; messenger:9,:106; tiktok:9,:135); degradation surfaced only as
cosmetic "(cached Ns ago)" header text (INBOX-02). Every failure path exits 0 — unconfigured or
broken accounts look successful (gmail:263-287; insta:101-122; msg:110-131; tiktok:151-161;
all_inboxes:63-65) (INBOX-03). Broad exception swallowing returns None/partial everywhere
(INBOX-04). browser_inbox.py fabricates read-state: Gmail rows `"unread": True` hardcoded :124,
Messenger rows `"unread": False` :179, TikTok `True` :184 (INBOX-05). Instagram normalization
drops source links entirely (review_instagram.py:54-63) while Messenger keeps href but never uses
it as identity (INBOX-06). No stable identity contract anywhere — sender/subject/snippet/date
tuples at best; no cursor/dedupe/new-item detection (INBOX-07). browser_inbox.py uses Playwright
persistent profiles (:30,:69-73,:199-211) — forbidden for monitoring accounts by plan rule 34
(INBOX-08). Selectors are broad class fragments incl. `div.x1lliihq` :167, `[role=listitem]` +
`a[href*=t]` :175, bare `li` (review_tiktok.py:74) (INBOX-09).

**CDP** — browser_auto.py launches snap Chromium on the real profile with `--remote-debugging-
port=9222` (:36,:45-48), probes `/json/version` (:61-67), connects Playwright over CDP (:76), and
can CLOSE user tabs by URL match (:178-196) — conflicts with plan §2.1 "never close user tabs".
Nothing is listening on 127.0.0.1:9222 right now (`ss`, 2026-08-24): saved sessions ≠ active
monitoring (CDP-01, DEGRADED precondition).

**Reporting** — generate_report.py extracts files/commands/URLs/screenshots/tests via regexes
(:32-77) and writes Markdown only (:145-240,:279-294). `_persist_full_report` writes raw .md with
a small header (orchestrator.py:1886-1904). `/api/reports` (webui.py:706-780) merges activity-feed
+ shared-ledger + log records and NEVER reads the reports directory; no screenshot route, manifest,
or index exists (grep confirms zero hits) (RPT-01/RPT-02). Ground truth: reports dir holds **410
files, all .md; zero html/json/images** (RPT-02 evidence). Modes violate privacy targets: report
file 0664; reports/state-root/inbox-cache/browser-profile dirs 0775 (RPT-03; targets 0600/0700).

**Scheduling** — no inbox/communications timer/service exists (SCH-01); the in-process reminder is
one-shot, ≤180 min, dies with the process (actions.py:398-415, Timer at :414) (SCH-02). Hourly
monitor + professional reports remain PLANNED (Phase 8, gated).

## K. Upstream-derived functionality and provenance

Full records: `docs/UPSTREAM_REUSE.md` (created this packet; all 14 matrix rows processed).

Highlights and corrections against plan §4.1:

| UP-01 | AG-UI encoder/types depend on pydantic → ADAPT wire contract stdlib-only; null-omission implemented centrally in adaptation. |
| UP-02 | OpenSandbox pin has NO symlink-resolved allowlists (plan wording attributed too much); realpath/symlink rejection is a LOCAL requirement. Lifecycle has Pausing/Paused/Resuming, not "starting". Repo slug alibaba/OpenSandbox redirects to opensandbox-group. |
| UP-03 | Semantic Router no-match returns empty RouteChoice(), not None — local sentinel choice must be documented. |
| UP-04 | Letta pinned commit contains NO CODE (repo archived to stub; V1 server only on unsupported archive branch) → REJECT as pinned; re-pin decision needed before Packet 5B. |
| UP-05 | Qwen3.5-4B is multimodal (vision projector GGUFs separate); MTP/speculative acceleration documented for SGLang main/vLLM nightly only — never claimable on Ollama. |
| UP-06 | Himalaya installed binary (2.1.0) previously returned an error object on probe → INSTALLED ≠ HEALTHY; startup JSON-envelope health-check mandatory. |
| UP-07 | Ollama sched.go semantics CONFIRM the P0-M mechanism: differing options struct (incl. num_ctx) forces reload; keep_alive affects expiry only — validates MOD-01..07. |
| UP-08 | AirLLM REJECTed for production (torch/transformers/accelerate/bitsandbytes dependency set); guards recorded as design notes only. |

Existing upstream-derived functionality in-repo today: bounded tool runner shaped after
OpenSandbox concepts (sandbox.py:1-64 — bounded exec only), CopilotKit-inspired feedback ledger /
schema / streaming (learning.py:2,:79; local_llm streaming; webui delta+ETag) — all currently
UNDOCUMENTED provenance (no commit/path/license records existed before this packet); now covered
as baseline entries to be converted into real reuse records when their packets run.

---

## Validation performed in Packet 0A

| Check | Result |
|---|---|
| Preflight reads (AGENTS.md, full plan) | PASS |
| `git status --short` recorded; unrelated work preserved (untouched) | PASS |
| `git diff --check` (whitespace/conflict-marker scan of tracked diffs) | PASS (no output) |
| New files inspected post-write; schema fields complete (12 fields × 14 records; report sections A-K) | PASS |
| Privacy leak scan of both new files (no [REDACTED]-class literals beyond markers) | PASS |
| Focused steal-list pytest run | **SKIPPED → DEGRADED**: executes live-model HTTP paths and local servers; exceeds this packet's read-only diagnostic boundary; no code changed, so regression coverage adds no signal |
| `compileall` | SKIPPED (no source changed) |
| Chromium `/json/version` probe | SKIPPED (browser not running with CDP; endpoint fetch risks title leakage) → CDP-01 stays DEGRADED |
| Full pytest suite / "148 passed" claim reproduction | SKIPPED (same boundary reason) → STEAL-16 UNVERIFIABLE |
| `graft build` | SKIPPED deliberately (docs-only change; graft/ gitignored & regenerable) |
| Live-model benchmark reproduction | OUT OF SCOPE (Packet 4A) |

## Rollback

Delete the two new untracked files:
`rm docs/PACKET_0A_MISMATCH_REPORT.md docs/UPSTREAM_REUSE.md`
No other file, config, service, model, alias, audio setting, browser session, or timer was touched
by Packet 0A. Nothing requires service restart.
