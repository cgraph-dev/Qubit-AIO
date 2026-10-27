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

## HarnessRouter — harness console styles and structure

The `/harness-router` console (`frontend/qubit-web/src/styles/upstream/harness.css`
plus the matching JSX structure in `frontend/qubit-web/src/pages/HarnessRouterPage.tsx`)
ports selectors verbatim from HarnessRouter/harnessrouter (Apache-2.0),
reviewed at pinned commit `f3192445c82da533fe83b125917efb8c3947aef0` (2026-10-02):
the Agent harnesses console in `ui/src/app/v2.css` (`hx-*` panel/header/index-table
rules, `ak-*` sheet/dialog rules) and the skeleton shimmer in `ui/src/app/revamp.css`
(`.sk`). Color/font tokens are remapped to the Qubit theme and upstream product
marks are not carried over. No upstream backend code is used — all data flows
over the existing Qubit family bridge.

Apache License 2.0 requirement: include the license text and copyright notice in
distributions (full text at https://www.apache.org/licenses/LICENSE-2.0):

> Copyright (c) 2026 HarnessRouter
>
> Licensed under the Apache License, Version 2.0 (the "License"); you may not
> use this file except in compliance with the License. You may obtain a copy of
> the License at http://www.apache.org/licenses/LICENSE-2.0
>
> Unless required by applicable law or agreed to in writing, software distributed
> under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
> CONDITIONS OF ANY KIND, either express or implied. See the License for the
> specific language governing permissions and limitations under the License.

## OpenClaw — console styles and structure (workboard, sessions, cron, devices, debug, skills)

The non-Hermes overlay pages (`frontend/qubit-web/src/styles/upstream/workboard.css`,
`upstream-sessions.css`, `upstream-cron.css`, `upstream-devices.css`, `upstream-chrome.css`,
`upstream-compat.css`, plus matching JSX structure in the Task Inbox, Board Admin,
Session Ops, Nodes, Devices, Scheduler, Taskflows, Worktrees, Checkpoints, Memory
Backends, Canvas, MCP Calls, and Skills Review pages) port selectors from
openclaw/openclaw (MIT), reviewed at pinned commit
`7ff3b3be3713751de356800167d67ce752ad0d04` (2026-10-02):
`extensions/workboard/browser/{pages/workboard,widgets,styles}`,
`ui/src/pages/{sessions,devices,cron,worktrees,config,debug,skill-workshop,skills}`,
`ui/src/styles`, `extensions/{memory-lancedb,memory-wiki,active-memory,canvas}`,
`docs/automation`. Color/font tokens are remapped to the Qubit theme and upstream
product marks are not carried over. No upstream backend code is used — all data flows
over the existing Qubit bridges.

MIT License text requirement (copyright notice retained):

> Copyright (c) 2026 OpenClaw Foundation
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

## OpenMuse — mobile-chat UX patterns (bot + webchat)

The `/qubit-bot` and `/webchat` pages (`frontend/qubit-web/src/styles/upstream/botchat.css`
plus matching JSX structure) adapt chat UX patterns from CopilotKit/openmuse (MIT),
reviewed at pinned commit `9ec439fbaa878197d9d44c2aa982cca55676dd68` (2026-10-02):
`apps/mobile/src/{chat,threads,agent-ui,assistant-response,conversation-queue,conversation-run,screens,ui}`.
No verbatim upstream code is shipped (reimplemented for web in the Qubit theme);
all turns run over the existing Qubit chat/SSE transport.

MIT License text requirement (copyright notice retained):

> Copyright (c) 2026 OpenMuse contributors
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

## OpenMuse — Goals, Monitors and Ideas domain (agent surfaces)

The Qubit agent surfaces `Goals`, `Monitors` (page watches) and `Ideas` port the
domain model and service behaviour of CopilotKit/openmuse (MIT) at pinned commit
`b06caad7005ac5b6d2b451752a3794a6ae1759c1` (2026-10-04):

- `packages/domain/src/agent.ts` — the `Goal` (line 57), `Monitor` (line 66) and
  `Idea` (line 82) interfaces, their zod input schemas (`monitorInputSchema`,
  `goalInputSchema`), and the `AgentTask["kind"]` enum that includes `monitor`
  and `finance`.
- `apps/server/src/engine/routes.ts` — the `POST /goals`, `POST /goals/:id`,
  `POST /monitors`, `POST /monitors/:id/control`, `POST /ideas/refresh` and
  `POST /ideas/:id` route shapes.
- `apps/server/src/engine/service.ts` — the observable behaviour of
  `createGoal`/`updateGoal` (incl. pause-cascades-to-linked-tasks),
  `createMonitor`/`controlMonitor` (incl. URL safety rejection and the stopped
  monitor restart rule), and `refreshIdeas`/`decideIdea` (idempotent derivation,
  stable-hash ids, retire-obsolete, compare-and-swap on status).

No verbatim upstream code is shipped. Field names, defaults, validation bounds,
state transitions and error semantics are reproduced so the surfaces behave 1:1;
the storage is Qubit's own (existing stores, no new daemons). Where upstream
derives ideas from a mail store Qubit does not have, the derivation reads Qubit's
real stores instead — same rules and evidence shape, no fabricated rows.

MIT License text requirement (copyright notice retained):

> Copyright (c) 2026 OpenMuse contributors
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

## Hermes agent — retired vendoring (native replacements shipped)

Qubit previously shipped two verbatim files from NousResearch/hermes-agent
(MIT © 2025 Nous Research), reviewed at pinned commit
`2237be355906fbe6065ce1815711eee52b2d646e` (tag `v2026.9.7`):

- `voicebridge/upstream/hermes_ansi_strip.py` ← `tools/ansi_strip.py`
- `voicebridge/upstream/hermes_sqlite_util.py` ← `hermes_cli/sqlite_util.py`

These files have been REMOVED and replaced by independent Qubit
implementations (`voicebridge/textutil.py`, `voicebridge/sqlstore.py`)
with differential tests against the pinned oracle plus adversarial
tests. The MIT attribution is
retained here and in `voicebridge/upstream/ATTRIBUTION.md` as the
legally required provenance record for the retired vendoring.

MIT License text requirement (retained provenance record):

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

## Hermes agent dashboard — vendored baseline (owner amendment 2026-09-11)

`frontend/qubit-web/` (191 files from upstream `web/`) plus
`frontend/qubit-shared/` (18 files from upstream `qubit-shared`) is a
source-level Qubit rebrand of NousResearch/hermes-agent (MIT © 2025 Nous
Research) at pinned commit `2237be355906fbe6065ce1815711eee52b2d646e` (tag
`v2026.9.7`), authorized by `docs/archive/2026-09-21/QUBIT_OWNER_AMENDMENT_2026-09-11.md` and
recorded in `docs/UPSTREAM_REUSE.md` (R35) with per-file upstream/local hashes,
patch reasons, and affected regression tests in
`docs/HERMES_SOURCE_COMPARISON.json` (unchanged files remain byte-identical;
rebranded files carry an explicit patch record). Qubit integration, branding,
and OpenClaw additions live in `frontend/qubit-app/` (overlay) and
`voicebridge/qubit_compat.py`; no vendored file is edited without a recorded
patch entry.
`LICENSE` (Qubit school-project terms) does not apply to these paths.

MIT License text requirement (vendored dashboard provenance record):

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

---

## Agent-Quest — office2 concept surface (authored, no code vendored)

The `/qubit-office2` page is an **AUTHORED Qubit surface** implementing the
Agent-Quest concept from FulAppiOS/Agent-Quest (MIT © 2026 Fulvio Schilone),
reviewed at `main`. Upstream is a **Bun server + client application** (root
`bin/ client/ server/ scripts/ docs/` with `package.json`, `bun.lock`,
`install.sh`) — not a static Vite build. **No upstream Agent-Quest source code
is currently vendored in this repository**; the office2 page is Qubit's own
implementation of the concept over existing Qubit bridges, so only the
attribution below is required today.

MIT License text requirement: include the copyright notice and this permission
statement in distributions:

> Copyright (c) 2026 Fulvio Schilone
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

---

## starnet — office3 design reimplementation (no code copied)

The `/qubit-office3` page is a Qubit-authored **REIMPLEMENTATION** of the
published design document `docs/VOICE_CHAT_PLAN.md` from androoAGI/starnet
(MIT © 2026 Andrew Sims), reviewed on branch `feat/harness-backend`. The
upstream document is a **design specification**; **no upstream starnet source
code was copied** into this repository, so only the attribution below is
required today.

MIT License text requirement: include the copyright notice and this permission
statement in distributions:

> Copyright (c) 2026 Andrew Sims
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

## OpenJarvis skill source resolvers — Apache-2.0

Adapted from [open-jarvis/OpenJarvis](https://github.com/open-jarvis/OpenJarvis)
at commit `05839014127772bd077ba9ba2a2393a007f63ac6` (Apache License 2.0;
the upstream `LICENSE` carries the full text and copyright).

- Reference sources: `src/openjarvis/skills/sources/base.py`, `hermes.py`,
  `openclaw.py` — clone/pull cache, skill walk, frontmatter preview, commit
  pin, stale-cache refresh fallback, OpenClaw `_meta.json` sidecar.
- Local adaptation: `qubit_runtime/tools/skills_hub_git_sources.py` and
  `qubit_runtime/tests/tools/test_skills_hub_git_sources.py`. Qubit's hub
  (auth, cache, trust, SSRF guards, bundle validation, installer) is not
  replaced; only the resolver semantics were ported.
- Test scenarios adapted from OpenJarvis `0583901`, Apache-2.0:
  `tests/skills/test_sources.py` and `tests/skills/test_source_refresh_fallback.py`.
  Only the resolver-level scenarios that Qubit implements are reproduced
  (layout walk, frontmatter preview, commit pin, stale-cache fallback and
  warning lifecycle); the importer, trust-gate, telemetry and CLI-display
  tests were not ported because those layers are Qubit's own.

## OpenJarvis out-of-process engine adapter — Apache-2.0

The optional `openjarvis` RuntimePort backend
(`qubit_runtime/agent/runtime_openjarvis.py`) drives
[open-jarvis/OpenJarvis](https://github.com/open-jarvis/OpenJarvis) at commit
`05839014127772bd077ba9ba2a2393a007f63ac6` (Apache License 2.0; the upstream
`LICENSE` carries the full text and copyright). **No OpenJarvis source is
copied or imported into the Qubit process**; the adapter spawns the pinned
`jarvis ask --json` CLI and maps its result onto Qubit's port events.

- Reference interface: `src/openjarvis/cli/ask.py` (`ask` options and JSON
  result shape), `src/openjarvis/evals/backends/external/{hermes_agent.py,
  openclaw.py,_subprocess_runner.py}` (the out-of-process, pinned-commit,
  "foreign code never imports into this process" pattern).
- Local adaptation: `qubit_runtime/agent/runtime_openjarvis.py` plus
  `qubit_runtime/tests/runtime/test_runtime_openjarvis.py`. Qubit owns session
  state and renders system/history into the CLI prompt; OpenJarvis keeps its own
  `OPENJARVIS_HOME`, isolated under Qubit's home.
- Test scenarios adapted from OpenJarvis `0583901`, Apache-2.0:
  `tests/evals/comparison/test_subprocess_runner.py`,
  `tests/evals/comparison/test_third_party.py`,
  `tests/cli/test_ask_e2e.py` and `tests/cli/test_ask_agent.py`. Only the
  scenarios that apply to the Qubit adapter are reproduced (timeout/crash/
  malformed output, commit-pin mismatch, model/tools/agent options, usage and
  tool-result shape); the energy sampler, eval telemetry propagation and
  OpenClaw `payloads` runner are not ported because Qubit does not implement
  those layers.

## OpenJarvis morning-digest prompt/policy — Apache-2.0

The `morning_digest` recipe's synthesis prompt and section policy
(`qubit_runtime/recipes/morning_digest.py`) adapts the "ONLY facts from the
data", "never invent personal context", per-section guide wording and
word-limit brevity rule from [open-jarvis/OpenJarvis](https://github.com/open-jarvis/OpenJarvis)
at commit `05839014127772bd077ba9ba2a2393a007f63ac6` (Apache License 2.0;
the upstream `LICENSE` carries the full text and copyright).

- Reference sources: `src/openjarvis/agents/morning_digest.py`
  (`_SECTION_PROMPTS`, `_build_system_prompt`) and
  `src/openjarvis/tools/digest_collect.py` (section ordering/format only).
- Local adaptation: `qubit_runtime/recipes/morning_digest.py`,
  `qubit_runtime/recipes/runner.py`, and `qubit_runtime/tests/recipes/`.
  **No OpenJarvis code is vendored or imported.** Qubit's deterministic
  collectors, independent degradation, typed unavailable reasons, CLI, voice
  and cron wiring are original.
- Deliberate divergence: OpenJarvis asks the model to silently omit absent
  sources; Qubit reports each missing connector/credential as a typed
  `unavailable` section so a gap is never hidden.
- The brief's schedule → preparation → urgent-mail ordering also follows the
  Qubit-bundled `google-workspace` skill's `references/daily-brief.md`
  (procedure only, no code copied).

## OpenJarvis research-loop policy — Apache-2.0

The `deep_research` recipe's bounded-retrieval and citation policy
(`qubit_runtime/recipes/deep_research.py`) adapts the research-loop ideas from
[open-jarvis/OpenJarvis](https://github.com/open-jarvis/OpenJarvis) at commit
`05839014127772bd077ba9ba2a2393a007f63ac6` (Apache License 2.0; the upstream
`LICENSE` carries the full text and copyright).

- Reference sources: `src/openjarvis/agents/research_loop.py`
  (`SYSTEM_PROMPT` citation rules, `max_iterations` tool budget,
  `build_sources_for_client` source cap and `renumber_citations`
  first-appearance renumbering) and `src/openjarvis/agents/deep_research.py`
  (force-synthesis wording after the turn budget).
- Local adaptation: `qubit_runtime/recipes/deep_research.py` plus
  `qubit_runtime/tests/recipes/test_deep_research.py`. **No OpenJarvis code is
  vendored or imported.** Qubit assigns citation numbers deterministically
  from the search results (first-appearance order, de-duplicated), bounds the
  search to 8 results and extraction to 5 pages, and uses Qubit's existing
  `web_search` / `web_extract` tools. The model only writes the brief.
- Deliberate divergence: OpenJarvis runs a planner-executor loop over a
  personal knowledge corpus with `search`/`clarify` tool calls. Qubit's recipe
  is a single deterministic retrieval frame; iterative multi-hop refinement
  remains the native agent's job (it can call `web_search`/`web_extract`
  repeatedly). Unread sources and fetch failures are reported as explicit
  facts and typed section gaps instead of being silently omitted.

## OpenJarvis Google Contacts connector field mapping — Apache-2.0

The read-only Google Contacts connector
(`qubit_runtime/connectors/google_contacts.py`) adapts the People API field
mapping and contact-shape constants from
[open-jarvis/OpenJarvis](https://github.com/open-jarvis/OpenJarvis) at commit
`05839014127772bd077ba9ba2a2393a007f63ac6` (Apache License 2.0; the upstream
`LICENSE` carries the full text and copyright).

- Reference sources: `src/openjarvis/connectors/gcontacts.py`
  (`_gcontacts_api_list` People API endpoint/fields, `_format_contact` name/
  email/phone/organization mapping, `contacts_find`/`contacts_get_info` tool
  intent) and `tests/connectors/test_gcontacts.py` (field-mapping scenarios).
- Local adaptation: `qubit_runtime/connectors/google_contacts.py`,
  `qubit_runtime/tools/google_contacts_tool.py`, and
  `qubit_runtime/tests/connectors/`. **No OpenJarvis code is vendored or
  imported**; OpenJarvis is not a Qubit dependency. Qubit's credential path,
  scope preflight, pagination bounds, typed refusals, redaction and registry
  integration are original.
- Deliberate divergences: Qubit reads the existing Google Workspace
  `google_token.json` and refreshes it through google-auth (no second OAuth
  flow, token store, or `~/.openjarvis` path); query search uses the People API
  `people:searchContacts` endpoint rather than client-side filtering; the
  connector is read-only and returns bound results, where OpenJarvis also runs
  a bulk document-sync pipeline.

## OpenJarvis trace schema and eval metric names — Apache-2.0

The opt-in local trace store (`qubit_runtime/traces/`) and the offline eval
bench (`qubit_runtime/evals/bench/`) adapt the *field set and reporting shape*
of [open-jarvis/OpenJarvis](https://github.com/open-jarvis/OpenJarvis) at commit
`05839014127772bd077ba9ba2a2393a007f63ac6` (Apache License 2.0; the upstream
`LICENSE` carries the full text and copyright). **No OpenJarvis source is
vendored, imported, or required at runtime.**

- Reference sources: `src/openjarvis/traces/store.py` (interaction trace
  columns: id, engine, model, query/result, timestamps, token totals, latency,
  metadata), `src/openjarvis/evals/core/types.py` (`EvalResult`/`RunSummary`
  metric names for accuracy, latency, prompt/completion tokens, cost, energy
  and estimated FLOPs), and `src/openjarvis/cli/eval_cmd.py` /
  `cli/optimize_cmd.py` (CLI vocabulary only).
- Local adaptation: `qubit_runtime/traces/` and
  `qubit_runtime/evals/bench/` plus their tests. Qubit's SQLite schema and
  migration/versioning, write-time redaction and length bounds, opt-in
  config/env gate, typed errors, retention/prune semantics, offline JSONL
  adapter and JSON/summary renderer are original.
- Deliberate divergences: Qubit stores only bounded, redacted excerpts and no
  message bodies, raw tool arguments/results or credentials; unknown
  energy/FLOPs/cost stay `null` and the bench reports them as `unavailable`
  rather than `0`; the eval bench makes no model calls and opens no network
  connection. The upstream DSPy-style optimization loop
  (`src/openjarvis/learning/optimize/`) is **not ported and not run** in this
  slice — `qubit evals run` states optimization as unavailable.

## OpenJarvis prompt-variant optimizer idea — Apache-2.0

The offline prompt-variant selector (`qubit_runtime/evals/optimize/`, exposed as
`qubit optimize variants`) adapts the *idea and CLI vocabulary* of scoring
candidate prompt variants against labeled examples from
[open-jarvis/OpenJarvis](https://github.com/open-jarvis/OpenJarvis) at commit
`05839014127772bd077ba9ba2a2393a007f63ac6` (Apache License 2.0; the upstream
`LICENSE` carries the full text and copyright). **No OpenJarvis source is
vendored, imported, or required at runtime, and DSPy is not a dependency.**

- Reference sources: `src/openjarvis/cli/optimize_cmd.py` (command vocabulary
  only) and `src/openjarvis/learning/optimize/` (the concept of comparing
  candidate prompt variants on scored examples).
- Local adaptation: `qubit_runtime/evals/optimize/` plus
  `qubit_runtime/tests/evals/test_variant_optimizer.py`. Qubit's variant
  validation and bounds, dataset/trace adapters, optional label sidecar for
  trace ids, deterministic prompt-to-variant matching, exact/contains/callable
  scorers, Wilson confidence intervals, refusal codes, and JSON/text reports
  are original.
- Deliberate divergences: this is **offline selection, not an optimization
  run**. It never generates or rewrites a prompt, never calls a model, never
  opens a network connection, and never invents a winner: it refuses when no
  labels exist or fewer than two variants have the minimum labeled samples. The
  upstream DSPy-style loop (prompt proposal, model calls, iterative search) is
  **not ported and not run**; `qubit evals run` continues to report
  optimization as unavailable.
