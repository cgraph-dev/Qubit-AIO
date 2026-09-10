# Brain Upgrade — How to Make the Local 3B Smarter (Beyond Text Files)

> You asked: can we edit the model's brain directly, remove bad habits and teach new things, auto-improve — not just prompts/templates. Answer: yes, with **finetuning**. Here's the practical path for this box.

## Reality Check (This Box: Ryzen 5700X + RTX 3070 Ti 8 GB + 62 GB RAM)

| Method | VRAM Need | Time | Risk | What It Does |
|--------|-----------|------|------|--------------|
| **Ollama modelfile SYSTEM patch** | 0 | 1 min | none | Bakes routing rules into the model weights-adjacent layer — survives no-file edits |
| **LoRA finetune (Unsloth / Axolotl)** | 8–12 GB | 20–90 min | low | Trains a small adapter on your voice logs so the 3B learns your tasks |
| **Abliteration / weight surgery (Obliteratus)** | 8 GB | 10 min | medium | Removes refusals / over-cautious behavior at the weight level |
| **Knowledge distillation from 30B/70B** | 8 GB | 2–6 h | medium | Big model teaches small model your domain |
| **Continuous auto-improve (RL / DPO loop)** | 8 GB | ongoing | needs data | Nightly retrain on accepted vs rejected replies |

**Text files (what we already do) are the right first layer** — `SYSTEM_PROMPT`, `dialogs.md`, templates, `shared_memory`, sticky routing — they cost 0 VRAM and survive reboots. Brain edits are layer 2.

## Immediate Wins (Do These Today)

### 1. Ollama modelfile — bake the persona without touching code

```dockerfile
# Modelfile.qubit
FROM huihui_ai/llama3.2-abliterate:3b
SYSTEM """You are Qubit, Tricky's voice assistant. You NEVER forget routing: when the user says route to agent/worker/browser, you honor it for the next 8 turns. You produce professional reports via the generate_report.py helper. You speak directly, no filler."""
PARAMETER temperature 0.4
PARAMETER num_ctx 4096
```

```bash
ollama create qubit-brain -f Modelfile.qubit
# then set intent.local_llm.model: qubit-brain in voice.yaml
```

### 2. Obliteratus — remove the "I'm sorry, I can't" reflex

```bash
pip install obliteratus
# needs the HF model dir — run on the 3B
python -m obliteratus --model huihui_ai/llama3.2-abliterate --output qubit-abliterated
ollama create qubit-abliterated -f Modelfile.abliterated
```

This is literally weight surgery (diff-in-means on refusal vectors) — no prompt can replicate it.

### 3. Unsloth LoRA — teach it YOUR tasks from logs

Collect training data from `events.jsonl` + `shared_memory.jsonl`:

```python
# build training set: 200-500 examples of (instruction → perfect report / routing)
{"instruction": "fix the login bug", "output": "<professional report markdown + 40w summary>"}
{"instruction": "route to agent", "output": "Got it — routing to the agent from now on."}
```

```bash
pip install unsloth
python scripts/finetune_lora.py --data data/qubit_train.jsonl --base huihui_ai/llama3.2-abliterate:3b --iters 200
# produces adapters/qubit-lora/ — merge or load as ollama model
```

## Auto-Improve Loop (Nightly)

```
events.jsonl + shared_memory.jsonl
        │
        ├──> nightly job: extract (user text → agent reply, was it accepted?)
        ├──> DPO dataset: chosen vs rejected
        └──> Unsloth DPO step → new adapter → ollama create qubit-brain-v2
```

```bash
# cron: 03:00 nightly
0 3 * * * ~/qubit-voice-agent/scripts/auto_improve.sh
```

`auto_improve.sh` should: pull last 7 days logs → build DPO pairs (tasks where you said "good" vs re-prompted) → 30-min finetune → swap model if eval improves.

## What We Already Ship (No Brain Edit Needed)

- `scripts/generate_report.py` — the local agent never needs to reason about report format; it just calls the script (works even if the 3B is "retarded").
- `voicebridge/shared_memory.py` — ledger survives reboots + lane switches (agent / worker / browser / local all share it).
- `StickyRouteState` — `route to agent` persists 8 turns / 10 min to disk (`sticky_route.json`), so the tiny brain doesn't need to remember.
- `templates/professional_report.md` — the orchestrator's nudge tells the big-model worker to produce the two-layer report (full + 40w spoken).

## Recommendation For You

**Don't LoRA yet** — finish the deterministic layer (done) and collect 2 weeks of real logs. Then:

1. Today: `Modelfile.qubit` (SYSTEM bake) + verify OpenAI Nova voice toggle.
2. Week 2: run `scripts/finetune_lora.py` on 300+ real examples.
3. Ongoing: `auto_improve.sh` nightly DPO.

The 3B will never be a 30B — but with LoRA + abliteration + deterministic report scripts, it behaves like a much smarter model for YOUR tasks, on this 8 GB GPU.
