# Scoped SQLite/FTS5 personal memory (Packet 5B)

Contract adapted from the pinned Mem0 record (docs/UPSTREAM_REUSE.md R8):
add/search/correct/forget/export/purge with normalized-hash deduplication
and per-record provenance. Letta is explicitly NOT used (R9 rejected — no
code exists at the pinned ref; the core/archival tier taxonomy is referenced
as a concept only).

## Store (voicebridge/memory/store.py)
- Tiers: core / working / recall / archive.
- Records: scope, kind, text, norm_hash, confidence, sensitivity, source,
  provenance (source evidence IDs), created/updated/expiry times,
  supersedes linkage, tombstoned, staged flags.
- Append-only `history` table for remember/correct/forget/purge/promote.
- FTS5 full-text search (stdlib), tombstone/expiry/staged-filtered, scoped.
  Deterministic LIKE fallback when FTS5 is unavailable.
- Explicit ops: remember (dedupe via norm-hash, Mem0 "hash match" skip),
  correct (new supersedes old; old tombstoned), forget (soft), purge (hard),
  export (private JSON, 0600), what_do_i_know().
- Staged inferred memories: `stage_inferred()` writes candidates with
  supporting source IDs; NEVER auto-promoted; `promote()` is explicit and
  refuses secret-shaped candidates.
- Secret rejection at write boundaries (api-key/token/secret/password shapes
  and card-like digit runs).
- Private file modes: DB + exports 0600; parent dir 0700. WAL + a write lock
  for concurrency; PRAGMA user_version migrations (idempotent reopen).

## Tests (tests/test_packet5b.py, 23)
add/search/dedupe vector, tiers, scope isolation, expiry, tombstone,
supersede/correct-outranks, forget/purge, export modes, history
append-only, restart persistence, concurrent writes (40 rows, no errors),
migration idempotence, private file modes, FTS5-only mode + fallback,
secret rejection (write + promote), staged non-promotion until explicit,
Letta/mem0 import-forbidden.
