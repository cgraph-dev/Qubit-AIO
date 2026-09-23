# Evidence & adapter contracts (Packet 5A)

One normalized read-only evidence record for every source:
`voicebridge/evidence/models.py` (EvidenceRecord, AdapterContract,
Scope/Sensitivity/Trust enums) and `voicebridge/evidence/registry.py`
(EvidenceRegistry) with a concrete adapter set in
`voicebridge/evidence/adapters.py` wired by `build_evidence_registry()`.

## EvidenceRecord fields
evidence_id (stable, deterministic from adapter|scope|source_ref|hash),
adapter + adapter_version, source_ref, scope, sensitivity, trust,
captured_at_epoch, freshness_ttl_s, permission ("read"), content_hash
(sha256 of source text), redacted_excerpt (secret patterns stripped, ≤2000
chars; raw text is NEVER stored), confidence, citations, meta. Immutable.

## AdapterContract / separation
Every adapter DECLARES permissions (subset of read/write/remember), scope,
sensitivity, trust, freshness and a citation policy. 5A ships READ-ONLY
adapters only; the registry fails closed if a read_only adapter declares
write/remember or exposes a write-like member. A non-read-only path (future
5B+) can never be reached through the read fetch() (permission error).

## Adapters
files (pathlib read, escape-blocked, size-bounded), repos (read-only git
log/status), web (bounded HTTP, SSRF/private-network blocked, host
allowlist), reports (manifest scan, typed partial on bad JSON), browser /
inbox / worker / memory (declarative placeholders; real implementations
land in 8B / 5B and keep the same contract).

## Failure semantics
All failures are typed: EvidenceError, EvidencePermissionError,
EvidenceUnavailableError, EvidencePartialError. Partial results never
silently truncate — they raise typed partial with reason.

## Tests
tests/test_packet5a.py (17): contract validation, registry enforcement,
stable ID/hash determinism, redaction-only-excerpt, freshness, adapter
behaviour incl. path-escape/SSRF/oversize/partial, canary inertness,
canary required fields, rollback flip.
