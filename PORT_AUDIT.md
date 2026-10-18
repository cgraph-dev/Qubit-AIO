# Port audit — silent no-op / phantom-symbol defects

**Tree:** `/tmp/wt-p4-audit` (branch `fix/p4-audit`, merged tree port/merged-926)
**Scope:** every plugin under `qubit_runtime/plugins/platforms/*`, plus same-class
phantom imports found outside it.
**Method:** real discovery through `qubit_cli.plugins.get_plugin_manager()` +
`gateway.platform_registry`, AST signature comparison against `PlatformEntry`,
AST/importlib resolution of every absolute and relative import, and direct
execution of each suspect import in a clean process.
**Rule applied:** a typed refusal is never a working capability. A name that
imports to nothing — or an `except` that turns a missing symbol into `None` — is
a defect, not a feature.

**No code was changed.** This commit adds this report only.

---

## Verdict

- **Item (1) — unknown kwargs to `register_platform` → TypeError at load:** **CLEAN.**
- **Item (2) — empty / `__pycache__`-only platform dirs:** **CLEAN.**
- **Item (3) — names imported that do not exist in the target module:** **11 REAL DEFECTS**
  (3 inside platform plugins, 8 outside).

The previous three merge incidents did **not** recur: all 27 bundled platform
directories are tracked, all 27 resolve through the real registry, and all 13
declared `target_ref_syntax_fn` parsers execute and return real ids.

---

## Findings, by severity

### S1 — `feishu` adapter is unimportable: whole platform is dead

| | |
|---|---|
| **File:line** | `qubit_runtime/plugins/platforms/feishu/adapter.py:94` |
| **Code** | `from gateway.status import acquire_scoped_lock, release_scoped_lock` |
| **Reality** | `release_scoped_lock` lives in `gateway/status_scoped_locks.py:13`; `gateway.status` no longer defines it (`hasattr` → `False`). `gateway/platforms/base.py:2293` uses the correct new import. |

**Runtime symptom (verified):**

```
>>> from gateway.status import release_scoped_lock
ImportError: cannot import name 'release_scoped_lock' from 'gateway.status'
```

- Registry `get("feishu")` → **`None`**. The plugin drops out of discovery entirely.
- Manager records: `platforms/feishu: error=cannot import name 'release_scoped_lock' from 'gateway.status'`.
- `pytest tests/gateway/test_feishu.py` fails at collection/import:
  `1 failed, 1 passed` — `ImportError` at `plugins/platforms/feishu/adapter.py:94`.
- Every Feishu/Lark capability (messages, drive comments, meeting invites, webhook)
  is gone while the directory, manifest and `plugin.yaml` all still look intact —
  exactly the "appears present, does nothing" shape.

**Fix:** import `release_scoped_lock` from `gateway.status_scoped_locks`
(keep `acquire_scoped_lock` from `gateway.status`, which is still defined at
`gateway/status.py:1606`).

---

### S2 — `qqbot` and `weixin` interactive setup crash on invocation

Both import five helper names that exist only with a leading underscore.

| | |
|---|---|
| **File:line** | `qubit_runtime/plugins/platforms/qqbot/register.py:42` |
| **Code** | `from qubit_cli.gateway_setup_wizard import (confirm_reconfigure, offer_home_channel, print_setup_header, prompt_csv, save_env_values)` |
| **File:line** | `qubit_runtime/plugins/platforms/weixin/adapter.py:1317` |
| **Code** | `from qubit_cli.gateway_setup_wizard import (confirm_reconfigure, offer_home_channel, print_setup_header, prompt_csv, save_env_values)` |
| **Reality** | `qubit_cli/gateway_setup_wizard.py` defines only `_confirm_reconfigure:192`, `_offer_home_channel:201`, `_print_setup_header:187`, `_prompt_csv:213`, `_save_env_values:208`. No un-prefixed aliases exist. |

**Runtime symptom (verified):**

```
ImportError: cannot import name 'confirm_reconfigure' from
'qubit_cli.gateway_setup_wizard'
```

- Trigger is `setup_qqbot()` (qqbot) and `interactive_setup()` (weixin) — both
  bound as the registry's `setup_fn`.
- So `qubit gateway setup` → QQ Bot, and → Weixin, raise `ImportError` instead of
  prompting. The platform registers fine and connects fine; only the guided setup
  path is dead, which is why nothing at startup catches it.
- The setup **menu** still lists both platforms (it reads the manifest, not the
  setup body), so the user is offered a flow that cannot run.

**Fix:** use the `_`-prefixed names, or expose un-prefixed aliases in
`gateway_setup_wizard.py`. Note `qubit_cli/gateway.py:4700` imports the *same*
un-prefixed names as a facade re-export — that file has the same latent break
and should be checked together.

---

### S3 — `weixin` silently loses its CA bundle: `except ImportError: return None`

| | |
|---|---|
| **File:line** | `qubit_runtime/plugins/platforms/weixin/adapter.py:106` |
| **Code** | `from agent.ssl_verify import certifi_bundle_path` |
| **Reality** | `agent/ssl_verify.py` defines `install_truststore:36`, `platform_ssl_context:119`, `resolve_httpx_verify:133`, `_shared_context:96` — **no** `certifi_bundle_path`. No other module defines it either. |

**Runtime symptom (verified):**

```
>>> weixin._make_ssl_connector()
None
```

- The absent name raises `ImportError`, which the enclosing
  `try: ... except ImportError: return None` swallows at `:107-108`.
- `_make_ssl_connector()` therefore returns `None` on **every** call, so all six
  `_new_session()` call sites (`:605, :767, :770, :866, :1246`, plus the helper)
  build `aiohttp.ClientSession(connector=None)` — aiohttp defaults.
- This is the textbook silent no-op: the function's whole documented purpose
  (pin certifi's CA bundle because `ilinkai.weixin.qq.com` fails some system
  stores) is dead, and nothing logs a word. Only the docstring and the
  `#137255` comment remain to say it was ever meant to work.
- The platform still connects on hosts whose system store happens to work, which
  is why this never surfaced as a test failure.

**Fix:** restore/define `certifi_bundle_path()` in `agent/ssl_verify.py` (or
import from wherever it moved to) — and make the `except ImportError` log at
`warning` instead of returning `None` silently, since a missing symbol here is
not an expected condition.

---

### S4 — seven more phantom imports outside the platform tree

Same defect class; all verified with a real `ImportError`. None is covered by
any test (`grep` for the symbol names across `tests/` → zero hits), which is why
they survived the merge.

| File:line | Statement | Symptom | Reachable from |
|---|---|---|---|
| `gateway/run.py:5182` | `from gateway.status import release_all_scoped_locks` | Stale scoped locks from a SIGKILL'd old gateway are never released → new gateway refuses to start. Import sits inside `try/ … except Exception: pass`, so it fails **silently**. | gateway startup takeover |
| `gateway/relay/inbound_admission.py:43` | `from gateway.relay.adapter import _PERMANENT_REFUSALS` | Every relay inbound event raises at admit time instead of ACK/redeliver. | `async def _admit`:39, every relay message |
| `gateway/session_authority.py:284` | `from gateway.session_viewer_close import cancel_persisted_close` | `attach()` raises: session attach fails after the snapshot is built. | `async def attach`:261 |
| `gateway/session_local_recovery.py:250` | `from gateway.session_viewer_close import rearm_persisted_closes` | Paused local-session recovery raises instead of re-arming viewer closes. | local recovery loop |
| `qubit_cli/web_routers/messaging.py:1043` | `from gateway.pairing import _PLATFORM_ALLOWLIST_ENV` | Channels-page authz posture request 500s for every platform. | `_platform_authz_env_names`:1039 |
| `qubit_cli/web_server_app.py:30` | `from qubit_cli.web_server import _probe_install_before_state_db` | Dashboard/web app lifespan fails on startup (import is deferred, so it fails at first request, not at import). | `standalone_lifespan` |
| `tui_gateway/methods_connectors.py:92` | `from tools.connectors import ensure_guest_identity` | Opening/using the Connectors page raises instead of creating the guest identity. | `_account_identity_error`:89 |

`agent/session_client_env.py:23` imports `is_secret_env_name` from the same
non-exporting module but **guards it correctly** with
`except ImportError:` + a local fallback definition — that one is sound and is
listed only to mark the contrast with S3.

---

## What was checked and found clean

### (1) `register_platform` kwargs vs `PlatformEntry` — CLEAN

`PlatformEntry` (`gateway/platform_registry.py:53`) declares 45 fields. All 27
`register_platform` call sites were AST-extracted, including the 6 that splat
`**gateway_env.AUTHZ` / `**common` (those dicts were resolved and their keys
checked individually: `allowed_users_env`, `allow_all_env`, `own_policy_env`,
`group_allowed_chats_env`, `allowlist_suppresses_pairing` — all defined).

**Unknown kwargs found: none.** No plugin raises `TypeError` at registration.

Runtime confirmation via real discovery — 27/27 bundled platforms registered,
26/27 resolved, the one `None` being the S1 feishu import failure:

| Status | Platforms |
|---|---|
| `get()` returns an entry | a2a, bluebubbles, buzz, dingtalk, discord, email, google_chat, homeassistant, irc, line, matrix, mattermost, ntfy, photon, qqbot, raft, signal, slack, sms, teams, telegram, wecom, weixin, whatsapp, yuanbao |
| `get()` returns `None` | **feishu** (S1 — import failure, not a kwargs problem) |
| `get()` returns `None` | **simplex** — *not a defect*: `platforms/simplex` is on the user's own `plugins.disabled` list. The adapter imports cleanly when forced. |

`target_ref_syntax_fn` is `None` for 14 platforms; **all are legitimate
undeclared fields**, not dropped registrations. The five platforms named in the
earlier incident — signal, qqbot, weixin, yuanbao, bluebubbles — all declare it
(both signal/weixin/yuanbao via an explicit parser; qqbot and bluebubbles are
pure-id platforms handled by core's generic rules). All 13 declared parsers were
executed against representative input and each returned a real `(chat_id, None)`
— no stub returns `NOT_EXPLICIT` unconditionally.

`tools/send_message_targets.py:33-39` treats a missing parser as an intentional
"use generic id rules" signal, and `resolve_send_target` is lenient by design for
parser-less plugin platforms — so `None` here is contract, not damage.

### (2) Empty / `__pycache__`-only platform dirs — CLEAN

All 27 directories carry tracked sources (`git ls-files`), each has both
`__init__.py` and a `plugin.yaml`, and each has 2–13 tracked `.py` files. The
earlier incident (`{signal,qqbot,weixin,yuanbao,bluebubbles}` holding only
`__pycache__`) has not recurred — e.g. signal now tracks 9 files / 8 `.py`,
qqbot 12 / 11. `git status --ignored` shows only normal `__pycache__/` noise.

### (3b) Relative sibling imports — CLEAN

Every `from .X import a, b` inside the platform packages was resolved against the
target module's AST: **0 missing names, 0 missing modules.**

Third-party `ModuleNotFoundError`s (`mautrix`, `slack_bolt`, `microsoft_teams`,
`aiohttp_socks`) are optional-SDK imports behind `check_fn`/`ensure_deps_fn` and
are by design, not defects.

---

## Recommended fix order

1. **S1** `feishu/adapter.py:94` — one-line import move; restores an entire
   platform. Highest ratio of damage to effort.
2. **S4** `gateway/run.py:5182` — inside `except Exception: pass`, so it fails
   silently and leaves stale locks that block gateway restart.
3. **S3** `weixin/adapter.py:106` — restore `certifi_bundle_path` and stop
   swallowing the import error.
4. **S2** `qqbot/register.py:42`, `weixin/adapter.py:1317` (+ check
   `qubit_cli/gateway.py:4700`) — guided setup for two platforms.
5. Remaining **S4** rows — same one-line import corrections.

## Reproduction

Probes left in place (untracked, for the fixer):

- `tmp/audit_probe.py` — real discovery + forced resolution of all 27 platforms.
- `tmp/import_check.py` — item (3) sweep over all platform `.py` files.
- `tmp/verify_findings.py` — executes the feishu / qqbot / weixin / certifi imports.
- `tmp/verify_extra.py` — executes the eight S4 imports.
- `tmp/parser_check.py` — executes all 13 `target_ref_syntax_fn` parsers.
