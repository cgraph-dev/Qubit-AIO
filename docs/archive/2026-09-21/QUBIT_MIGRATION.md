# Qubit Migration Compatibility

Retired assistant lanes (`openclaw`, `hermes` config sections, spoken
aliases, journal routes, and session providers) keep working through
documented compatibility mappings. Nothing here launches, installs, or
requires a foreign assistant; every mapping resolves to the native Qubit
lane.

## Config sections

`openclaw.*` and `hermes.*` keys in an existing `voice.yaml` fold into
the unified `agent` section at load (`voicebridge/config.py`
`_migrate_legacy_sections`): `model`, `session_prefix`, `skills_nudge`,
`task_timeout_s`, `max_history_chars`, `progress_updates`,
`progress_min_interval_s`, `enabled`. Explicit `agent.*` keys always win;
legacy sections are dropped after folding, so exactly one configuration
tree remains at runtime.

## Spoken aliases

"ask Hermes to …", "route to openclaw", "use Hermes", and similar
phrasings classify to the `agent` intent / `worker` sticky lane
(`voicebridge/intent/classifier.py`). The assistant answers as Qubit;
legacy names never appear in replies, receipts, or the dashboard.

## Journal and session history

Old journal rows, session files, and sticky-route records may carry
retired route/provider names (`openclaw`, `hermes`, `claw`). Readers
(`voicebridge/webui.py` feed mapping, `shared_memory`, intent state)
treat them as opaque labels and render Qubit lane names. History is
never rewritten.

## Legacy file locations

`openclaw-voice-bridge` / `qubit-voice-agent` config, state, and data
paths resolve read-only when the canonical `qubit-voicebridge` tree is
absent (`LEGACY_NAME`, `detect_legacy_configs`); writes always target
the canonical tree. The legacy `~/.hermes/.env` key file is honored
only when the canonical env file is absent, with a one-time migration
warning. Product migration commands (`qubit product migrate`,
`migrate-rollback`) move user data with backup and rollback.

## Retired product code (provenance)

Previously vendored upstream files and the supervision stack were
removed (see `voicebridge/upstream/ATTRIBUTION.md` and
`docs/UPSTREAM_REUSE.md` R33). No migration path reinstalls them; the
Qubit-native replacements (`voicebridge/textutil.py`,
`voicebridge/sqlstore.py`, `voicebridge/agent.py`) are the only
supported surface.
