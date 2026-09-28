# Bounded context compiler & retrieval safety (Packet 5C)

voicebridge/context/compiler.py — deterministic-precedence compilation with
retrieval from the Packet 5B FTS5 memory store plus optional bounded
evidence reads. Reuses the Packet 4B budget engine
(voicebridge/context_compiler.py) so budgets/reserve math is unchanged.

## Precedence (dropped LAST → FIRST)
persona (immutable, never truncated) → tools (immutable) → active (hard
clip) → core (corrected durable facts) → recall (FTS5 top-k) → evidence
(parallel bounded reads). persona+tools+output reserve must fit the model
context or compile raises ContextBudgetError (typed) — required
instructions never silently truncated.

## Retrieval safety
- Recall: MemoryStore.search (5B) filters tombstoned/staged/expired;
  corrections already tombstone the stale record, so corrected facts win
  by construction.
- Conflict labels: deterministic same-scope + same-topic detection;
  conflicts surfaced in the report, never silently resolved.
- Evidence: optional evidence_provider; partial/whole failures recorded in
  report.failures and never block required sections.
- Parallel reads: ThreadPoolExecutor with per-source + total deadline
  (bounded; deadline exceeded futures dropped), order preserved.
- Cache TTL: query-keyed cache of REDACTED excerpts only; secrets never
  cached; expiry forces refetch.
- Redaction: every excerpt is the record's redacted_excerpt (5A contract);
  compiler adds nothing raw.

## Injection resistance
persona/tools/active are fixed inputs rendered verbatim OUTSIDE reference
blocks. Retrieved text is appended ONLY inside [reference]/[untrusted
evidence] blocks with citations — it cannot alter policy, tools, routing,
persona, or durable memory (the compiler only reads the store).

## Observability (no content leakage)
CompilationReport: sections included + per-section token estimates, omitted
item reasons, failures, conflicts, cache hits, retrieval ms, citation
count. IDs/counts only — never raw text.

## Optional semantic reranking
Interface + flag exist (rerank_enabled / rerank_fn) but DEFAULT OFF — Packet
4C's L1 was never approved, so reranking stays disabled pending an approved
reranker; the identity path is the FTS5 order.

## Tests (tests/test_packet5c.py, 20)
budget/reserve survival, required-section immutability, budget error,
corrections-win, low-score omission metadata, tombstone exclusion, conflict
labels, cross-scope isolation, citations, partial-failure visibility, cache
TTL hit/expiry, injection-cannot-change-policy/tools/persona/routing/memory,
rerank off-by-default + enabled-reorder, parallel order/partials/deadline,
FTS5 fallback, and "no new database" source assertion.

## Live integration (authorized packet, 2026-08-25)
The 5C compiler is now WIRED into the live chat path:
`voicebridge/local_llm._user_content` (-> _chat_payload -> chat_local). Every
chat turn runs the bounded compiler with FTS5 recall (5B store), core facts,
optional bounded evidence, citations and content-free observability. The
persona/tools remain in the immutable system message (rendered separately,
so retrieved text can never change policy/tools/persona/routing).

Flags (intent.compiler):
- mode: "5c" (default) | "4b" (rollback to the Packet 4B compiler).
- evidence_sources: [] (optional 5A read-only adapter names, bounded).
- observe: true (appends a content-free [ctx: ms=… omitted=… ev_fail=…]
  trailer and logs when retrieval is partial).

Store location: <state_dir>/memory/memory.db (private 0700 dir) (env VOICE_BRIDGE_MEMORY_DB overrides
for tests; tests/conftest.py isolates pytest from real user state). Q5 canary
remains disabled by default.

Live verification (tests/test_packet5c_integration.py, 5 tests, skipif no
Ollama): real chat_local turn with captured payload proves retrieved facts +
citations appear, corrected fact wins over stale, tombstoned excluded, budget
bounded, partial failures visible via trailer, and hostile retrieved text
cannot alter system persona, policy footer, or introduce execution authority.
Rollback unit test proves mode=4b restores the exact 4B output shape.
