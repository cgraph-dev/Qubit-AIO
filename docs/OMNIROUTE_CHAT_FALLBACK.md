# OmniRoute /chat fallback — diagnosis, fix, verification, rollback

Date: 2026-10-11. Operator: key wiring for billing/rate-limit failover in the
interactive `/chat` terminal. Voice-bridge turns are unaffected by all of this.

## 1. Symptom

In `/chat`, a billing (402) or rate-limit (429) error on the primary provider
ended the turn. It never switched to OmniRoute (`http://127.0.0.1:20128`),
even though the live config names it as fallback.

## 2. Root cause (verified read-only before any change)

- Live `~/.qubit/config.yaml` DOES wire the fallback:
  `providers.omniroute` (`base_url http://127.0.0.1:20128/v1`,
  `key_env: OMNIROUTE_API_KEY`, `default_model: auto`) plus
  `fallback_providers: [{provider: omniroute, model: auto, …}]`.
- But `OMNIROUTE_API_KEY` existed NOWHERE Qubit reads secrets (no
  `~/.qubit/.env`, nothing in `legacy-hermes-secrets/.env`, not in process
  env; `~/.omniroute/.env` is the gateway's own env and is never read by Qubit).
- Fallback activation therefore skipped the entry every time:
  `qubit_runtime/qubit_cli/fallback_config.py:15` (`resolve_entry_api_key`
  → `get_secret` → `None`) → `qubit_runtime/agent/route_binding.py:40`
  (`bind_route_entry` → client `None` → return `None`) →
  `qubit_runtime/agent/chat_completion_helpers.py:2094`
  (`"Fallback to %s failed: provider not configured"` → next → chain exhausted).
- Contributing facts: the planned setup module
  (`voicebridge/omniroute_setup.py`, `setup run --steps omniroute`) exists only
  as `docs/superpowers/plans/2026-10-11-omniroute-integration.md` — never
  implemented — so the key-store step never ran. The pre-existing dashboard key
  `qubit-voicebridge` has `allowKeyReveal: false`, so its value was unrecoverable;
  minting a fresh dedicated key was the clean path.
- Out of scope by owner call: the voicebridge headless worker
  (`voicebridge/providers/gateway.py:849-883`) has NO `fallback_providers`
  support at all (402 → `PROVIDER_OUTAGE`, 429 → `RATE_LIMITED` + retry only).
  Voice turns neither needed nor received this fix.

## 3. Fix applied (secret only — zero config/code changes)

1. Backed up config: `~/.qubit/config.yaml.bak-20261011-omniroute`
   (verified byte-identical afterwards — `config.yaml` untouched).
2. Minted a dedicated gateway API key named `qubit-chat-fallback`
   (id `53b90703-…`, `modelAccessMode: all`, `allowAutoCombos: true`) via
   `omniroute api api-keys post-api-keys`. The pre-existing
   `qubit-voicebridge` dashboard key was left untouched.
3. Stored it Qubit-side through the canonical seam
   (`qubit_cli.config.save_env_value("OMNIROUTE_API_KEY", …)` → new
   `~/.qubit/.env`, mode `0600`). Key value never printed, logged, or committed.
4. Cleaned up: 3 orphan keys minted during probing
   (1 unstored `qubit-chat-fallback`, 2 `qubit-chat-fallback-probe`) deleted via
   `DELETE /api/keys/<id>` (all `200`). Panel now holds exactly 2 keys:
   `qubit-voicebridge` (pre-existing) + `qubit-chat-fallback` (this fix).
5. No repo files edited for this fix (`git status` shows only unrelated
   pre-existing worktree modifications). Voice bridge, `/chat` slots, and the
   Providers dashboard panel are structurally unchanged — the only new state is
   one secret line plus one dashboard key.

## 4. Verification evidence (all live, secret never echoed)

- `resolve_entry_api_key({provider: omniroute, model: auto, …})` → set (`True`;
  was `None` before the fix).
- `GET /v1/models` with the stored key → `200`, 927 models visible
  (was `401` without a key).
- `POST /v1/chat/completions` `{model: auto, max_tokens: 8}` with the stored
  key → `http=200`, served by `pplx-auto` (proves the fallback target serves).
- `get_fallback_chain(live config.yaml)` → `[('omniroute', 'auto')]`.
- Next `/chat` turn loads the chain: expect
  `🔄 Fallback model: auto (omniroute)` at init, and on the next billing /
  rate-limit failure `⚠️ Model fallback: … using auto via omniroute.`
  IMPORTANT: a `/chat` process started BEFORE the key was stored (14:07 EEST
  2026-10-11) holds a stale environment and will keep failing the same way.
  Either run `/reload` inside that session (re-reads `~/.qubit/.env` into the
  process via `qubit_cli.config.reload_env`) or restart `/chat`, THEN retry.
  If it still doesn't switch, check `qubit logs --level warning` for
  `Fallback to omniroute failed` (key resolution) — the exact skip reason is logged.
- Session poisoning gotcha (hit live 2026-10-11): when the walk runs while the
  key is still unresolvable, the entry `('omniroute','auto',
  'http://127.0.0.1:20128/v1')` is added to the agent's in-memory
  `_unavailable_fallback_keys`, which is NEVER cleared for the life of that
  agent (`chat_completion_helpers.py:1964-1966`, only initialized at
  `:2087`). `/reload` fixes the environment but cannot un-poison the skip set
  (nor reset an exhausted `_fallback_index`). After storing the key you MUST
  start a fresh session / restart `/chat` — retrying in the same poisoned
  session skips OmniRoute silently (debug log only) and prints the plain
  billing block again.

## 5. Rollback / switch-back (anytime, no restart of voice bridge needed)

Full revert (2 steps, either order):
1. Qubit side — forget the key (canonical remover, same seam as the writer):
   `qubit config unset OMNIROUTE_API_KEY`
   (removes the line from `~/.qubit/.env`; start a fresh `/chat` turn after).
2. Gateway side — delete the key: OmniRoute dashboard → API Keys →
   `qubit-chat-fallback` → delete (or `DELETE /api/keys/53b90703-…`).
   Result: panel back to exactly the 1 pre-existing key.

Restore point: `~/.qubit/config.yaml.bak-20261011-omniroute`
(only needed if `config.yaml` was ever touched afterwards — it wasn't).

Temporary disable WITHOUT deleting (keeps the key for instant re-enable):
comment out / empty the `fallback_providers:` list in `~/.qubit/config.yaml`
(backed up above), fresh `/chat` turn to pick it up; uncomment to switch back.

## 6. Known limits (not changed)

- Billing 402 on the PRIMARY still benches that credential (~1h for single-key
  setups); fallback fires, primary stays benched — by design.
- A `Retry-After`-carrying error can defer the switch
  (`switch_deferred_by_reset`); a repeated failure walks the chain on the next attempt.
- Voice-bridge turns never consult this chain (see §2); a voice-side failover to
  OmniRoute would be a new feature, not a config change.

## 7. Dashboard-chat wiring fix (code, 2026-10-11 ~14:30 EEST)

Follow-up finding: the failing surface was the voicebridge dashboard
`/qubit-chat`, not the terminal. No `qubit` CLI process was running; the stack
is `voicebridge run` + `gateway_daemon`. That path builds the vendored
`AIAgent` in `voicebridge/agent.py::_run_vendored_session` WITHOUT passing
`fallback_model`, so `_init_fallback_chain` always normalized to `[]` and the
`~/.qubit/config.yaml` chain (plus the stored key) was never consulted — a
fresh session and `/reload` could not help.

Fix (one fail-open hunk, same pattern the CLI uses in
`qubit_cli/oneshot.py:590`): read `get_fallback_chain(vcfg)` from the
already-loaded vendored config and pass `fallback_model=_fb_chain` to
`AIAgent`. Empty chain → `None` → byte-identical to previous behavior, so
installs without `fallback_providers` are unaffected and the Providers
dashboard panel is untouched (no frontend changes).

Verified (focused only, no suites): `py_compile` clean;
`get_fallback_chain(live config)` → `[('omniroute','auto')]` normalizes
through the real `_fallback_entries`; empty config → `None`.

## 8. True root cause: wrong home (found live 2026-10-11 ~15:00 EEST)

The 14:58 turn log (`runtime/logs/agent.log`) settled it:
`Error classified: reason=billing status=402 … fallback=True`, then straight
to `Non-retryable client error` with NO `trying fallback…` and NO
`Fallback to omniroute failed` — i.e. an EMPTY chain, not a bind failure.
A second witness on the aux path: `no fallback_chain / fallback_providers
is configured`.

Cause: the serving stack reads its config from the voicebridge runtime home
`/home/trick/.local/state/qubit-voicebridge/runtime/config.yaml` (via
`voicebridge/qubit_runtime.py:use_qubit_home`), whose line 6 was
`fallback_providers: []` with no `providers.omniroute`. The
`~/.qubit/config.yaml` chain fixed in §3-§5 is read by NOBODY in this stack
(no `qubit` CLI process runs; only `voicebridge run` + `gateway_daemon`).

Additional finding: the runtime home's own `.env` ALREADY held a valid
`OMNIROUTE_API_KEY` (auth `200` against `/v1/models`), so no secret work was
needed there — only the chain was missing.

Fix (config only, backed up to
`runtime/config.yaml.bak-20261011-omniroute`): mirrored the
`providers.omniroute` block + one `fallback_providers` entry into the runtime
config. Verified: `get_fallback_chain` → `[('omniroute','auto')]`,
normalizes clean, key resolves `True` under a scope built exactly the way
the server builds it (`build_profile_secret_scope`). No restart required:
`_run_vendored_session` runs `load_config()` and a fresh scope per turn, and
builds a fresh agent per turn. Retry in the dashboard chat directly.

Rollback: restore the backup file (one `cp`), next turn is back to old behavior.

## 9. Pin: `auto` routes to a signup wall (2026-10-11 ~15:20 EEST)

With the chain live, the 402 disappeared but the fallback answered
`Sign up and repeat your request.` — OmniRoute `model: auto` had picked an
upstream that returns a signup wall as HTTP 200 text (no further failover can
see it; same signature as the direct `pplx-auto` probe). Probes against
`/v1/chat/completions` (`reply with the single word ok`, `max_tokens: 16`):
`duckduckgo-web/gpt-5.4-mini` → `ok`, `cloudflare-playground/auto` → `ok`;
`lmarena/auto` → 500, `opencode/big-pickle` → 403, `zcode/glm-5.3-flash` →
502, `auggie/sonnet4.6` → 502, `uncloseai/…` → 404 (id form).

Fix: pinned the fallback entry to `model: duckduckgo-web/gpt-5.4-mini`
(provider stays `omniroute`) in BOTH homes' configs (runtime + `~/.qubit`,
kept identical). Verified chain normalizes in both. Re-pin or revert to
`auto` by editing the one `model:` line; per-turn config load means no
restart.

## 10. DDG conversation-limit → two-entry chain (~15:32 EEST)
Pinned DDG hit `HTTP 429: DuckDuckGo AI Chat error: ERR_CONVERSATION_LIMIT`
on all 3 attempts — the agent retried the single fallback and gave up with
`omniroute rate-limited every one of 3 attempts`. Fix: `fallback_providers`
is a LIST, so the chain is now two entries —
`cloudflare-playground/auto` first (also probed `ok`), `duckduckgo-web`
second. A 429 on one walks to the next; both verified in both homes' configs.
No restart (per-turn load).

## 11. Full loop state (~15:40 EEST)

Failover leg PROVEN in logs (15:34 turn `20261011_153411_caa0bc`):
`reason=billing status=402 fallback=True` →
`using fallback cloudflare-playground/auto via omniroute` →
`Turn ended: reason=text_response model=cloudflare-playground/auto`,
`tui turn finished: status=complete error_retained=False`. Second turn stayed
on the fallback (`history=2`, complete). The GLM self-identification in the
reply is the upstream model's own persona, not a routing fault.
Pool leg: `opencode-go` pool reports available NOW (bench cleared), so new
turns start on the primary again — a fresh 402 re-walks the same proven
chain. Voicebridge builds a fresh agent per turn, so there is no stale
fallback state to clear.

## 12. Cron jobs — verification + chain reorder (2026-10-12)

Owner ask: do scheduled cron jobs also fail over to OmniRoute on a
rate-limit/billing failure on the primary provider?

### 12.1 Finding — cron already shares the fallback (no code change needed)

The cron execution path is NOT a bypass. Traced end to end:

- The 3 live jobs (`auto-push repos`, `Upstream drift check (6h)`, `Obsidian
  vault memory sync`) are all **unpinned** — no per-job `provider`/`model`/
  `base_url` (verified in `runtime/cron/jobs.json`).
- `cron/scheduler.py::_job_fallback_chain` (L94) →
  `scoped_fallback_chain(get_fallback_chain(cfg), pinned=_job_route_pinned(job))`.
  Unpinned → inherits the global chain; **pinned → `None`** (by design: an
  explicit pin never borrows the chain).
- Two consumers of that chain, both fed the same chain:
  1. **Resolve-time** (`_resolve_job_runtime`, L1784): an `AuthError` /
     transient-net failure walking the chain before the agent is built.
  2. **Mid-run** (`_resolve_cron_agent_setup` L2547 sets
     `setup.fallback_model`, passed to `AIAgent(fallback_model=…)` at
     `_construct_cron_agent` L2576) → `_init_fallback_chain` →
     `agent._fallback_chain` → `try_activate_fallback` on 402/429. Same seam
     `/chat` uses.
- **Subprocess worker env** (`_launch_external_cron_worker`): the daemon's own
  env does NOT carry `OMNIROUTE_API_KEY` (only `QUBIT_HOME` is set). But the
  worker re-reads it itself — `_reload_dotenv_and_publish_delivery_target`
  (run_job L2662) + `hydrate_profile_secret_sources` resolve the key from
  `runtime/.env` into the profile scope. Proven with `env -i` (no key
  inherited): scope has the key, and both chain entries are reachable.

### 12.2 Defect found + fixed — chain entry 1 was dead

`get_fallback_chain` resolved fine and the key was valid (35-char `sk-f…`,
`/v1/models` → 200), but at probe time the FIRST chain entry
`cloudflare-playground/auto` returned **HTTP 502** — Playwright browser binary
missing on the gateway (`browserType.launch: Executable doesn't exist`).
`duckduckgo-web/gpt-5.4-mini` served **200** (2/2 probes). The chain walks on
failure, so cron still landed on the working entry — but only after a wasted
attempt.

Fix (config only, both homes kept identical, backups
`config.yaml.bak-omniroute-reorder`): reordered `fallback_providers` so the
working `duckduckgo-web/gpt-5.4-mini` is entry 1, `cloudflare-playground/auto`
entry 2. `qubit config check` → `state: OK`.

### 12.3 Verification evidence (focused, no suites, no provider credit)

- `_job_fallback_chain(unpinned)` → both omniroute entries in resolution
  order; `resolve_entry_api_key` → set for both; `_job_fallback_chain(pinned)`
  → `None` (control).
- **Real AIAgent**, built via cron's own `_construct_cron_agent` (primary
  pointed at an unreachable stub so the first call's failure is classified, no
  completion spent): `FailoverReason.billing` → `try_activate_fallback` True →
  routes to `omniroute / duckduckgo-web/gpt-5.4-mini @ http://127.0.0.1:20128/v1/`.
- `FailoverReason.rate_limit` (429) → activates the same way.
- End-to-end: the bound fallback client returns a real completion
  (`chat.completions.create` → `'ok'`) via the gateway.
- Cold-worker sim (`env -i`, no inherited key): dotenv+hydrate restores the
  key; entry 1 = 200.

### 12.4 Caveats

- `cloudflare-playground/auto` will 502 until the gateway's browser binary is
  installed; kept as a spare entry (harmless — 502 classifies and the walk
  continues to entry 2). Fix the gateway browser, or drop the entry.
- `cloudflare-playground/auto` is no longer needed for liveness; the chain is
  healthy on DDG alone.
- A job with an explicit `provider`/`model`/`base_url` pin gets NO chain
  (unchanged, by design); `qubit cron edit <id> --unpin` restores liveness.
- Failure is surfaced in the delivered cron report (the `_fallback_notice`
  rides with the output), so an operator sees the switch.
- Rollback: restore either `config.yaml.bak-omniroute-reorder` (one `cp`), or
  re-reorder the two `model:` lines.
