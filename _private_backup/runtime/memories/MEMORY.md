Git remote: cgraph-dev/CGraph. cgraph-web on Vercel.
§
CGraph UI: best of Telegram+Signal, NOT 1:1 copy; CLAUDE.md 'identical UX' language is outdated scaffolding; Signal=crypto/messaging refs, Telegram=UI refs only.
§
Qubit = OpenClaw fork at ~/Projects/Repos/Qubit Agent (not 'Qubit (2)'; ~/.local/bin/qubit launcher points at old name). Voice bridge daemon = ~/Projects/Repos/openclaw-voice-bridge (systemd svc, :8890, parakeet v3 ASR).
§
PA fees (8/26): +$0.99/order — 9.99% curr/item/boost, acct 12.99% (22g 17.99%), topup 4.99%; buyer 4.99%+$0.29; OSRS promo 4.99% til 9/21. Watchdog cron.
§
OpenClaw gateway (18789): default model ox-alpha-free (opencode-go), fallback laguna-s-2.1-free then deepseek-v4-flash. Hermes CLI default now ox-alpha-free (opencode-go), 1M context.
§
Scarlett 2i2 4ch profile; bridge takes loudest channel + high-pass; mic.gain 8.0 (AT2020).
§
task-templates pre-classifier. PersonaPlex REMOVED.
§
CGraph graphify: ~/Projects/Repos/CGraph-graphify (web+backend+packages); systemd cgraph-graphify-watch auto-rebuilds CGraph Umbrella/.graphify/graph.json. Skill graphify-knowledge-graph.
§
Perplexity Pro browser search (FREE 1yr): skill perplexity-web-search — CDP vs logged-in Chromium profile. OpenClaw search → duckduckgo. Zero API cost.
§
TTS=nova gpt-4o-mini-tts (JP accent) fallback Piper; interrupt before busy guard; /api/talk|type→/api/result.
§
Config: agent.reasoning_effort=none (speed-first; user flips /reasoning manually when a task needs depth). display.show_reasoning stays true — don't touch.
§
Voice UX: dashboard interrupt while busy must fire before busy guard; Qubit (assistant) / Tricky (user, cgraph-dev) identity persists via personal-knowledge + 8-turn memory; routing hermes delegation outranks templates; long tasks use 900s poll reaper, keep_alive pinned.
§
Durable memory: shared_memory.jsonl (fsync) + sticky_route.json (8 turns/10m) + VS Code workspace capture. Reports via scripts/generate_report.py.
§
User prefers concise, direct responses — no over-thinking or lengthy explanations. When frustrated with verbosity, respond with just the facts.