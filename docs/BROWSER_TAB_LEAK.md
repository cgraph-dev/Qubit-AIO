# Browser tab leak — root cause

**Status:** mechanism PROVEN by controlled reproduction; the *caller* that loops
`chromium <url>` is NOT proven from in-repo code. Ranked suspects in §5.
**Date:** 2026-11-10
**Symptom:** "something keeps opening new chromium tabs continuously every couple
seconds even though I don't use the PC"; 30 leaked renderers over ~3 days in the
user's main Chromium.

---

## 1. Verdict in one paragraph

The repeating `chrome://newtab/` load is **not** a Qubit tab creation. It is the
signature of the snap Chromium **launcher being invoked repeatedly with a URL**
(`/snap/bin/chromium <url>`), which forwards the URL into the already-running
main browser; Chromium internally turns that into a New Tab request against the
**NORMAL** (non-incognito) profile, and `NewTabUIConfig::IsWebUIEnabled` logs
`Requested load of chrome://newtab/ for incorrect profile type` **once per
invocation** and refuses the page. The tab still gets created; the page does not
render. Every Qubit-adjacent candidate (browser tools, cron, extension,
Playwright MCP, CDP, watchdog) is **ruled out with evidence** in §4. What is
proven is the mechanism and the 1:1 error-per-launch law; what is not proven is
*which* process issues the launches in a loop (§5).

---

## 2. The exact evidence chain

### 2.1 The journal signature is browser-internal, not a tool

All 318 occurrences over Nov 7 → Nov 10 carry the **browser-process main
thread** (`pid:pid`, never a renderer pid):

```bash
journalctl --user --since "2026-11-07 00:00:00" | grep "incorrect profile type" \
  | grep -oE "\[([0-9]+):([0-9]+):" | awk -F'[][:]' '$2==$3{b++} END{print b}'
# 318   (browser-thread), 0 from any renderer
```

Two browser PIDs emit it: `2810149` (the user's main snap Chromium, 269×) and
`2575994` (a Google Chrome launched Nov 8 19:06, 49× — a second independent
confirmation of the same law, see §2.4).

Sample line (`2810149` is the browser, thread id equals the pid):

```
Nov 10 18:04:20 trick chromium_chromium.desktop[2810149]:
  [2810149:2810149:1110/180420.769791:ERROR:chrome/browser/ui/webui/ntp/new_tab_ui.cc:54]
  Requested load of chrome://newtab/ for incorrect profile type.
```

### 2.2 What that Chromium error actually means

From Chromium source, `chrome/browser/ui/webui/ntp/new_tab_ui.cc`,
`NewTabUIConfig::IsWebUIEnabled`:

```cpp
switch (NTPResourceCache::GetWindowType(profile)) {
  case NTPResourceCache::NORMAL:
    LOG(ERROR) << "Requested load of chrome://newtab/ for incorrect profile type.";
    return false;                       // <- the log line, and the refusal
  case NTPResourceCache::INCOGNITO:
  case NTPResourceCache::GUEST:
  case NTPResourceCache::NON_PRIMARY_OTR:
    return true;
}
```

So the line fires **only** when `chrome://newtab/` is requested against a
`NORMAL` profile — i.e. the ordinary user profile, which is exactly the
`Default` profile named `Work` in
`$HOME/snap/chromium/common/chromium/Local State`
(`info_cache.Default.name == "Work"`, `last_used == "Default"`). It is a *tab
creation* event, not a page-load failure of an existing tab.

Corroborating independent reports of the same string as a *tab-open* artifact:
<https://github.com/elie222/rakazo/issues/1387> ("a relaunch always lands on a
New Tab page"), <https://www.mail-archive.com/debian-bugs-rc@lists.debian.org/msg758359.html>.

### 2.3 The launch vector: the snap desktop entry

```
$ grep -h "^Exec=" /var/lib/snapd/desktop/applications/chromium_*.desktop
Exec=/snap/bin/chromium %U
Exec=/snap/bin/chromium
Exec=/snap/bin/chromium --incognito
Exec=/snap/bin/chromium --temp-profile
```

and the HTTP handler default:

```
$ xdg-settings get default-web-browser
chromium_daemon.desktop
$ grep -n "chromium" $HOME/.config/mimeapps.list
x-scheme-handler/http=chromium_daemon.desktop
x-scheme-handler/https=chromium_daemon.desktop
x-scheme-handler/about=chromium_chromium.desktop
```

The gateway's own Python resolver agrees — `python3 -c "import webbrowser;
webbrowser.get()"` reports `name: chromium`, `args: ['%s']`, `tryorder:
['chromium', 'xdg-open', 'gio', ...]`. So **any** `webbrowser.open(url)` on this
host runs `/snap/bin/chromium <url>` against the user's main profile.

### 2.4 Controlled reproduction (the proof)

Run against the live main browser (pid `1685254`, snap rev 3590). Each row is
one invocation; the newtab count is measured in the following 8–30 s window.

| Invocation | newtab errors produced |
|---|---|
| `/snap/bin/chromium about:blank` | **0** |
| `/snap/bin/chromium` (no args) | **0** |
| `/snap/bin/chromium --new-window` | **1** |
| `/snap/bin/chromium x-scheme-handler/about` | **1** (reproduced 3×) |
| `/snap/bin/chromium chrome://newtab/` | **1** |
| `/snap/bin/chromium chrome://newtab` (no slash) | 0 |
| `/snap/bin/chromium about:newtab` | 0 |

The two errors my own session produced land exactly on my own invocations:

```
2026-11-10T21:02:38 trick chromium_chromium.desktop[1685254]: ... new_tab_ui.cc:54 ...
2026-11-10T21:02:49 trick chromium_chromium.desktop[1685254]: ... new_tab_ui.cc:54 ...
```

and the matching launcher scopes are recorded by systemd in the same seconds:

```
2026-11-10T21:02:31 trick systemd[2235]: Started snap.chromium.chromium-33e6e5b2-....scope
2026-11-10T21:02:46 trick systemd[2235]: Started snap.chromium.chromium-546bc9a1-....scope
```

**Law: one `chromium <tab-producing-url>` invocation ⇒ one `snap.chromium.chromium-*.scope`
⇒ one `new_tab_ui.cc:54` error ⇒ one extra tab/renderer in the main browser.**

### 2.5 The renderer leak is the same events, counted

`snap.chromium.chromium-*` scope starts per hour on Nov 10:

```
1 2026-11-10T05   7 2026-11-10T05       <- (one hour, split)
2 2026-11-10T06   5 2026-11-10T07   1 2026-11-10T08
2 2026-11-10T16   2 2026-11-10T17  20 2026-11-10T18   2 T19, 2 T20
```

20 launcher invocations in the 18:00 hour; 16 newtab errors in the
17:50–18:40 window against 18 scope starts in the same window. The ratio is
~1:1, and the burst the user saw at 18:04:55/18:06:00/18:06:01 maps to the
scopes at `18:04:54`, `18:06:00`, `18:06:20`:

```
2026-11-10T18:04:54 Started snap.chromium.chromium-91e34cb5-...
2026-11-10T18:06:00 Started snap.chromium.chromium-2e85a637-...
2026-11-10T18:06:20 Started snap.chromium.chromium-0079e9a9-...
```

A tab created this way is *added* to the existing window session, and the
session store confirms both URL forms are present
(`chrome://newtab/` ×14, `chrome://new-tab-page` ×28, `New Tab` title ×14 in
`Default/Sessions/Tabs_13438809297923349`). Renderers are never closed, so over
three days of sporadic launches the count only grows — the observed 30.

**Live confirmation of the accumulation:** the browser relaunched at 20:34:54
with **4** renderers. By 21:05, after **17** launcher invocations (the
reproduction runs above), it holds **29** renderers — the same one-way ratchet,
reproduced inside this session rather than merely inferred:

```bash
$ pgrep -c -f 'snap/chromium/3590.*type=renderer'   # at 20:35 -> 4
$ pgrep -c -f 'snap/chromium/3590.*type=renderer'   # at 21:05 -> 29
$ journalctl --user --since "2026-11-10 20:35:00" | grep -c "Started snap.chromium.chromium-.*scope"
# 17
```

---

## 3. The 18:00 cron correlation — **rejected**

The `Daily watch video` job fires at 18:00 and the burst was ~18:04, which looks
like a match. It is not.

* The job is `no_agent: true` with `script: watch_video_of_day.py`. That script
  (`$HOME/.local/state/qubit-voicebridge/runtime/scripts/watch_video_of_day.py`)
  is 177 lines of `print()`/`json.dumps` — it imports `argparse, json, sys,
  datetime, zoneinfo` and **never touches a browser**.
* Its actual run log for that evening is a failure, and it returned in ~3 s:
  `$HOME/.local/state/qubit-voicebridge/runtime/cron/output/721c3d035c22/2026-11-10_18-00-52.md`
  → `Status: script failed / dependency environment interpreter is missing:
  .../qubit_runtime/venv/bin/python`.
* `executions.db` confirms it started `18:00:52` and was done by `18:00:53.635`.
  The first burst error is `18:03:23` — **2.5 minutes later**, and further bursts
  land at 18:18:36 and 18:29:35, which are *not* on any 18:00-aligned schedule.

The apparent correlation is coincidence: **every** :00 hour has launcher activity,
and the 18:00 hour simply happened to be the busiest. Same story for
`office autowork tick` (fails in 1.4 s, no browser) and `wave1-job`
(prompt is literally `" summarize notes"`, `toolsets: None`, completed 18:14:52 —
*after* the 18:14:06 burst).

---

## 4. All Qubit candidates — ruled out, with evidence

| # | Candidate | Verdict | Evidence |
|---|---|---|---|
| 1 | `browser_tool.py` / `browser_cdp_tool.py` / `browser_use_cli` / `browser_automation` | **Ruled out** | No CDP endpoint exists to drive: `curl http://127.0.0.1:9222/json/version` → no listener; `ss -ltn` shows nothing on 9222/9333; the main browser's argv has no `--remote-debugging-port`. Without a debug port, `Target.createTarget` cannot reach it at all. |
| 2 | Cron jobs (9) | **Ruled out** | Bodies read in full. Browser-touching = none. `watch_video_of_day.py` is print-only; `office_autowork_tick.py`/`vault_prune.py`/`reminder_due.py` never open a browser. See §3. |
| 3 | browser-companion extension (`chrome.tabs.create`) | **Ruled out** | Not installed. `Default/Extensions/` holds only `gighmmpiobklfepjocnamgkkbiglidom` (AdBlock 6.52.0) and `iphcomljdfghbkdcfndaijbokpgddeno` (Cookie Editor 2.2.0.0, no `tabs` permission). No Qubit extension id in `Default/Preferences`. Its own code never navigates to `newtab` (`grep -c "chrome://newtab\|about:newtab" sidepanel.js` → 0). |
| 4 | Playwright MCP auto-spawned by the gateway | **Ruled out** | Two `npm exec @playwright/mcp@latest` are live (pids 1739169 ← `voicebridge run` 1738760, 1739580 ← `gateway_daemon` 1738759) but they use **isolated** profiles under `~/.cache/ms-playwright-mcp/mcp-chrome-*`, whose newest mtime is `2026-11-10 06:54` — untouched during every 18:xx burst. They never touch `snap/chromium/common/chromium`. |
| 5 | Watchdog / retry loops | **Ruled out** | `voicebridge/cdp_supervisor.py` looks like the culprit (`CDP_NEW_TAB_URL = .../json/new`, `reconcile()` → `open_pinned_tab`) but it is **dead code**: no process matches `cdp_supervisor`, `grep` finds no importer anywhere in the repo, and `/tmp/qubit-voicebridge/cdp_health.json` does not exist. `tools/browser_supervisor.py:456` creates targets only on a live CDP socket, which does not exist (row 1). |
| 6 | **`voicebridge/browser_tabs.py`** (`open()` → CDP `PUT /json/new`) — the installed build's tab-opening surface | **Ruled out** | Present in both tree and install (`voicebridge/browser_tabs.py:678`). Every mutating entry point is gated: `open()` calls `_require_confirmed(confirmed, "open")` first (`:137`) and raises `CONFIRM_CODE` when `confirmed` is falsy; it also short-circuits on `estop_engaged(cfg)`. `http_json` targets `CDP_PORT = 9222` (`voicebridge/cdp.py:27`) which has no listener. The auto-poll callers (`cli.py:8257`, `computer_use.py:618`, `voice_commands.py:2590/3577`) all call the **read-only** `BT.tabs()`. No `while True`/`for ... in range` loop exists in the module. |
| 7 | Installed build differs from worktree | **Ruled out as a hiding place** | `voicebridge/browser_tabs.py` is byte-identical in both (73332 B, same mtime). The installed `webbrowser.open` call sites are the same set as the worktree's, all explicit-flag gated (`--open`, `no_open`). |

---

## 5. What is NOT proven — ranked suspects for the looping caller

The mechanism is proven; the *invoker* is not, because the launches leave no
journal-visible parent (each appears as a bare `systemd Started snap.chromium.*`
scope). Ranked:

1. **A Qubit agent turn calling `webbrowser.open` in a retry/reconnect loop**
   (most likely). The gateway/voicebridge do open browsers through
   `webbrowser.open` in several places —
   `qubit_runtime/qubit_cli/webapp_launcher.py:100`,
   `qubit_runtime/tools/mcp_oauth.py:781`,
   `voicebridge/cli.py:12337`, `voicebridge/auth_login.py:137`,
   `qubit_runtime/qubit_cli/auth_*`. On this host that resolves to
   `/snap/bin/chromium <url>` (§2.3). An OAuth flow that re-opens the
   authorization URL when its callback never completes would produce exactly this
   cadence. **Counter-evidence:** these are all gated on explicit flags
   (`--open`, `no_open`), and no in-repo loop over them was found.
2. **A non-Qubit launcher on the desktop session** — the only category with no
   in-repo evidence either way. A GNOME/`snapd` handler or another `.desktop`
   consumer re-opening a URL. Supporting: Nov 8 19:07 shows **Google Chrome**
   launched "by gnome-shell" producing an identical 33-error cluster from a
   different browser PID (2575994), which is independent of Qubit entirely.
   Decisive test — catch the parent at the next burst:
   ```bash
   sudo auditctl -w /snap/bin/chromium -p x -k tablaunch
   sudo ausearch -k tablaunch -i | tail -40     # ppid + parent comm
   ```
3. **The out-of-repo venv copy — checked and cleared.** The running
   gateway is `$HOME/venvs/qubit-0.0.13/bin/python` with cwd
   `$HOME/.local/share/qubit-live-013`, i.e. the *installed* build, not
   this worktree. I inspected it: its `webbrowser.open` call sites are the same
   flag-gated set as the tree's, and its one tab-creating module
   (`voicebridge/browser_tabs.py`) is byte-identical to the tree's and
   confirm-gated. The loop is **not** hiding in a diverged install.

---

## 6. Fix

### 6.1 Proven defect in this repo — dead, dangerous CDP-tab-opening module

`voicebridge/cdp_supervisor.py` is dead code that *would* open tabs in the
owner's real profile via `http://127.0.0.1:9222/json/new`
(`CDP_NEW_TAB_URL`, line 31; `open_pinned_tab()` lines 211–219; called by
`reconcile()` line 234). If anything ever imports it while a debug-port Chromium
is up, it is a tab-opening loop by construction.

**This report does not change it** — deleting a module is a separate decision
and there is no proof it ran. Recommended follow-up, not applied here.

### 6.2 The actual lever: stop the launcher from being the path

The repeatable, safe, code-free fix is to give Qubit's `webbrowser.open` call
sites a target that **cannot** spawn launcher tabs — reuse the existing tab
instead of invoking `chromium <url>`:

* Set a dedicated `BROWSER` for the gateway/voicebridge units so
  `webbrowser.get()` does not resolve to `chromium %s`. The cheapest safe value
  is an explicit **new-tab-in-existing-window** command, or simply disable
  auto-open (`no_open`) where the code already supports it.
* Alternatively pin `browser.cdp_url` to a *broken* value? No — leave it empty,
  as it effectively is (nothing listens), and never wire `webbrowser` to the
  real profile.

**Safe without restart?** Editing the unit environment requires
`systemctl --user restart qubit-gateway qubit-voicebridge` — which this task
forbids. `BROWSER` can be applied at next natural restart. Nothing in the fix
touches user data; no browser needs to be killed.

### 6.3 If a loop is confirmed in the installed build

Kill the *loop*, not the browser: identify the calling PID
(`journalctl --user --since -2s | grep scope` gives the second; correlate with
`/proc/*/cmdline` for a python in `qubit-live-013`) and stop that job/turn.

---

## 7. Reproducing / monitoring

```bash
# Is the leak happening? (one line per leaked tab)
journalctl --user --since "-10m" | grep "incorrect profile type"

# Which launcher invocations accompanied them?
journalctl --user --since "-10m" | grep "Started snap.chromium.chromium-.*scope"

# Tab-per-invocation law, harness (safe: creates a tab in your own browser)
/snap/bin/chromium --new-window; sleep 2
journalctl --user --since "-5s" | grep -c "incorrect profile type"   # expect 1
```

A healthy state is `0` for both counts. Post-relaunch on snap 3590 this repo's
baseline was zero errors from 20:34 until the controlled tests above.
