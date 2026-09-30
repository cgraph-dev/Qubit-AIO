# Post-removal crash loop + gateway embedding dependency (Aug 2026)

Two bugs hit back-to-back on 2026-08-21 after the user ordered PersonaPlex
removed. Both produced the SAME journal signature, so they're documented
together.

## Symptom (both cases)

events.jsonl repeats every ~3-4 s:

```
openclaw.discovery      {'url': 'ws://127.0.0.1:18789', ...}
openclaw.gateway.info   {'url': 'ws://127.0.0.1:18789', ...}
openclaw.gateway.connect {'url': 'ws://127.0.0.1:18789', 'version': ...}
openclaw.route          {'route': 'gateway'}
memory.note.saved       {'path': '.../2026-08-21.md'}
```

No `asr.load`, no `tts.load`, no `hotkey.active` — the bridge never completes
startup. The user reports "nothing happens" / "it's not working".

**First diagnostic move: `journalctl --user -u openclaw-voice-bridge.service`
and look for the python traceback.** The events journal shows only the
*successful* parts of each attempt; the failure is in the service log.

## Bug 1: dangling import after deleting the PersonaPlex package

Removal removed `voicebridge/personaplex/` and `voicebridge/asr/personaplex.py`,
but `Orchestrator._make_asr()` still contained:

```python
from .asr.personaplex import PersonaPlexASR
```

→ `ModuleNotFoundError: No module named 'voicebridge.asr.personaplex'` at
startup → systemd `Restart=on-failure` → infinite crash loop.

Why it fooled us: every crash re-ran `start()` → gateway discovery → connect →
memory note → crash, which reads like a *gateway reconnect loop* when you only
watch events.jsonl.

### Removal checklist (use after ANY "remove X" of a code component)

1. `grep -rn '<removed-pkg>' voicebridge/ --include='*.py'` — ALL references,
   including non-obvious ones (`_make_asr`, `cli.py` command registry,
   `config.py` defaults, tests).
2. `grep -rln '<removed-pkg>' tests/` and delete/adjust those tests.
3. `python -m compileall -q voicebridge` + `pytest -q` (tests must pass).
4. **Restart the service and verify startup completed**: expect
   `asr.load`, `tts.load`, `memory.loaded`, `hotkey.active` in events.jsonl and
   `systemctl --user is-active` = active with 0 FAILURE lines in journalctl.
   Do NOT declare done on compile+tests alone.

## Bug 2: gateway memory embeddings need ollama

Same loop signature, different cause. The Qubit gateway (v2026.7.1-2) runs a
memory subsystem that computes embeddings via ollama at 127.0.0.1:11434 with
`nomic-embed-text`. After `systemctl --user stop ollama` (per the user's
"disable all local ollama" directive), the gateway log fills with:

```
memory embeddings retryable error; retrying in 501ms
memory sync failed (watch): Error: fetch failed | connect ECONNREFUSED 127.0.0.1:11434
```

The gateway's memory side keeps failing → the bridge's gateway connection
churns → bridge never finishes startup. This is NOT the voice path: the
embeddings are the EXISTING Qubit infrastructure the user told us to reuse.

Fix (keeps the user's directive intact — no big local models):

```bash
systemctl --user start ollama          # or: sudo systemctl start ollama
ollama pull nomic-embed-text           # 274 MB, CPU-only, tiny
systemctl --user restart openclaw-gateway.service
systemctl --user restart openclaw-voice-bridge.service
```

Big models (qwen3:30b, qwen3-vl:8b, ...) stay listed but UNLOADED — nothing in
the voice path loads them, and `ollama ps` stays empty.

Verify gateway healthy: `tail /tmp/openclaw/openclaw-2026-08-21.log` shows
`⇄ res ✓ ...` request responses and NO `ECONNREFUSED 127.0.0.1:11434` lines.

## Cross-cutting note for Vast.ai box setup (hit on EVERY box, 3×)

The kit's `run_setup_box.sh` reads the Tailscale key from `/root/k1` but NOT the
HF token from `/root/k2` → `setup_personaplex.sh` skips the model download with
"HF_TOKEN is a placeholder". Always ship + run `/tmp/dl_model.sh` (which reads
`/root/k2`) after the setup finishes, before starting the server. If a rented
host never accepts SSH (`Connection closed by ... port`), destroy it and rent a
different one — and read the real `ssh_host`/`ssh_port` from
`vastai show instances` (hosts differ per instance, e.g. ssh1 vs ssh3).
