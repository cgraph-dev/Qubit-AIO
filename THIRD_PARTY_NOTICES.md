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

---

Entries for Semantic Router (MIT), Mem0 (Apache-2.0), devtools-protocol
(BSD-3-Clause), and Himalaya (MIT) become required in the packet that performs
each COPY/ADAPT listed in docs/UPSTREAM_REUSE.md §15. Wrap-only and REJECT
decisions carry no notice obligation.
