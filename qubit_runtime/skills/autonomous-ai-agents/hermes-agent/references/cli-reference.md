# Qubit CLI Reference

Live sources when anything looks stale: `qubit --help`, `qubit <command> --help`,
https://github.com/cgraph-dev/Qubit-AIO/tree/main/docs/reference/cli-commands

### Global Flags

```
qubit [flags] [command]        (no subcommand = interactive chat)

  --version, -V             Show version
  -z, --oneshot PROMPT      One-shot: print ONLY the final response (for scripts/pipes)
  -m MODEL  --provider P    Model/provider override for this invocation
  -t, --toolsets LIST       Comma-separated toolsets for this invocation
  --resume, -r SESSION      Resume session by ID or title
  --continue, -c [NAME]     Resume by name, or most recent session
  --worktree, -w            Isolated git worktree mode (parallel agents)
  --skills, -s SKILL        Preload skills (comma-separate or repeat)
  --profile, -p NAME        Use a named profile
  --yolo                    Skip dangerous command approval
  --tui / --cli             Force the Ink TUI / classic REPL
  --ignore-rules            Skip AGENTS.md/SOUL.md/memory/skill injection
  --safe-mode               Disable ALL customizations (troubleshooting)
  --pass-session-id         Include session ID in system prompt
```

### Chat

```
qubit chat [flags]
  -q, --query TEXT          Single query, non-interactive
  --image PATH              Attach a local image to a single query
  -Q, --quiet               Suppress banner, spinner, tool previews
  --checkpoints             Enable filesystem checkpoints (/rollback)
  --max-turns N             Cap tool-calling iterations
  --source TAG              Session source tag (default: cli)
```
(plus the global flags above)

### Configuration

```
qubit setup [section]      Wizard (model|tts|terminal|gateway|tools|agent)
qubit model                Interactive model/provider picker
qubit fallback [add|remove|list]  Fallback provider chain
qubit config [show|edit|get|set|unset|path|env-path|check|migrate]
qubit login / logout       OAuth sign-in / clear stored auth
qubit doctor [--fix]       Check dependencies and config
qubit status [--all]       Component status
```

### Tools & Skills

```
qubit tools [list|enable NAME|disable NAME]   Per-platform toolsets (curses UI with no args)

qubit skills list|browse|search QUERY|inspect ID
qubit skills install ID    Hub identifier OR a direct https://…/SKILL.md URL
qubit skills config        Enable/disable skills per platform
qubit skills check|update|uninstall|publish PATH
qubit skills tap add REPO  Add a GitHub repo as a skill source
qubit bundles              Skill bundles (one /<name> alias loads several skills)
```

### MCP Servers

```
qubit mcp add NAME (--url or --command) | remove | list | test NAME
qubit mcp catalog | install NAME     Curated catalog install
qubit mcp configure NAME             Toggle tool selection
qubit mcp serve                      Run Qubit as an MCP server
```
Details (transport, tool discovery, catalog): `references/native-mcp.md`.

### Gateway (Messaging Platforms)

```
qubit gateway run|install|start|stop|restart|status|setup
```

20+ platforms: Telegram, Discord, Slack, WhatsApp (Baileys + Business Cloud API), iMessage (Photon — `qubit photon setup`), Signal, Email, SMS, Matrix, Mattermost, Teams, LINE, SimpleX, ntfy, Google Chat, Home Assistant, DingTalk, Feishu, WeCom, Weixin, API Server, Webhooks. Open WebUI connects via the API Server adapter. Most adapters ship under `plugins/platforms/`.
Docs: https://github.com/cgraph-dev/Qubit-AIO/tree/main/docs/user-guide/messaging/

### Sessions

```
qubit sessions list|browse|rename ID TITLE|delete ID|export OUT|prune|stats
```

### Cron / Webhooks

```
qubit cron list|create SCHED|edit ID|pause|resume|run ID|remove|status
    Schedules: '30m', 'every 2h', '0 9 * * *', ISO timestamp
qubit webhook subscribe NAME|list|remove NAME|test NAME
```
Webhook payloads/routes: `references/webhooks.md`.

### Profiles

```
qubit profile list|create NAME (--clone|--clone-all|--clone-from)|use|show|delete
qubit profile rename A B | alias NAME | export NAME | import FILE
qubit profile migrate-identity A B   Retry a completed rename's session/routing identity migration
```

### Credentials & Pools

```
qubit auth                 Interactive credential manager
qubit auth add [PROVIDER]  Add OAuth or API-key credential (qubit, openai-codex, qwen-oauth, …)
qubit auth list|remove P IDX|reset PROVIDER|status
```
Multiple credentials per provider form a pool that rotates automatically and skips exhausted keys.

### Other

```
qubit desktop / gui        Native desktop app
qubit dashboard            Web admin panel + embedded chat (--stop / --status)
qubit proxy                OpenAI-compatible local proxy backed by an OAuth provider
qubit portal               Quick setup / sign in via Qubit Labs
qubit kanban <verb>        Multi-agent work-queue board
qubit project              Named multi-folder workspaces
qubit skin list|use|set    Switch/tweak skins (see references/themes.md)
qubit pets <verb>          Pet mascots (see references/petdex.md)
qubit memory setup|status|off|reset   Memory provider
qubit secrets bitwarden|onepassword   External secret stores
qubit moa                  Mixture-of-Agents slots
qubit hooks / security / backup / import / checkpoints / console
qubit logs [-f] [errors]   View agent/error logs
qubit send                 One-off message through a gateway platform
qubit pairing / plugins / insights / journey / computer-use
qubit acp                  ACP server (IDE integration)
qubit completion bash|zsh|fish
qubit update / uninstall / claw migrate
```

Plugin- and provider-supplied subcommands (e.g. `qubit photon setup`) only appear once their plugin is installed/active.

### Where to Find Things

| Looking for... | Location |
|---|---|
| Config options | `qubit config edit` · [Configuration docs](https://github.com/cgraph-dev/Qubit-AIO/tree/main/docs/user-guide/configuration) |
| Tools / toolsets | `qubit tools list` · [Tools reference](https://github.com/cgraph-dev/Qubit-AIO/tree/main/docs/reference/tools-reference) |
| Skills catalog | `qubit skills browse` · [Skills catalog](https://github.com/cgraph-dev/Qubit-AIO/tree/main/docs/reference/skills-catalog) |
| Provider setup | `qubit model` · [Providers guide](https://github.com/cgraph-dev/Qubit-AIO/tree/main/docs/integrations/providers) |
| Env variables | `qubit config env-path` · [Env vars reference](https://github.com/cgraph-dev/Qubit-AIO/tree/main/docs/reference/environment-variables) |
| Gateway logs | `~/.qubit/logs/gateway.log` (or `qubit logs`) |
| Sessions | `qubit sessions browse` (reads state.db) |
