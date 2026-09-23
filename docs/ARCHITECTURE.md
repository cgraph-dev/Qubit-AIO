# Qubit ARCHITECTURE — one unified agent

Qubit is **one agent** assembled from three layers, all driven through a single
authority path. It is a rebranded fork of the upstream reference (renamed to the Qubit
namespace), plus a voice control surface, plus upstream-compatible adapters.

## Layers

```
  /chat (upstream terminal behavior, rebranded)      /qubit-chat (native voice)
              \                                        /
               \______  one backend / one authority  __/
                              |
        ┌─────────────────────┼─────────────────────┐
        │  Qubit runtime (qubit_runtime/) = upstream fork, renamed │
        │  agent · gateway · providers · plugins · MCP · sessions │
        └─────────────────────┼─────────────────────┘
        │  broker · policy · audit · confirmation · e-stop (one path) │
        │  QUBIT_HOME state · Qubit skills/plugins/MCP · Qubit config │
        └─────────────────────────────────────────────────────────┘
   voice bridge (STT/TTS, Right-Ctrl)      upstream adapters (voicebridge/*)
```

## Authority rules

- **Runtime state:** `QUBIT_HOME` only. `~/.hermes` is read-only
  migration/reference input and must never be a hidden runtime fallback.
- **One path:** every capability runs through the same broker / policy / audit /
  confirmation / e-stop chain, regardless of surface (text, dashboard, voice).
- **Canonical surfaces:** `/chat` = full upstream terminal behavior, rebranded.
  `/qubit-chat` = native local/voice surface (Brain4/local model, dynamic
  context, reasoning selector, API escalation, voice, Right-Ctrl) on the same
  backend.
- **Parity is behavior.** A capability counts only when it performs real backend
  work. A typed refusal is not a working capability.

## What is real vs what is overlay

- **Real (vendored upstream core):** `qubit_runtime/agent`, `gateway`, `providers`,
  `plugins`, `mcp_serve.py`, `qubit_cli/`. Large, complete, renamed.
- **Overlay (Qubit-specific):** `voicebridge/qubit_compat.py`,
  `voicebridge/gateway/openclaw_handlers/`, Qubit route/voice/state adapters.
- **Not to do:** do not rebuild the upstream with stubs, fake registries, or
  placeholder handlers. Diff against the working install only to find real
  drift/bugs; port a module only if it is provably missing and required, then
  apply the hermes→qubit rename map.

## Reference surface and brand

- **Behavioral reference:** the live local reference dashboard at
  `http://127.0.0.1:9119/?profile=default` (working install `hermes-agent`
  0.21.3). Everything it does, Qubit must do, and every capability must have a
  voice path through the Qubit voice bridge. See [PARITY.md](PARITY.md).
- **Brand:** zero legacy brand on any shipped/user-facing surface; legal
  attribution in `LICENSE` / `THIRD_PARTY_NOTICES.md` is the required exception.
  See [BRAND.md](BRAND.md).

## Independence

Qubit is a **standalone product** — everything, not only the installer. It owns
its installer/packaging, its 1:1 `qubit` command surface, its home/state/config
(`QUBIT_HOME`, one `state.db`), its service units, its updates and rollback, its
diagnostics, its skills/plugins/MCP registries, its model/provider config and
credential refs, its sessions/history, its voice pipeline, and its dashboard/UI.
It has **no runtime dependency on the reference install or service**; `~/.hermes`
is migration input only, never a fallback. See [CHECKLIST.md](CHECKLIST.md) §10.

## Known structural facts

- Runtime is `qubit-agent` 0.21.1; the working upstream install is 0.21.3. The gap is upstream
  decomposition; nothing Qubit imports is missing.
- 20 upstream architecture-gated methods are OPEN by design (no upstream
  registry/transport); they are not parity failures, but they are not "done"
  either.
