# Qubit Voice Agent — tiny, clear guide

Qubit is a local helper. You speak or type. It thinks with a local model. It
does only the actions that are allowed. It shows the result in your terminal,
voice, or dashboard.

## 1. The simple picture

```text
you → microphone / keyboard / dashboard
  → voice-to-text
  → local model
  → permission check
  → read-only tool or approved action
  → result, report, voice, or dashboard
```

The model can suggest an action. The permission broker makes the final choice.
This is important: “local” does not mean “allowed to do everything.”

## 2. What it can do now

- Listen through the microphone and transcribe speech.
- Answer by text and Piper voice.
- Run a typed task through the local model.
- Use the Right Ctrl push-to-talk key.
- Use the authenticated web dashboard.
- Read approved local files, projects, reports, and other configured sources.
- Use bounded memory, reports, logs, skills, and typed failure states.
- Inspect supported browser tabs in a user-owned Chromium session when the
  browser path is configured. The user logs in; Qubit does not receive the
  password.
- Prepare controlled actions. Sensitive actions need a clear human
  confirmation.
- Export and import portable state with a passphrase.

Some packet features are optional, disabled by default, or still need live
operator testing. A green hermetic test is not proof that your real microphone,
browser account, or speaker works.

Packets are work-and-test steps, not separate programs. The completed/current
surface covers browser qualification (8G), audio qualification (8H),
notifications (8I), recovery (8J), release checks (8K), and dashboard UX
(8L). Later ideas such as semantic memory (8M), Ghost/Comet research adapters
(8N), broader computer use (8O), and Praxis/Evolve evaluation (8P) are separate
projects. They must be added and tested one at a time; they are not magically
enabled by this guide.

## 3. What it must not pretend to do

Qubit is not an all-powerful robot. It must not:

- Guess passwords, bypass CAPTCHA or 2FA, or steal cookies/tokens.
- Send, delete, buy, publish, or change things silently.
- Treat web-page text as instructions or grant itself new permissions.
- Start a personal browser profile or use private accounts without the
  configured, approved path.
- Claim that a browser, microphone, model, timer, or speaker worked when it
  was not really tested.
- Install hidden skills, train itself on private data, or save raw private
  audio/page text by surprise.

Future packets can add carefully bounded adapters for more sites, research,
notifications, and computer-use workflows. Each one needs its own scope,
confirmation, redaction, tests, and rollback. The project does not promise
that every website or account will work forever.

## 4. Install for development or school

The easiest option for another person is a signed release artifact when one is
available. For source-code development:

```bash
git clone <repository-or-private-release-url>
cd qubit-voicebridge
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[test]"
```

The normal runtime also needs the documented local model and audio tools. Read
these before setup:

- `docs/AUDIO_SETUP.md` — microphone and speaker setup.
- `docs/VOICE_BRIDGE_COMPLETE_GUIDE.md` — the longer explanation.
- `docs/VOICE_BRIDGE_TROUBLESHOOTING.md` — common fixes.
- `docs/OPERATIONS_8L_CHECKLIST.md` — real live checks.

If a packaged release is provided, install that package and run:

```bash
qubit product install
qubit product first-run
```

Use `python -m voicebridge` instead of `qubit` when the command is not
installed on your `PATH`. (`voicebridge` still works as a compatibility
alias for `qubit`: same commands, same backend.)

## 5. Start it

From the project with the virtual environment active:

```bash
qubit product first-run
qubit status
qubit run
```

`run` starts the normal push-to-talk mode. Hold Right Ctrl, speak, and release.
Press `Esc` to cancel. You can also use the text task command:

```bash
qubit task "summarize my project notes"
qubit once
qubit mic-test
```

Start the dashboard with the existing bridge:

```bash
qubit webui --open
```

Then open the local address printed by the command, usually
`http://127.0.0.1:8890/`. Pair/authenticate as documented. Do not put the API
token in the URL or paste it into screenshots.

If the bridge is managed by a user service, inspect the unit name first and
then use that exact name:

```bash
qubit product service
systemctl --user status qubit-voicebridge.service --no-pager -l
systemctl --user restart qubit-voicebridge.service
```

Only restart a service when you intend to interrupt current work.

## 6. The main commands

Run this to see every top-level command installed in your copy:

```bash
qubit --help
```

The main groups are:

```text
run once mic-test task asr-test tts-test status roundtrip enroll verify
flags flag-drill package product audio profile branding permissions skills
memory websites actions export-state import-state device monitor webui
```

Useful examples:

```bash
# Health and end-to-end checks
qubit status
qubit roundtrip --text "say hello"
qubit asr-test recording.wav
qubit tts-test "hello" --out /tmp/qubit-test.wav

# Audio devices and safe selection
qubit audio list
qubit audio status
qubit audio check
qubit audio select mic "<device shown by audio list>"
qubit audio select output "<device shown by audio list>"
qubit audio reset

# Product setup and portability
qubit product paths
qubit product capabilities
qubit product repair
qubit product shortcut
qubit product tray
qubit product boundary
qubit export-state --out qubit-state.bundle
qubit import-state qubit-state.bundle
```

Use `--help` after a group for its exact options, for example:

```bash
qubit audio --help
qubit product --help
qubit permissions --help
```

## 7. Permissions, skills, websites, and actions

These commands are deliberately careful. A change is normally a dry run or
needs `--confirm` (or the command's documented apply flag).

```bash
# Permissions: restricted is the safe default
qubit permissions mode
qubit permissions list
qubit permissions check
qubit permissions grant <capability> --confirm
qubit permissions revoke <capability> --confirm
qubit permissions reset --confirm

# Skills: inspect first; install starts disabled
qubit skills list
qubit skills show <name>
qubit skills check <name>
qubit skills install <manifest>
qubit skills enable <name> --confirm

# Websites: currently an onboarding/proposal and observability surface
qubit websites add <url>
qubit websites list
qubit websites show <id>
qubit websites inspect <id>
qubit websites check <id>
qubit websites logs <id>
qubit websites probe <id> --live

# Controlled actions: preview before prepare/execute
qubit actions list
qubit actions check <action>
qubit actions preview <action>
qubit actions prepare <action> --confirm
qubit actions execute <confirmation-id> --confirm
qubit actions receipts
```

Pasting a website URL does not log in, grant access, train the model, or make a
hidden skill. Browser work is read-only unless a separately approved action
exists. Never use `--confirm` unless you understand exactly what it will do.

## 8. Memory, reports, browser, and monitoring

```bash
# Memory is bounded and optional; mutations are normally dry-run
qubit memory status
qubit memory explain
qubit memory export

# Scheduled communications monitor
qubit monitor health
qubit monitor run

# Browser/report helpers (use only with the documented local setup)
python scripts/tab_report.py <target> --out report.md
python scripts/generate_report.py --task "<task>" --status completed \
  --summary "<short summary>" --out report.md
python scripts/browser_auto.py <documented-command>
```

For browser work, use the supported user-launched Chromium/CDP path and the
runbook in `docs/Chromium-Operations.md`. The account owner logs in manually.
The future Comet MCP path is a separate research/browser adapter; it is not a
free replacement for every website API and is not automatically enabled.

## 9. Find files and troubleshoot

Qubit chooses normal per-user paths. Do not copy this author's `/home/...`
paths to another computer. Ask Qubit where it is using:

```bash
qubit product paths --json
qubit product capabilities --json
qubit audio list
qubit audio status
qubit webui --json
journalctl --user -u qubit-voicebridge.service -n 100 --no-pager
```

The product paths command shows config, state, model/data, and any legacy path.
The exact path is different for every user and operating system.

If voice fails, check the selected input and output device, microphone browser
permission, model availability, and the service journal. If the dashboard says
`unauthorized`, pair it with the token file described by the setup docs; do not
visit `/api/status` without the authentication header.

## 10. How to share it

For a school demonstration, share a release archive or a private repository,
plus:

- this guide and the setup/troubleshooting docs;
- the license and `THIRD_PARTY_NOTICES.md`;
- the model/provider setup instructions;
- a small test checklist.

Never share your API token, `.env` files, state directory, browser profile,
cookies, passwords, private reports, private recordings, or personal model
data. Each person installs the program, creates their own folders, selects
their own audio devices, and logs into their own accounts.

The original Qubit license applies only to Qubit code. Code copied from another
project keeps that project's license and copyright notice. Check
`docs/LICENSING.md` before redistributing anything.

## 11. The honest bottom line

Today Qubit is a local voice-and-text assistant with a dashboard, a local
model, controlled tools, reports, and optional browser/read-only features. It
can become a stronger personal computer assistant through separate, tested
adapters for research, websites, notifications, and approved actions.

It should be fast, clear, and useful. It should also say “not available” when
something was not tested or not allowed. That is a feature, not a failure.
