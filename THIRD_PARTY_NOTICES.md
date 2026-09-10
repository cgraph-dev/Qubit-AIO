# Third-Party Notices

Required attributions for upstream-derived material shipped in this repository.
Governed by `docs/UPSTREAM_REUSE.md` (pin, license, and decision per record).

## CopilotKit — human-in-the-loop approval semantics

The durable approval store `voicebridge/approvals.py` adapts the tested state
machine from CopilotKit's `use-human-in-the-loop` hook
(MIT © Atai Barkai), reviewed at pinned commit
`e9387e04835545c45744b791aee7c9c03520be31`
(`packages/react-core/src/v2/hooks/use-human-in-the-loop.tsx` and its e2e test).

Adapted behavior: InProgress → Executing → Complete gating; `respond` exposed
only while Executing; settle-exactly-once; abort settles pending work as an
explicit outcome; renderer/listener cleanup.

Local divergence (documented in UPSTREAM_REUSE.md R2): records are durable and
restart-safe via an append-only journal; the upstream hook is in-memory.

MIT License text requirement: include the copyright notice and this permission
statement in distributions:

> Copyright (c) Atai Barkai
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

---

## AG-UI — event types and SSE encoding (Packet 2A)

The typed event contract `voicebridge/events/core.py` adapts AG-UI's
`sdks/python/ag_ui/core/events.py` and `ag_ui/encoder/encoder.py`
(MIT © 2025), reviewed at pinned commit
`821b8c227c6fe7b78de2c8259173dd7cbbdf181b`.

Adapted behavior: EventType strings verbatim for the subset the bridge emits;
snake_case→camelCase wire keys (`messageId`, `toolCallId`, `toolCallName`,
`parentMessageId`, `parentRunId`, `threadId`); central omission of optional
None fields on every serialization path; SSE frame `data: <json>\n\n` with
content type `text/event-stream`; conformance vectors mirrored one-to-one from
the pinned `test_encoder.py`.

Local divergence (documented in UPSTREAM_REUSE.md R1): stdlib-only dict-based
events with explicit fail-closed field specs instead of the pydantic SDK;
chunk/thinking/reasoning families omitted; `RUN_CANCELLED` added locally;
journal-only envelope fields (`seq`, `schema`, correlation IDs) stripped from
AG-UI wire output.

MIT License text requirement: include the copyright notice and this permission
statement in distributions:

> Copyright (c) 2025 AG UI Protocol Contributors
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

---

## OpenSandbox — isolation probe, env policy, and lifecycle semantics (Packet 3B)

The isolated runner `voicebridge/isolated.py` ADAPTS behavior from OpenSandbox
execd (Apache-2.0 © 2026 Alibaba Group Holding Ltd.), reviewed at pinned commit
`6b2023e9b7eb80a940d88e6ae05fcbc0eb0cf23f`:
`components/execd/pkg/isolation/probe.go` (startup Probe + bwrap version parse +
namespace smoke-test argv), `bwrap_linux.go` (findBwrap order, setuid detection),
`config.go` (clearenv + allowlist with secret-shaped deny), and
`specs/sandbox-lifecycle.yml` / `components/execd/pkg/runtime/isolated_session_lifecycle_test.go`
(lifecycle states, terminal reason codes, bounded idempotent stop, reap-on-failed-start).

Local divergence (documented in UPSTREAM_REUSE.md R3): Python stdlib port; no Go
execd/server, seccomp BPF, overlay commit/diff, or native gate FDs; a local
`starting` state and mandatory realpath/symlink-escape rejection are added;
workloads launch inside per-run user-systemd transient units instead of an execd daemon.

Apache License 2.0 requirement: include the license text and copyright notice in
distributions (full text at https://www.apache.org/licenses/LICENSE-2.0):

> Copyright 2026 Alibaba Group Holding Ltd.
>
> Licensed under the Apache License, Version 2.0 (the "License"); you may not
> use this file except in compliance with the License. You may obtain a copy of
> the License at http://www.apache.org/licenses/LICENSE-2.0
>
> Unless required by applicable law or agreed to in writing, software distributed
> under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
> CONDITIONS OF ANY KIND, either express or implied. See the License for the
> specific language governing permissions and limitations under the License.

## Optional local skill snapshots

The repository-local `skills/` folder contains third-party instruction and
reference snapshots only; it is not imported by the runtime. Attribution,
exact commits, selected source paths, and license evidence are maintained in
`skills/SOURCES.md`. The included sources are Anthropic frontend-design
(Apache-2.0), the selected Vercel skills (MIT metadata), UI/UX Pro Max (MIT),
Bencium Controlled UX Designer (MIT), and AccessLint’s selected skills (MIT
declared in its plugin manifest). Retain the upstream terms when sharing these
files. Vercel Web Design Guidelines and the unidentified “Kyosaki” skill were
not included because their exact licensing/source is unresolved.

---

## GENOME — semantic-memory contract (Packet 8M)

The optional semantic-memory adapter `voicebridge/memory/semantic.py` +
`voicebridge/memory/journal.py` ADAPT the tested journal/temporal/explain
contracts from GENOME (Apache-2.0 © 2026 Northtek (FrostByte Digital LLC)),
reviewed at pinned commit `ea76cf924b2382499a158b8c7ad8a815913c962f`:
`genome/journal.py` (append/replay/verify), `genome/memory/temporal.py`
(bi-temporal fact windows, believer-aware invalidation, origin-bound
authority), and `genome/explain.py` (stored-data-only recall explanations).

Adapted behavior: hash-chained append-only journal with monotonic seq and
torn-tail recovery; replay + chain verification; `valid_from`-inclusive /
`valid_until`-exclusive temporal windows with point-in-time recall; believer-
aware invalidation; low-trust facts cannot close higher-trust facts.

Local divergence (documented in docs/UPSTREAM_REUSE.md R23): stdlib-only; no
numpy/sentence-transformers/scikit-learn/rank-bm25/PyTorch; no MCP or FastAPI
server; no graph/entity/postgres layers; journal is written after a successful
mutation (not write-ahead); snapshot hashing excludes wall-clock fields; the
opt-in embedding provider reuses the existing Ollama `nomic-embed-text`
endpoint (no new model artifact). Repairs (2026-09-05): the durable dedup
identity is scope-qualified (`mkey = scope\x00<content>`) so identical facts
store independently per scope; mutation methods enforce origin-bound authority
via a `requested_by` source-trust gate; secrets are rejected at every write
boundary and redacted from history/recall/explain diagnostics; corrupt or
newer runtime state is a typed degraded/newer result, never silently reset.
Rejection #2 repairs (2026-09-05): raw external-origin (web/tool/model/
observed) content is never stored or surfaced — it becomes a bounded
non-recoverable reference; the persisted runtime-state configuration is fully
validated on load and write; state writes surface file-fsync/rename/
directory-fsync failures as typed errors instead of swallowing them.
Rejection #3 repairs (2026-09-05): correct() errors never echo caller text;
belief entity/fact_type are bounded single-token identifiers so prose cannot
be smuggled through them; the embedding provider URL is loopback-only (plain
http to 127.0.0.1/::1/localhost); durability failures at every persistence
boundary surface as typed errors with the previous state preserved.
Rejection #4 repairs (2026-09-05): parent-directory creation failures are typed
SemanticStateError; directory-fsync failure after rename is reported as
durability-UNCERTAIN with an explicit recovery/rollback contract (never
claimed preserved or durable); unknown fields are rejected on write and load.
Concurrency (2026-09-05): write_semantic_state is serialized across processes
by a sidecar flock and writes through per-process unique temp files, so
concurrent writers can never produce partial/corrupt/duplicate state (proved by
a real multi-process test with recovery afterward).

The GENOME NOTICE file, preserved verbatim:

> GENOME
> Copyright 2026 Northtek (FrostByte Digital LLC)
>
> This product includes software developed by Northtek (FrostByte Digital LLC).
>
> Licensed under the Apache License, Version 2.0. See the LICENSE file for details.
> Contact: info@northtek.io

Apache License 2.0 requirement: include the license text and copyright notice
in distributions (full text at https://www.apache.org/licenses/LICENSE-2.0):

> Licensed under the Apache License, Version 2.0 (the "License"); you may not
> use this file except in compliance with the License. You may obtain a copy of
> the License at http://www.apache.org/licenses/LICENSE-2.0
>
> Unless required by applicable law or agreed to in writing, software distributed
> under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
> CONDITIONS OF ANY KIND, either express or implied. See the License for the
> specific language governing permissions and limitations under the License.

---

## Ghost — computer-use capability contract (Packet 8N)

`voicebridge/ghost.py` adapts the typed capability/HTTP contract and browser-
role isolation idea from the Ghost computer-use layer for AI agents
(MIT © 2026 Northtek), reviewed at pinned commit
`00811dfb6fe62415c9dc7cc054a88c6fcf237288`
(`crates/ghost-http/src/main.rs`, `crates/ghost-core/src/system/clipboard.rs`,
`crates/ghost-core/src/capture/screen.rs`, `crates/ghost-browser/src/cdp.rs`,
`crates/ghost-core/src/focus.rs`, `crates/ghost-core/src/input/*`).

Adapted behavior: typed desktop-discovery / launch / window-anchoring /
browser-tab-inspection / click / type / scroll / clipboard / screenshot
capability surface; loopback-only CDP; typed outcome states; verify-then-report
receipts; adapter health states; emergency stop.

Local divergence (documented in UPSTREAM_REUSE.md R24): the adapter is Python
over the existing 8F ActionBroker/ActionGate/ActionRunner; all capabilities are
disabled by default; no Ghost subprocess/HTTP server is launched by this
packet; coordinate-only scripts, foreground input, clipboard reads,
browser/CDP control and account/credential operations are high-impact
capabilities requiring fresh exact-hash confirmation; the shell capability is
hard-disabled (`GHOST_SHELL=off`) by default.

MIT License text requirement: include the copyright notice and this permission
statement in distributions:

> Copyright (c) 2026 Northtek
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

---

## mcp-comet — research-only adapter contract (Packet 8N)

`voicebridge/ghost.py`'s `CometResearchAdapter` wraps the typed research-mode
contract of the mcp-comet MCP server connecting to the Perplexity Comet
browser (MIT © 2025), reviewed at pinned commit
`6f10c86b0991ce95d9f41d972429888a111b86d5`
(`src/comet-ai.ts`, `src/cdp-client.ts`, `src/types.ts`).

Adapted behavior: research-mode-only selection; bounded query submission;
source/page-content extraction bounded and redacted as untrusted evidence;
typed unavailable/disabled states; provider disclosure.

Local divergence (documented in UPSTREAM_REUSE.md R25): the local adapter is a
Python contract boundary over the existing broker; it is disabled by default;
`computer` mode and write-capable tools are disabled until Packet 8O; no Node/
TypeScript runtime, no unauthenticated remote MCP mount, and no live connect
happen in this packet.

MIT License text requirement: include the copyright notice and this permission
statement in distributions:

> Copyright (c) 2025
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

---

## Praxis — verified skill memory contract (Packet 8P)

The optional verified-skill-memory store package `voicebridge/verified_skills/`
(`__init__`, `_models`, `_persist`, `_lifecycle`, `_recall`, `_transfer`,
composed `store`; each file <300 lines; shared `voicebridge/state_journal.py`)
ADAPTS the verified-before-remember lifecycle, negative ("known failure")
records, budgeted top-K recall, deduplication/reinforce, and retention tiers
from Praxis (MIT © 2026 Kristian Baer / Northtek), reviewed at pinned commit
`a84d486c900aa1b438b75cc2e881bfc2013af84c`
(`src/skill.ts`, `src/verify.ts`, `src/negative.ts`, `src/retrieve.ts`,
`src/dedup.ts`, `src/utility.ts`, `src/store.ts`, and their tests).

Adapted behavior: `quarantined → verified` only after an owner-gated
acceptance check passes (fail-closed: no/weak/vacuous test → quarantined,
assertion failure → refuted); first-class negative skills with evidence;
top-K recall under a token budget with a separate negatives slot; verified
near-duplicate reinforces while a quarantined duplicate suppresses without
inflating utility; utility-weighted hot/warm/cold tiers; reinforce-failure
re-checks then quarantines; deterministic hash-chain journal.

Local divergence (documented in docs/UPSTREAM_REUSE.md R26): stdlib-only
Python store; no MCP server, Node runtime, or worker_threads sandbox; no
model-proposed code is ever executed (the acceptance verdict is an explicit
operator/owner decision — there is no self-verification path); task text is
stored as a bounded redacted label plus a content hash, never raw model/page
text; verified skills are advisory recipes and the existing MemoryStore
remains the authoritative fact store.

MIT License text requirement: include the copyright notice and this permission
statement in distributions:

> Copyright (c) 2026 Kristian Baer / Northtek
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

---

## Evolve — offline experiment comparison contract (Packet 8P)

The optional controlled-evaluation store package `voicebridge/experiments/`
(`__init__`, `_stats`, `_models`, `_persist`, `_run`, `_promote`, `_transfer`,
composed `store`; each file <300 lines; shared `voicebridge/state_journal.py`)
ADAPTS the pure statistical comparison and promotion guardrails from Evolve
(Apache-2.0 © 2026 Kristian Baer / Northtek), reviewed at pinned commit
`36404a7ac75080370b5157977e3bbe73979c9527`
(`evolve-core/src/promotion.rs`, `evolve-core/src/agent_config.rs`,
`evolve-storage/migrations/0001_init.sql`, and their tests).

Adapted behavior: per-session signals clamped to [0,1] with explicit=5x
implicit weighting; empty → neutral 0.5; wins/losses binarized at 0.5;
Beta(1+wins,1+losses) Monte Carlo posterior; NeedMoreData below 20 sessions
per arm; Promote only at posterior ≥ 0.95, else Hold; deterministic under a
fixed seed.

Local divergence (documented in docs/UPSTREAM_REUSE.md R27): stdlib-only
Python; no Rust CLI, adapter, HTTP proxy, dashboard, or traffic-roll routing;
no mutation from live sessions; no implicit regex feedback; a promoted
experiment is a stored decision record only and applying it remains a manual,
separately authorized operation. The upstream repo ships no NOTICE file, so
Apache-2.0 §4(d) imposes no separate NOTICE carry-forward beyond this record.

Apache License 2.0 requirement: include the license text and copyright notice
in distributions (full text at https://www.apache.org/licenses/LICENSE-2.0):

> Licensed under the Apache License, Version 2.0 (the "License"); you may not
> use this file except in compliance with the License. You may obtain a copy of
> the License at http://www.apache.org/licenses/LICENSE-2.0
>
> Unless required by applicable law or agreed to in writing, software distributed
> under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
> CONDITIONS OF ANY KIND, either express or implied. See the License for the
> specific language governing permissions and limitations under the License.

## Hermes agent — vendored sanitizer and SQLite primitives (Packet 8T-F)

Qubit ships two verbatim files from NousResearch/hermes-agent
(MIT © 2025 Nous Research), reviewed at pinned commit
`2237be355906fbe6065ce1815711eee52b2d646e` (tag `v2026.9.7`):

- `voicebridge/upstream/hermes_ansi_strip.py` ← `tools/ansi_strip.py`
  (`strip_ansi`, `sanitize_display_text`, `strip_unicode_tags`);
  executes in `voicebridge/hermes/backend.py` and
  `voicebridge/runtime/real_hermes.py` on live agent output.
- `voicebridge/upstream/hermes_sqlite_util.py` ← `hermes_cli/sqlite_util.py`
  (`write_txn`, `add_column_if_missing`); behavior proven by differential
  tests, reserved for non-executescript writes (see UPSTREAM_REUSE.md R31).

MIT License text requirement: include the copyright notice and this permission
statement in distributions (full text in
`voicebridge/upstream/ATTRIBUTION.md`):

> Copyright (c) 2025 Nous Research
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.
