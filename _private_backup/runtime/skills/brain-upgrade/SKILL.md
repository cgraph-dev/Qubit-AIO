---
name: brain-upgrade
description: How to make the local 3B brain smarter at the weight level (LoRA, abliteration, modelfile, auto-improve loop) — beyond prompts.
---

# Brain Upgrade (Local 3B)

See `docs/BRAIN_UPGRADE.md` for the full playbook. TL;DR:

- **Modelfile SYSTEM bake** (today): `ollama create qubit-brain -f Modelfile.qubit` — bakes routing/report rules into the model.
- **Obliteratus** — weight surgery to remove refusals (diff-in-means).
- **Unsloth LoRA** — finetune on `shared_memory.jsonl` + `events.jsonl` (300 examples, 8 GB VRAM, 30 min).
- **Nightly DPO auto-improve** — `scripts/auto_improve.sh` at 03:00 on cron.
- **Deterministic fallback** — `scripts/generate_report.py` makes the local agent perfect at reports even if the 3B is weak.

Don't finetune until you have 2 weeks of logs. Deterministic scripts + StickyRoute already cover 80% of the "retarded model" complaints.

## References
- Umbrella: `qubit-voice-bridge/references/voice-providers-2026-08-23.md`, `references/power-loss-recovery.md`, `references/durable-routing-2026-08-23.md`, `references/professional-reports-2026-08-23.md`.
- Project docs: `docs/BRAIN_UPGRADE.md` (full playbook).
- Related skills: `voice-routing` (sticky hints + templates bypass), `professional-reporting` (deterministic `scripts/generate_report.py`), `vscode-memory` (shared ledger + VS Code capture).

