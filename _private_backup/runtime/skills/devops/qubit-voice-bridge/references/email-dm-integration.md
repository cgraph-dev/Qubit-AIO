# Email + DM integration for the voice bridge (Aug 2026)

User asked the bridge to "verify my emails and tell me which ones are important"
and "check my private messages from social media". This file records what is
actually possible per platform (verified this session), the setup paths, and
what was already wired.

## Current state (verified 2026-08-21)

- Bridge routing: `check-email` / `check-dms` templates exist in
  `config/task-templates.yaml` → dispatch to the executor (Qubit or Qubit,
  per `ask_executor` mode) with a task spec that says "flag IMPORTANT items and
  report what integration is missing".
- **himalaya v2.1.0 installed** (`~/.local/bin/himalaya`, build has `+gmail
  +imap +smtp +rustls-ring`). Multi-account config scaffolded at
  `~/.config/himalaya/config.toml` (personal + work sections, Gmail IMAP/SMTP,
  `backend.auth.keyring = "himalaya-<name>"`).
- NOT wired yet (needs user secrets): no Gmail App Passwords entered, no
  WhatsApp pairing, no Discord bot, Qubit gateway stopped.

## Email — the ONLY fully-legit channel here

Two paths, both official:

1. **himalaya IMAP (recommended, no Google Cloud project)** — each Gmail needs:
   - 2-Step Verification enabled, then an App Password at
     https://myaccount.google.com/apppasswords (one per account)
   - Run the wizard **once per account**:
     `export PATH="$HOME/.local/bin:$PATH"; himalaya configure`
     (top-level command, NOT `himalaya account configure` — that subcommand
     doesn't exist; the bare `configure` is the interactive wizard).
     It prompts: Email → App Password, then writes config + stores the secret
     in the keyring.
   - Gmail folder aliases: inbox=INBOX, sent="Sent Mail", drafts=Drafts,
     trash=Trash.
2. **google-workspace skill** (OAuth) — needs a Google Cloud project + OAuth
   client + consent; better for Calendar/Drive too but clunkier for multiple
   accounts (one token file per account). Only use if himalaya is insufficient.

Wizard pitfall: `himalaya account configure` errors with "unrecognized
subcommand"; `himalaya account list` shows accounts parsed from config.toml;
`himalaya account check` fails with "No backend matching auto" when the
account section structure is wrong — let the wizard write the file instead of
hand-crafting TOML (the shared-API backend section shape is finicky).

## Social platforms — reality check (VERIFIED, do not re-promise)

| Platform | Personal DMs readable? | Path |
|---|---|---|
| WhatsApp | **YES** (official Web protocol) | Qubit extension `@openclaw/whatsapp` already installed (`~/.openclaw/extensions/whatsapp`, v2026.7.1, "pairing" auth mode) → one-time QR pairing. |
| Discord | **PARTIAL** | A bot token reads DMs only where the bot itself is a participant. It CANNOT read the user's private DMs with other people without a ToS-violating self-bot. User can DM their own bot. |
| Instagram | **NO** for personal accounts | Graph API exposes DMs only for **Business** accounts. |
| Facebook | **NO** for personal | Messenger API is Page/business-scoped. |
| TikTok | **NO** | No DM API exists at all. |

Unofficial scraping/selenium of IG/FB/TikTok personal DMs violates ToS (ban
risk on real accounts), breaks constantly, and can't reliably flag importance.
Refuse politely; offer the legit alternative (notifications/public posts via
logged-in browser) or a Business-account API if they ever convert.

## Frontier-API architecture question (user asked, answered)

"Connect the local thinking model with a cheap frontier API → route tasks and
execute commands better/faster? internal internet access? cheap?"

- YES on routing quality: a frontier model understands intent far better than
  a 3B ("verify my emails" vs "patch the webhook" routes correctly).
- NO on "internal internet access": an API is a brain, not hands. Internet,
  email, and DM access come from the AGENT'S TOOLS, not the model. Correct
  mental model: frontier model DECIDES → agent tools EXECUTE.
- YES on cost: keep the local model first (it absorbs ~80% of turns =
  chitchat/basic), bill only the ~20% of hard turns. Cheap APIs (DeepSeek
  class) = pennies/month at this volume. Cost trap: sending long history +
  big outputs to the API — let the local model summarize so the API only sees
  small task text.
- The user's plan is sound for a bigger GPU later: 4-8B local router + frontier
  API for hard tasks. Until then, ask_executor mode + the local 3B is the
  working hybrid.
