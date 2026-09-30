# Voice Dashboard — PTT modes + classifier traps (Aug 2026, second pass)

Follow-on to voice-dashboard-build.md: the click-to-talk work, the
possessive-apostrophe template trap, and the `qubit` launcher.

## PTT mode selector (click-to-talk default, hold kept)

User requirement: "it needs to be click to talk and click to stop on top
of hold cos maybe we access it through pc and we cant hold on pc. so add
this and keep both options."

- The chat page button (`#ptt`) has TWO modes, switched by a small
  `#modebtn` toggle under the mic button:
  - **click** (default): pointerdown toggles — press = start recording,
    press again = stop & send. Works with a mouse AND a touchscreen.
  - **hold**: pointerdown = start, pointerup = stop & send.
- Mode persisted in `localStorage` (`vdb_ptt_mode`).
- `updatePttUI()` rewrites button label + hint live; called from
  `startRec()`/`stopRec()` so the label reflects recording state.
- The OLD logic used a 120ms timer to guess hold-vs-tap and silently
  flipped `pushMode` — confusing; replaced with the explicit selector.

Editing reminder: the bridge serves the frontend LIVE from the
voice-dashboard repo (`~/Projects/Repos/voice-dashboard/frontend/`), so a
page refresh picks up edits — no bridge restart. Verify with
`curl -s http://127.0.0.1:8890/ | grep -c modebtn`.

## Possessive-apostrophe template trap

ASR turned "check my projects status" into "check my **project's** status".
The `check-projects-status` template regex `check (my )?(projects|repos)
status` does NOT match `project's` (the `\b` after `projects` fails before
the apostrophe). The template was skipped; the classifier's
`_system_command` then matched "status" and the user got a bridge-health
readout instead of the intended project-status task.

Fix: possessive-tolerant alternations (`projects?('s)?`). Verify with the
EXACT ASR output (apostrophe included), not the idealized phrase.
Symptom in events.jsonl: no `routing.template` event + `routing.decision
kind=system` for a phrase the user clearly meant as a task.

Related (same pass): `TASK_HINTS` plural fix — `\bfolder\b` doesn't match
"folders"; noun alternations need plurals (`folder|folders`,
`issue|issues`, `file|files`, `repo|repos`), and the verb alternation
needs listing verbs (`list|show|find|search|count|summarize|report|
describe`) or "list the top-level folders in the projects directory"
scores 0 → conversation.

## `qubit` one-word launcher

User: "when I write Qubit I want the dashboard to open." Implemented as a
REAL script at `~/.local/bin/qubit` (cd into `~/Projects/Repos/Qubit (2)/`
+ `exec .../voice-dashboard/open.sh`) — NOT a `.bashrc` function.

Why: the login shell on this machine reads `~/.profile`, which sources
`.bashrc` only under conditions; an appended `.bashrc` function never
loaded in the user's interactive terminal ("Command 'qubit' not found")
even though `bash -i -c 'type qubit'` passed. A script on PATH
(`~/.local/bin` is on PATH) can't be skipped by shell-config quirks.

Lesson for user-facing shell commands on this box: prefer
`~/.local/bin/<name>` scripts over `.bashrc` aliases/functions, and verify
with `which qubit` (not just a `bash -i` probe).
