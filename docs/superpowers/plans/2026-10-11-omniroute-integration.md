# OmniRoute Opt-In Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `setup run --steps omniroute` provisions a local OmniRoute gateway and wires it as Qubit's automatic rate-limit fallback, on Linux, macOS, and Windows, fully opt-in.

**Architecture:** One new owner module `voicebridge/omniroute_setup.py` (probe/provision/repair/remove) registered as an opt-in entry in the existing `SETUP_STEPS` table; all config writes go through the canonical seams proven live on 2026-10-11 (profile upsert, atomic config save, runtime `.env` secret path).

**Tech Stack:** Python (voicebridge), systemd user units (Linux), launchd plists (macOS), scheduled tasks (Windows), npm (gateway package, pinned floor 3.8.51), React page already shipped (`frontend/qubit-web/src/pages/OmniRoutePage.tsx`).

**Spec:** `docs/superpowers/specs/2026-10-11-omniroute-integration-design.md`

## Global Constraints

- Opt-in only: the step never runs unless explicitly requested (explicit `--steps omniroute` or a default-no confirm); unprovisioned installs behave exactly as today.
- Gateway version floor is 3.8.51; provision refuses to seed against anything older.
- Provider connections inside OmniRoute stay user-owned; setup never copies upstream keys.
- Secrets (admin password, gateway API key) live only in `~/.omniroute/.env` (0600) and the Qubit runtime `.env` via the existing secret path; shown once, then referenced as `env:OMNIROUTE_API_KEY`.
- Config writes use canonical seams only: `upsert_model_profile(confirm=True)`, `load_config`/`save_config` (atomic, additive).
- `LICENSE` covers original Qubit material only; third-party provenance recorded before implementation.
- No full test suite until implementation is frozen; focused checks only while building.

## Review Focus

- Port 20128 taken by a foreign process: provision must fail with the exact fix (show PID/owner, offer `--port`), never kill it.
- npm registry unreachable mid-install: typed failure naming the retry command, prior good state intact.
- Headless API-key creation gap: if no local no-auth key-creation path exists, provision must stop with operator instructions, not a half-wired install.
- Re-provision duplicates: second run must report all-skipped-present with zero config diff.
- Secret leak: key or password value must never appear in logs, test snapshots, page output, or receipts; a test pins this.

---

### Task 1: Provenance + license record

**Files:**
- Modify: `THIRD_PARTY_NOTICES.md` (append OmniRoute entry)
- Test: none (docs); verification is the review step below.

**Interfaces:**
- Consumes: nothing.
- Produces: pinned upstream ref + license basis every later task relies on.

- [ ] **Step 1: Resolve the pinned upstream ref**

Run: `npm view omniroute version && npm view omniroute dist.tarball`
Expected: version >= 3.8.51. Record repo `https://github.com/diegosouzapw/OmniRoute`, the exact tag/commit matching the npm payload, and license `MIT` (verify `LICENSE` file in the tarball).

- [ ] **Step 2: Append the THIRD_PARTY_NOTICES entry**

```markdown
## OmniRoute (https://github.com/diegosouzapw/OmniRoute) — MIT
- Pinned: omniroute@<exact-version> (<tag/commit>), installed via npm at setup time, never vendored.
- Local changes: none to upstream code. Qubit-side wiring only: setup provisioner
  (`voicebridge/omniroute_setup.py`), 4 voice profiles, 1 agent fallback entry,
  1 dashboard page (`frontend/qubit-web/src/pages/OmniRoutePage.tsx`).
- Data: gateway runs on loopback; provider accounts are operator-connected.
```

- [ ] **Step 3: License gate check**

Run: `grep -n "OmniRoute" THIRD_PARTY_NOTICES.md`
Expected: entry present. If the tarball license is not MIT, stop and report `BLOCKED — LICENSE REVIEW REQUIRED` instead of proceeding.

- [ ] **Step 4: Commit**

```bash
git add THIRD_PARTY_NOTICES.md
git commit -m "Record OmniRoute provenance and MIT basis"
```

### Task 2: Provision core module (probe + package + env)

**Files:**
- Create: `voicebridge/omniroute_setup.py`
- Test: `tests/test_omniroute_setup_core.py`

**Interfaces:**
- Consumes: stdlib only (`shutil`, `subprocess`, `secrets`, `urllib`).
- Produces: `probe_environment() -> dict`, `write_gateway_env(home, password) -> Path`, `GATEWAY_FLOOR = "3.8.51"`, `GATEWAY_PORT = 20128` for Tasks 3–5.

- [ ] **Step 1: Write the failing test**

```python
def test_probe_reports_missing_node(tmp_path, monkeypatch):
    import voicebridge.omniroute_setup as S
    monkeypatch.setattr(shutil, "which", lambda *_a, **_k: None)
    result = S.probe_environment()
    assert result["state"] == "FAILED"
    assert result["code"] == "NO_NODE"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `scripts/run_tests.sh tests/test_omniroute_setup_core.py -k test_probe_reports_missing_node`
Expected: FAIL with "No module named 'voicebridge.omniroute_setup'" (or ModuleNotFoundError).

- [ ] **Step 3: Write minimal implementation**

```python
"""Opt-in local OmniRoute gateway provisioning (setup step backend)."""
from __future__ import annotations
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict

GATEWAY_FLOOR = "3.8.51"
GATEWAY_PORT = 20128
GATEWAY_HOST = "127.0.0.1"

def _ok(**kw: Any) -> Dict[str, Any]:
    out = {"state": "OK"}
    out.update(kw)
    return out

def _fail(code: str, detail: str, **kw: Any) -> Dict[str, Any]:
    out = {"state": "FAILED", "code": code, "detail": detail}
    out.update(kw)
    return out

def probe_environment() -> Dict[str, Any]:
    """Check node/npm, installed gateway version, and port ownership."""
    node = shutil.which("node")
    npm = shutil.which("npm")
    if not node or not npm:
        return _fail("NO_NODE", "node/npm not on PATH; install Node 22+ then re-run.")
    omni = shutil.which("omniroute")
    if omni is None:
        return _ok(installed=False, version=None, port_free=_port_free())
    try:
        raw = subprocess.run([omni, "--version"], capture_output=True,
                             text=True, timeout=30).stdout.strip()
    except Exception as exc:
        return _fail("VERSION_CHECK_FAILED", f"omniroute --version failed: {exc}")
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", raw)
    if not m:
        return _fail("VERSION_UNPARSEABLE", f"could not parse version from: {raw!r}")
    if tuple(int(x) for x in m.groups()) < (3, 8, 51):
        return _fail("VERSION_TOO_OLD",
                     f"omniroute {m.group(0)} < floor {GATEWAY_FLOOR}; upgrade first.")
    return _ok(installed=True, version=m.group(0), port_free=_port_free())
```

(`_port_free()` tries `socket.bind((GATEWAY_HOST, GATEWAY_PORT))`; on failure it shells `ss -ltnp` best-effort to name the owner for the remediation message, and returns `port_free=False, owner=<desc>`. A second test pins this: a bound port yields `port_free is False` plus an `owner` description — the message names the holder, never kills it.)

- [ ] **Step 4: Run test to verify it passes**

- [ ] **Step 4: Run test to verify it passes**

Run: `scripts/run_tests.sh tests/test_omniroute_setup_core.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add voicebridge/omniroute_setup.py tests/test_omniroute_setup_core.py
git commit -m "Add OmniRoute setup probe core with version floor"
```

### Task 3: Platform service backends

**Files:**
- Modify: `voicebridge/omniroute_setup.py` (append backends)
- Test: `tests/test_omniroute_setup_service.py`

**Interfaces:**
- Consumes: `GATEWAY_PORT`, `GATEWAY_HOST` from Task 2.
- Produces: `install_service() -> dict`, `service_status() -> dict`, `remove_service(purge_data=False) -> dict` for Task 5.

- [ ] **Step 1: Write the failing test**

```python
def test_systemd_unit_renders_with_pinned_paths(tmp_path):
    import voicebridge.omniroute_setup as S
    unit = S.render_systemd_unit(node_bin="/x/bin/node", server_mjs="/x/omniroute.mjs")
    assert "ExecStart=/x/bin/node /x/omniroute.mjs serve --no-open" in unit
    assert "WantedBy=default.target" in unit
```

- [ ] **Step 2: Run test to verify it fails**

Run: `scripts/run_tests.sh tests/test_omniroute_setup_service.py -k test_systemd_unit_renders_with_pinned_paths`
Expected: FAIL with "has no attribute 'render_systemd_unit'".

- [ ] **Step 3: Write minimal implementation**

```python
def render_systemd_unit(node_bin: str, server_mjs: str) -> str:
    return f"""[Unit]
Description=OmniRoute AI Gateway (Qubit fallback provider)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={node_bin} {server_mjs} serve --no-open
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""
```

Plus the proven-live Linux flow (`~/.config/systemd/user/omniroute.service` +
`daemon-reload` + `enable --now`), a launchd plist renderer/installer
(`~/Library/LaunchAgents/qubit.omniroute.plist`, `KeepAlive`, `RunAtLoad`)
and a Windows scheduled-task renderer (`schtasks /create /tn
QubitOmniRoute /tr "... serve --no-open" /sc onlogon`, `/delete /f` on
remove), each behind a platform dispatch with `service_status()` parsing
`systemctl --user is-active` / `launchctl list` / `schtasks /query`.
`remove_service()` stops + disables; data under `~/.omniroute` is kept
unless `purge_data=True`.

- [ ] **Step 4: Run test to verify it passes**

Run: `scripts/run_tests.sh tests/test_omniroute_setup_service.py`
Expected: PASS (renderers + status-parser unit tests; live service assertions stay Linux-CI E2E in Task 7).

- [ ] **Step 5: Commit**

```bash
git add voicebridge/omniroute_setup.py tests/test_omniroute_setup_service.py
git commit -m "Add OmniRoute platform service backends"
```

### Task 4: Headless credential + key provisioning path

**Files:**
- Modify: `voicebridge/omniroute_setup.py` (append credential functions)
- Test: `tests/test_omniroute_setup_creds.py`

**Interfaces:**
- Consumes: running gateway on `GATEWAY_HOST:GATEWAY_PORT` (Task 3).
- Produces: `ensure_api_key() -> dict{key_shown_once | instructions}` for Task 5.

- [ ] **Step 1: Spike the headless key-creation path (time-boxed)**

Run, in order, and record which succeeds:
1. `omniroute keys list` (local store read, no server auth — proven live 2026-10-11).
2. Dashboard API key-create endpoints with the first-run session (inspect `src/server` routes for an unauthenticated setup token on fresh installs).
3. `omniroute setup --password <generated> --non-interactive` variants.

Acceptance: exactly one no-auth local path that mints a client API key. If none exists, implement the documented fallback: provision stops with operator instructions (dashboard → API Keys → create `qubit-voicebridge`), and the Qubit wiring steps wait — never half-wire.

- [ ] **Step 2: Write the failing test**

```python
def test_env_write_is_0600_and_idempotent(tmp_path):
    import voicebridge.omniroute_setup as S
    p = S.write_gateway_env(tmp_path, password="pw1")
    assert oct(p.stat().st_mode & 0o777) == "0o600"
    assert "CORS_ALLOWED_ORIGINS=http://127.0.0.1:8890" in p.read_text()
    S.write_gateway_env(tmp_path, password="pw1")
    assert p.read_text().count("CORS_ALLOWED_ORIGINS=") == 1
```

- [ ] **Step 3: Run test to verify it fails**

Run: `scripts/run_tests.sh tests/test_omniroute_setup_creds.py -k test_env_write_is_0600_and_idempotent`
Expected: FAIL with "has no attribute 'write_gateway_env'".

- [ ] **Step 4: Write minimal implementation**

`write_gateway_env()` (0600, keeps `STORAGE_ENCRYPTION_KEY`, sets `CORS_ALLOWED_ORIGINS=http://127.0.0.1:8890`, generated admin password via `secrets.token_urlsafe(24)`), `ensure_api_key()` (spike result; key value returned once in-memory only), and the Qubit-side store via the existing secret path (`save_env_value("OMNIROUTE_API_KEY", key)` — the same call the custom-endpoint flow uses). A redaction test asserts no `ensure_api_key()` result, log line, or receipt contains the key value itself (last-4 fingerprint only).

- [ ] **Step 5: Run test to verify it passes**

Run: `scripts/run_tests.sh tests/test_omniroute_setup_creds.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add voicebridge/omniroute_setup.py tests/test_omniroute_setup_creds.py
git commit -m "Add OmniRoute headless credential provisioning"
```

### Task 5: Qubit-side wiring (profiles + fallback chain) + setup-table registration

**Files:**
- Modify: `voicebridge/omniroute_setup.py` (append `seed_qubit_wiring()`), `voicebridge/lifecycle.py` (`SETUP_STEPS`), `voicebridge/product.py` (install probe)
- Test: `tests/test_omniroute_setup_wiring.py`

**Interfaces:**
- Consumes: `ensure_api_key()` (Task 4); canonical seams `upsert_model_profile`, `load_config`/`save_config`.
- Produces: registered opt-in `omniroute` setup step; seeded profiles + chains.

- [ ] **Step 1: Write the failing test**

```python
def test_seed_is_idempotent_and_dedupes(monkeypatch, tmp_path):
    import voicebridge.omniroute_setup as S
    # run against an isolated QUBIT_HOME + voice.yaml (A->B->A scope check)
    first = S.seed_qubit_wiring()
    second = S.seed_qubit_wiring()
    assert second["created_profiles"] == [] and second["updated_profiles"] == []
    assert second["fallback_appended"] is False
    from qubit_cli.fallback_config import get_fallback_chain
    chain = get_fallback_chain(...)  # isolated home config
    omni = [e for e in chain if e["provider"] == "omniroute"]
    assert len(omni) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `scripts/run_tests.sh tests/test_omniroute_setup_wiring.py -k test_seed_is_idempotent_and_dedupes`
Expected: FAIL with "has no attribute 'seed_qubit_wiring'".

- [ ] **Step 3: Write minimal implementation**

`seed_qubit_wiring()` creates the 4 profiles (`omniroute-auto/auto`,
`omniroute-coding/auto/coding`, `omniroute-fast/auto/fast`,
`omniroute-cheap/auto/cheap`, `kind=api`, `base_url`
`http://127.0.0.1:20128/v1`, `key_ref=env:OMNIROUTE_API_KEY`,
`locality=external`, `api_mode=chat_completions`,
`context_tokens=128000`) via `create_model_profile`, sets
`fallback_to=omniroute-auto` on external non-omniroute profiles via
canonical upsert, and appends
`{provider: omniroute, model: auto, base_url, api_mode:
chat_completions, key_env: OMNIROUTE_API_KEY}` to `fallback_providers`
plus `providers.omniroute` via `load_config`/`save_config` — every write
skipped when already correct (the exact shapes proven live
2026-10-11). `repair()` delegates to `provision()` (full re-run;
idempotency makes it safe). Registration: append the opt-in entry to `SETUP_STEPS`
(`"confirm": True`, default-no) plus a `product.install_steps` probe
reporting installed/unprovisioned.

- [ ] **Step 4: Run test to verify it passes**

Run: `scripts/run_tests.sh tests/test_omniroute_setup_wiring.py`
Expected: PASS, including the A→B→A profile-scope check (seed under home A, assert home B untouched).

- [ ] **Step 5: Commit**

```bash
git add voicebridge/omniroute_setup.py voicebridge/lifecycle.py voicebridge/product.py tests/test_omniroute_setup_wiring.py
git commit -m "Register opt-in omniroute setup step with Qubit wiring"
```

### Task 6: Dashboard page unprovisioned state + operator docs

**Files:**
- Modify: `frontend/qubit-web/src/pages/OmniRoutePage.tsx`, `frontend/qubit-web/src/pages/OmniRoutePage.test.tsx`
- Create: `docs/OMNIROUTE.md` (operator doc: install, rotate key, change dashboard password, per-platform service commands, remove/purge)

**Interfaces:**
- Consumes: nothing new (page probe already exists).
- Produces: documented, hint-complete unprovisioned UX.

- [ ] **Step 1: Write the failing test**

```tsx
it("unreachable state names the setup command", async () => {
  vi.stubGlobal("fetch", async () => { throw new Error("refused"); });
  renderPage(<OmniRoutePage />);
  await act(async () => {});
  expect(text()).toContain("setup run --steps omniroute");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/pages/OmniRoutePage.test.tsx`
Expected: FAIL (hint text absent).

- [ ] **Step 3: Write minimal implementation**

Unreachable status text becomes: "Unreachable — install it with `setup run --steps omniroute`, or start an existing install with `omniroute serve --daemon --no-open`." Write `docs/OMNIROUTE.md` covering install, key rotation (`keys rotate` + re-store + service restart), dashboard password change, per-platform start/stop/status, and `remove` vs `--purge-data`.

- [ ] **Step 4: Run tests + typecheck to verify**

Run: `npx vitest run src/pages/OmniRoutePage.test.tsx && npx tsc -b`
Expected: PASS, exit 0.

- [ ] **Step 5: Commit**

```bash
git add frontend/qubit-web/src/pages/OmniRoutePage.tsx frontend/qubit-web/src/pages/OmniRoutePage.test.tsx docs/OMNIROUTE.md
git commit -m "Pin OmniRoute unprovisioned hint and operator docs"
```

### Task 7: Single verification pass (frozen implementation)

**Files:** none (evidence only).

- [ ] **Step 1: Linux live E2E on a clean temp home**

Run: fresh `QUBIT_HOME` + voice.yaml → `setup run --steps omniroute` → assert `omniroute serve` healthy, authed `/v1/models` non-empty, 4 profiles gate OK, `fallback_to` set, agent chain length 1 → re-run → assert zero-diff idempotent → `remove` → assert service gone, wiring gone, data kept.
Expected: all green; record the receipt.

- [ ] **Step 2: Platform lanes**

Run: macOS launchd + Windows scheduled-task render/install/status paths on their lanes (or documented manual receipts if lanes lack service control).
Expected: green or receipted.

- [ ] **Step 3: Report**

Report PASS/FAIL per step with receipts. On failure, repair the affected task with focused checks and re-run this pass once.
