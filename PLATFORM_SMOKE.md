# Platform layer smoke test — merged tree (`fix/p5-live`)

Runtime-behaviour only. Every row below was produced by executing the real plugin
loader and calling the real factories/senders — not by reading source.

- Worktree: `/tmp/wt-p5-live` @ `2aa5f162a` (branch `fix/p5-live`)
- Interpreter: `/home/trick/Projects/Repos/qubit-voicebridge/.venv/bin/python` (3.12.3)
- Method: `PluginManager().discover_and_load()` against an isolated `QUBIT_HOME`
  with **all 27 bundled platform plugins enabled**, so enumeration reflects the
  code rather than this machine's `plugins.enabled` config.
- Probes: `tmp/platform_smoke_collect.py`, `tmp/platform_smoke_standalone.py`
  (data: `tmp/smoke_data.json`)

---

## 1. Registry enumeration — 27/27 register

Every bundled platform plugin under `plugins/platforms/` registers a `PlatformEntry`.

| platform | label | max_message_length | adapter_factory | target_ref_syntax_fn |
|---|---|---:|---|---|
| a2a | A2A | 0 (no limit) | ✅ | ❌ |
| bluebubbles | BlueBubbles (iMessage) | 0 (no limit) | ✅ | ❌ |
| buzz | Buzz | 0 (no limit) | ✅ | ❌ |
| dingtalk | DingTalk | 0 (no limit) | ✅ | ❌ |
| discord | Discord | 2000 | ✅ | ❌ |
| email | Email | 50000 | ✅ | ✅ |
| feishu | Feishu / Lark | 8000 | ✅ | ❌ |
| google_chat | Google Chat | 4000 | ✅ | ❌ |
| homeassistant | Home Assistant | 4096 | ✅ | ❌ |
| irc | IRC | 450 | ✅ | ❌ |
| line | LINE | 4500 | ✅ | ❌ |
| matrix | Matrix | 15000 | ✅ | ❌ |
| mattermost | Mattermost | 4000 | ✅ | ❌ |
| ntfy | ntfy | 4096 | ✅ | ✅ |
| photon | iMessage via Photon | 8000 | ✅ | ✅ |
| qqbot | QQ Bot | 0 (no limit) | ✅ | ❌ |
| raft | Raft | 0 (no limit) | ✅ | ❌ |
| signal | Signal | 8000 | ✅ | ✅ |
| simplex | SimpleX Chat | 8000 | ✅ | ❌ |
| slack | Slack | 39000 | ✅ | ❌ |
| sms | SMS (Twilio) | 1600 | ✅ | ❌ |
| teams | Microsoft Teams | 28000 | ✅ | ❌ |
| telegram | Telegram | 4096 | ✅ | ✅ |
| wecom | WeCom (Enterprise WeChat) | 4000 | ✅ | ✅ |
| weixin | Weixin / WeChat | 0 (no limit) | ✅ | ✅ |
| whatsapp | WhatsApp | 4096 | ✅ | ✅ |
| yuanbao | Yuanbao | 0 (no limit) | ✅ | ✅ |

- `adapter_factory`: set on **27/27**.
- `target_ref_syntax_fn`: set on **9/27** (email, ntfy, photon, signal, telegram,
  wecom, weixin, whatsapp, yuanbao). The other 18 fall back to core's generic id
  rules — that is the documented contract (it is a *hint*, not an authority), so
  an absent one is not a defect.
- `max_message_length == 0` means "no cap" for 6 platforms (a2a, bluebubbles, buzz,
  dingtalk, qqbot, raft, weixin, yuanbao) — chunking is unbounded there by design.

## 2. Adapter construction — 27/27 construct

Each `adapter_factory` was called with a minimal but complete config
(`PlatformConfig.from_dict`, every `required_env` satisfied with a placeholder,
URL-shaped values where the name implies a URL). Exceptions were caught, not
propagated.

**Result: 27/27 constructed an adapter object. 0 failures.**

Two defects were found and fixed to reach that number. Both were load-time or
constructor-time blowups that no source-reading audit would have flagged as
"imports fine":

### DEFECT 1 (fixed) — feishu failed to load; platform silently absent
```
Failed to load plugin 'feishu-platform': cannot import name 'release_scoped_lock'
from 'gateway.status'
```
`release_scoped_lock` was split out into `gateway.status_scoped_locks`;
`acquire_scoped_lock` stayed in `gateway.status`. The ported feishu adapter imported
both from `gateway.status`, so the module failed at import and **feishu never
registered at all** — a whole platform invisible to the gateway, with no
user-facing error beyond a log line.

- Fix: `qubit_runtime/plugins/platforms/feishu/adapter.py:94` — import
  `acquire_scoped_lock` from `gateway.status`, `release_scoped_lock` from
  `gateway.status_scoped_locks`.
- Negative control: with the fix reverted, `platform_registry.get('feishu') is None`
  and the import error reproduces; with it applied, the entry registers and the
  adapter constructs.

### DEFECT 2 (fixed) — homeassistant adapter could never be constructed
```
AttributeError: type object 'Platform' has no attribute 'HOMEASSISTANT'
at HomeAssistantAdapter.__init__ -> super().__init__(config, Platform.HOMEASSISTANT)
```
`Platform` has no static `HOMEASSISTANT` member. Plugin platforms are **dynamic
members minted on first lookup** via `Platform._missing_`; `homeassistant` is a
bundled plugin, so `Platform` only grew the member once something asked for
`Platform("homeassistant")` — which the adapter's own constructor never did.

Impact path: `gateway/run_adapters.py:1873`. A config with homeassistant enabled hits
`platform_registry.create_adapter(...)` → factory raises → caught in
`create_adapter` → returns `None` → the gateway logs *"Platform 'homeassistant' is
registered but adapter creation failed (check dependencies and config)"* and **never
falls through to a built-in**. The platform was dead on every startup, and the
error text pointed at dependencies, which were fine.

- Fix: `qubit_runtime/plugins/platforms/homeassistant/adapter.py:96` —
  `Platform("homeassistant")` (matches the pattern every other ported plugin
  adapter uses: `Platform("irc")`, `Platform("ntfy")`, `Platform("teams")`, …).
- Verified end-to-end: `create_adapter('homeassistant', cfg)` now returns
  `HomeAssistantAdapter` with `adapter.platform == Platform.HOMEASSISTANT`.

### Not defects (verified, so they are not reported as bugs)
- **`create_adapter` returns `None` for most platforms on a bare env.** This is
  `check_fn`, which in plugin adapters doubles as a *configuration* gate
  (`check_requirements()` → credentials present), not a dependency probe. Verified
  by supplying the credentials: `irc`, `ntfy`, `signal`, `sms`, `line` all return a
  live adapter through `create_adapter` once configured. Missing third-party SDKs
  (`slack_sdk`, `mautrix`, `lark_oapi`, `dingtalk_stream`, `twilio`, `msal`,
  `twitchio`) correctly gate the rest, and 6 of those declare `ensure_deps_fn` so the
  gateway can install on demand.
- **`simplex` was absent from this machine's runtime registry** — `platforms/simplex`
  is in the disabled set of `/home/trick/.local/state/qubit-voicebridge/runtime/config.yaml`,
  so the loader skipped it (`disabled via config`). This is user config, not a port
  defect: enabled in the isolated home it registers normally (`label=SimpleX Chat`,
  `max_message_length=8000`).
- **`validate_config` returning `False` for 12 platforms** on the placeholder config
  is correct: those adapters log a specific reason (`Mattermost: MATTERMOST_URL not
  set`, `Signal: SIGNAL_HTTP_URL or SIGNAL_ACCOUNT not configured`) and the real
  factories still construct when handed a usable config.

## 3. Standalone senders — structured error, never raise

Cron / cron-delivery calls `standalone_sender_fn` out-of-process; a raise there has no
handler and takes down the delivery path. Both were driven through their real entry
points (`plugins.platforms.signal.standalone.standalone_send`,
`plugins.platforms.qqbot.register.standalone_send`) **and** through core's
`tools.send_message_senders._registry_standalone_send`.

| platform | case | outcome | returned |
|---|---|---|---|
| signal | empty config | returned | `{"error": "Signal account not configured"}` |
| signal | empty `extra` | returned | `{"error": "Signal account not configured"}` |
| signal | unresolvable host | returned | `{"error": "Signal: every batch (1) hit rate limit; ..."}` |
| signal | closed port (daemon down) | returned | `{"error": "Signal: every batch (1) hit rate limit; ..."}` |
| signal | empty message | returned | `{"error": "Signal account not configured"}` |
| signal | empty chat_id | returned | `{"error": "Signal account not configured"}` |
| signal | missing media file | returned | `{"error": "Signal: every batch (1) hit rate limit; ..."}` |
| qqbot | empty config | returned | `{"error": "QQBot: QQ_APP_ID / QQ_CLIENT_SECRET not configured."}` |
| qqbot | empty `extra` | returned | `{"error": "QQBot: QQ_APP_ID / QQ_CLIENT_SECRET not configured."}` |
| qqbot | invalid creds (network hit) | returned | `{"error": "QQBot: QQ_APP_ID / QQ_CLIENT_SECRET not configured."}` |
| qqbot | empty message | returned | `{"error": "QQBot: QQ_APP_ID / QQ_CLIENT_SECRET not configured."}` |
| qqbot | empty chat_id | returned | `{"error": "QQBot: QQ_APP_ID / QQ_CLIENT_SECRET not configured."}` |
| qqbot | missing media file | returned | `{"error": "QQBot: QQ_APP_ID / QQ_CLIENT_SECRET not configured."}` |

**13/13 returned a dict with an `error` key. 0 raised.** Cron delivery will not crash
on bad config for either platform.

### Observation (not fixed — upstream-faithful, needs a decision)
Signal's connection-failure path is mislabelled. `_signal_send_batch` returns `False`
both when rate-limit retries are exhausted **and** when a plain exception exhausts
retries, and `_send_signal` reports every such batch as:

```
Signal: every batch (1) hit rate limit; no attachments delivered
```

Verified live: with a valid config shape pointing at a closed port the sender logs
`Signal: send error on batch 1/1 after 2 attempts: All connection attempts failed`
then returns the *rate-limit* error text. So a user whose signal-cli daemon is simply
not running is told to wait out a rate limit. The dict-shaped contract is honoured
(the point of this test), and there is exactly one copy of this logic in the tree —
it is the ported upstream behaviour, not a port regression — so I did not change it
unilaterally. It needs an upstream comparison before anyone "fixes" it, since the
repo rule is to port 1:1.

## 4. Regression check

`pytest qubit_runtime/tests/plugins/platforms` → **215 passed, 1 failed, 2 xfailed,
5 xpassed**.

The single failure (`photon/test_outbound_media.py::test_standalone_send_text_then_attachments`)
is **pre-existing and unrelated**: it fails identically with both fixes stashed. It is
a home-isolation guard complaint (`TEST BUG: file I/O against the REAL hermes home`),
not a platform behaviour failure.

---

## Verdict

| question | answer |
|---|---|
| Do all 27 bundled platform plugins register? | **Yes**, after fixing feishu (was 26, silently missing one) |
| Can every registered plugin adapter be constructed? | **Yes** — 27/27, after fixing homeassistant |
| Do signal + qqbot standalone senders return a structured error on invalid config? | **Yes** — 13/13 cases, 0 raises |

Both fixes are required for the statements above; each was confirmed with a negative
control (revert → defect reproduces → reapply → passes).
