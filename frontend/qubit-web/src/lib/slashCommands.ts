/**
 * Static slash-command catalog for the `/qubit-chat` composer autocomplete.
 *
 * Derived from the vendored gateway/CLI command registry
 * (`qubit_runtime/qubit_cli/commands.py::COMMAND_REGISTRY`, 110 commands at
 * pin `69948c0057`) — the same registry the `/chat` TUI completer
 * (`SlashCommandCompleter`) reads, so the popup offers exactly what the
 * terminal offers. Descriptions are the registry descriptions verbatim,
 * except two legacy home paths re-pointed at the Qubit home (`/plan`,
 * `/reload-skills`).
 *
 * Data-source note: no `GET /api/chat/slash-commands` route exists, and the
 * live `complete.slash` JSON-RPC method only answers on the retired `/api/ws`
 * socket (`WS_SINGLE_PATH` — see `ChatSidebar.tsx`), which this page never
 * opens. So the composer filters this static catalog client-side instead of
 * fetching per keystroke. The gateway dispatches every name here
 * (`GATEWAY_KNOWN_COMMANDS`), including the nine `gateway_only` rows.
 *
 * `/mcp` and `/models` are not registry names; they complete to their
 * canonical commands (`/reload-mcp`, `/model`) so the popup never inserts a
 * token the backend cannot dispatch.
 */

export interface SlashCommandItem {
  /** Displayed token with leading slash (e.g. "/model"). */
  name: string;
  description: string;
  category: string;
  /**
   * Completion text inserted on accept. Defaults to the name (plus the
   * trailing-space rule in `slashCompletionText`). Aliases and the `/mcp`
   * + `/models` conveniences point at their canonical command instead, so
   * accepting never inserts an undispatched token.
   */
  completesTo?: string;
}

export const SLASH_COMMANDS: SlashCommandItem[] = [
  { name: "/start", description: "Acknowledge platform start pings without a reply", category: "Session" },
  { name: "/new", description: "Start a new session (fresh session ID + history)", category: "Session" },
  { name: "/topic", description: "Enable or inspect Telegram DM topic sessions", category: "Session" },
  { name: "/clear", description: "Clear screen and start a new session", category: "Session" },
  { name: "/redraw", description: "Force a full UI repaint (recovers from terminal drift)", category: "Session" },
  { name: "/history", description: "Show conversation history", category: "Session" },
  { name: "/save", description: "Export the current conversation (bare /save shows usage)", category: "Session" },
  { name: "/retry", description: "Retry the last message (resend to agent)", category: "Session" },
  { name: "/prompt", description: "Compose your next prompt in $EDITOR (markdown), then send it", category: "Session" },
  { name: "/undo", description: "Back up N user turns and re-prompt (default 1)", category: "Session" },
  { name: "/title", description: "Set a title for the current session", category: "Session" },
  { name: "/handoff", description: "Hand off this session to a messaging platform (Telegram, Discord, etc.)", category: "Session" },
  { name: "/branch", description: "Branch the current session (new thread on Discord/Telegram/Slack/Matrix; --here stays here)", category: "Session" },
  { name: "/worktree", description: "Show, list, create, or prune isolated git worktrees", category: "Session" },
  { name: "/compress", description: "Compress conversation context (add 'here [N]' to keep recent N turns; --preview shows what would happen)", category: "Session" },
  { name: "/rollback", description: "List or restore filesystem checkpoints (restores keep your hand-edits; --all overrides)", category: "Session" },
  { name: "/snapshot", description: "Create or restore state snapshots of Qubit config/state", category: "Session" },
  { name: "/export", description: "Export a profile (config, skills, theme) to a shareable archive", category: "Configuration" },
  { name: "/import", description: "Import a shared profile archive as a new profile", category: "Configuration" },
  { name: "/stop", description: "Kill all running background processes", category: "Session" },
  { name: "/pause", description: "Pause new work globally (emergency stop); '/pause off' resumes", category: "Session" },
  { name: "/approve", description: "Approve a pending dangerous command", category: "Session" },
  { name: "/deny", description: "Deny a pending dangerous command (optionally with a reason)", category: "Session" },
  { name: "/bg", description: "Run a prompt in a separate background session", category: "Session" },
  { name: "/btw", description: "Ask a side question about the current conversation without interrupting it", category: "Session" },
  { name: "/agents", description: "Show active agents and running tasks", category: "Session" },
  { name: "/journey", description: "Open the learning journey timeline", category: "Session" },
  { name: "/queue", description: "Queue a prompt for the next turn, or list/edit/rm/move/clear queued prompts", category: "Session" },
  { name: "/steer", description: "Inject a message after the next tool call without interrupting", category: "Session" },
  { name: "/goal", description: "Set a standing goal Qubit works on across turns until achieved", category: "Session" },
  { name: "/heartbeat", description: "Set a recurring prompt that re-enters this session when idle", category: "Session" },
  { name: "/refine", description: "Review this conversation now and save lessons to memory/skills", category: "Session" },
  { name: "/review", description: "Spawn an independent subagent to review the work just discussed (PR, code, docs)", category: "Session" },
  { name: "/loop", description: "Re-run a prompt on a recurring interval in this session", category: "Session" },
  { name: "/plan", description: "Write a markdown implementation plan to the Qubit home plans directory without executing anything", category: "Session" },
  { name: "/moa", description: "Run one prompt through the default Mixture of Agents preset, then restore your model", category: "Session" },
  { name: "/subgoal", description: "Add or manage extra criteria on the active goal", category: "Session" },
  { name: "/status", description: "Show session, model, token, and context info", category: "Session" },
  { name: "/egress", description: "Show Docker egress proxy status", category: "Session" },
  { name: "/context", description: "Show detailed context window view with usage gauge, category breakdown, compression stats, and throughput", category: "Session" },
  { name: "/whoami", description: "Show your slash command access (admin / user)", category: "Info" },
  { name: "/profile", description: "Show active profile name and home directory", category: "Info" },
  { name: "/sethome", description: "Set this chat as the home channel", category: "Session" },
  { name: "/resume", description: "Resume a previously-named session", category: "Session" },
  { name: "/sessions", description: "Browse and resume previous sessions", category: "Session" },
  { name: "/config", description: "Show current configuration", category: "Configuration" },
  { name: "/model", description: "Switch model (session-scoped; --global to persist)", category: "Configuration" },
  { name: "/codex-runtime", description: "Toggle codex app-server runtime for OpenAI/Codex models", category: "Configuration" },
  { name: "/personality", description: "Set a predefined personality", category: "Configuration" },
  { name: "/statusbar", description: "Toggle the context/model status bar", category: "Configuration" },
  { name: "/battery", description: "Toggle a color-coded battery indicator in the status bar", category: "Configuration" },
  { name: "/timestamps", description: "Toggle [HH:MM] timestamps on messages and /history", category: "Configuration" },
  { name: "/diff", description: "Show git changes in the working directory", category: "Info" },
  { name: "/verbose", description: "Cycle tool progress display: off -> new -> all -> verbose", category: "Configuration" },
  { name: "/focus", description: "Toggle focus view — show only your prompt and the final response", category: "Configuration" },
  { name: "/footer", description: "Toggle gateway runtime-metadata footer on final replies", category: "Configuration" },
  { name: "/yolo", description: "Toggle YOLO mode (skip all dangerous command approvals)", category: "Configuration" },
  { name: "/approvals", description: "Show or set the persistent dangerous-command approval mode", category: "Configuration" },
  { name: "/reasoning", description: "Manage reasoning effort and display", category: "Configuration" },
  { name: "/fast", description: "Fast mode — OpenAI Priority Processing / Anthropic Fast Mode (normal/fast/auto/cold)", category: "Configuration" },
  { name: "/skin", description: "Show or change the display skin/theme", category: "Configuration" },
  { name: "/indicator", description: "Pick the TUI busy-indicator style", category: "Configuration" },
  { name: "/voice", description: "Toggle voice mode", category: "Configuration" },
  { name: "/wake", description: "Toggle the 'Hey Qubit' wake word listener", category: "Configuration" },
  { name: "/busy", description: "Control how messages behave while Qubit is working", category: "Configuration" },
  { name: "/tools", description: "Manage tools: /tools [list|disable|enable] [name...]", category: "Tools & Skills" },
  { name: "/toolsets", description: "List available toolsets", category: "Tools & Skills" },
  { name: "/skills", description: "Search, install, inspect, or manage skills", category: "Tools & Skills" },
  { name: "/memory", description: "Review pending memory writes / toggle the approval gate", category: "Tools & Skills" },
  { name: "/bundles", description: "List skill bundles (aliases /<name> for multiple skills)", category: "Tools & Skills" },
  { name: "/pet", description: "Toggle or adopt a petdex mascot (/pet, /pet list, /pet <slug>)", category: "Tools & Skills" },
  { name: "/hatch", description: "Generate a new petdex pet from a description", category: "Tools & Skills" },
  { name: "/learn", description: "Learn a reusable skill from anything you describe (dirs, URLs, this chat, notes)", category: "Tools & Skills" },
  { name: "/init", description: "Generate or update AGENTS.md project instructions from a repo scan", category: "Tools & Skills" },
  { name: "/cron", description: "Manage scheduled tasks", category: "Tools & Skills" },
  { name: "/suggestions", description: "Review suggested automations (accept/dismiss)", category: "Tools & Skills" },
  { name: "/blueprint", description: "Set up an automation from a blueprint template", category: "Tools & Skills" },
  { name: "/curator", description: "Background skill maintenance (status, run, pin, archive, list-archived)", category: "Tools & Skills" },
  { name: "/board", description: "Show the ported board surface (tabs, widgets, roles, event log)", category: "Tools & Skills" },
  { name: "/harness", description: "List starter kits, submit tasks, or inspect harness sessions/files", category: "Tools & Skills" },
  { name: "/viking", description: "Inspect the local context engine (status, tree, search) or add to it", category: "Tools & Skills" },
  { name: "/bot", description: "Delegated bot tasks (ideas, goals, launch, pause, resume, cards)", category: "Tools & Skills" },
  { name: "/webchat", description: "List or inspect webchat sessions", category: "Tools & Skills" },
  { name: "/memory-backend", description: "Inspect the memory backend (status, provider, probe)", category: "Tools & Skills" },
  { name: "/canvas", description: "List, read, or confirm-gated write canvas files", category: "Tools & Skills" },
  { name: "/taskflow", description: "Durable flows and hooks (list, show, pause, resume, trigger, delete, hooks)", category: "Tools & Skills" },
  { name: "/kanban", description: "Multi-profile collaboration board (tasks, links, comments)", category: "Tools & Skills" },
  { name: "/reload", description: "Reload .env variables into the running session", category: "Tools & Skills" },
  { name: "/reload-mcp", description: "Reload MCP servers from config", category: "Tools & Skills" },
  { name: "/reload-skills", description: "Re-scan the Qubit home skills directory for newly installed or removed skills", category: "Tools & Skills" },
  { name: "/browser", description: "Connect browser tools to your live Chromium-family browser via CDP, or switch to Browser Use mode", category: "Tools & Skills" },
  { name: "/plugins", description: "List installed plugins and their status", category: "Tools & Skills" },
  { name: "/commands", description: "Browse all commands and skills (paginated)", category: "Info" },
  { name: "/help", description: "Show available commands (/help skills lists skill commands, /help <text> filters)", category: "Info" },
  { name: "/palette", description: "Open the fuzzy command palette (also Ctrl+P)", category: "Info" },
  { name: "/restart", description: "Gracefully restart the gateway after draining active runs", category: "Session" },
  { name: "/usage", description: "Show token usage and rate limits; `reset` redeems a banked Codex limit reset", category: "Info" },
  { name: "/subscription", description: "View your Qubit plan and change it in the browser", category: "Info" },
  { name: "/login", description: "Sign in with a Qubit account (keeps your connectors)", category: "Info" },
  { name: "/topup", description: "Show your Qubit balance and manage billing on the portal", category: "Info" },
  { name: "/insights", description: "Show usage insights and analytics", category: "Info" },
  { name: "/platforms", description: "Show gateway/messaging platform status", category: "Info" },
  { name: "/platform", description: "Pause, resume, or list a failing gateway platform", category: "Info" },
  { name: "/copy", description: "Copy the last assistant response to clipboard", category: "Info" },
  { name: "/paste", description: "Attach clipboard image from your clipboard", category: "Info" },
  { name: "/image", description: "Attach a local image file for your next prompt", category: "Info" },
  { name: "/update", description: "Update Qubit Agent to the latest version", category: "Info" },
  { name: "/version", description: "Show Qubit Agent version", category: "Info" },
  { name: "/debug", description: "Upload debug report (system info + logs) and get shareable links", category: "Info" },
  { name: "/quit", description: "Exit the CLI (use --delete to also remove session history)", category: "Exit" },
  { name: "/reset", description: "Alias for /new", category: "Session", completesTo: "/new" },
  { name: "/compose", description: "Alias for /prompt", category: "Session", completesTo: "/prompt" },
  { name: "/fork", description: "Alias for /branch", category: "Session", completesTo: "/branch" },
  { name: "/compact", description: "Alias for /compress", category: "Session", completesTo: "/compress" },
  { name: "/snap", description: "Alias for /snapshot", category: "Session", completesTo: "/snapshot" },
  { name: "/tasks", description: "Alias for /agents", category: "Session", completesTo: "/agents" },
  { name: "/learning", description: "Alias for /journey", category: "Session", completesTo: "/journey" },
  { name: "/memory-graph", description: "Alias for /journey", category: "Session", completesTo: "/journey" },
  { name: "/q", description: "Alias for /queue", category: "Session", completesTo: "/queue" },
  { name: "/hb", description: "Alias for /heartbeat", category: "Session", completesTo: "/heartbeat" },
  { name: "/proactive", description: "Alias for /loop", category: "Session", completesTo: "/loop" },
  { name: "/ctx", description: "Alias for /context", category: "Session", completesTo: "/context" },
  { name: "/set-home", description: "Alias for /sethome", category: "Session", completesTo: "/sethome" },
  { name: "/codex_runtime", description: "Alias for /codex-runtime", category: "Configuration", completesTo: "/codex-runtime" },
  { name: "/sb", description: "Alias for /statusbar", category: "Configuration", completesTo: "/statusbar" },
  { name: "/ts", description: "Alias for /timestamps", category: "Configuration", completesTo: "/timestamps" },
  { name: "/generate-pet", description: "Alias for /hatch", category: "Tools & Skills", completesTo: "/hatch" },
  { name: "/suggest", description: "Alias for /suggestions", category: "Tools & Skills", completesTo: "/suggestions" },
  { name: "/bp", description: "Alias for /blueprint", category: "Tools & Skills", completesTo: "/blueprint" },
  { name: "/reload_mcp", description: "Alias for /reload-mcp", category: "Tools & Skills", completesTo: "/reload-mcp" },
  { name: "/reload_skills", description: "Alias for /reload-skills", category: "Tools & Skills", completesTo: "/reload-skills" },
  { name: "/upgrade", description: "Alias for /subscription", category: "Info", completesTo: "/subscription" },
  { name: "/gateway", description: "Alias for /platforms", category: "Info", completesTo: "/platforms" },
  { name: "/v", description: "Alias for /version", category: "Info", completesTo: "/version" },
  { name: "/exit", description: "Alias for /quit", category: "Exit", completesTo: "/quit" },
  { name: "/mcp", description: "MCP servers (completes to /reload-mcp)", category: "Tools & Skills", completesTo: "/reload-mcp" },
  { name: "/models", description: "Switch model (completes to /model)", category: "Configuration", completesTo: "/model" },
];

/**
 * Bare-run picker commands take no trailing space: "/model " would block the
 * picker. Mirrors `SlashCommandCompleter._PICKER_COMMANDS` in
 * `qubit_runtime/qubit_cli/commands_completion.py`.
 */
const PICKER_COMMANDS = new Set(["/model", "/personality", "/skin"]);

/** Text to insert into the composer when `item` is accepted. */
export function slashCompletionText(item: SlashCommandItem): string {
  const base = item.completesTo ?? item.name;
  return PICKER_COMMANDS.has(base) ? base : `${base} `;
}

/**
 * Find the slash token under the caret, anywhere in the line.
 *
 * The composer used to test `/^\/[A-Za-z0-9_-]*$/.test(input)`, which only
 * matched when the `/` was the FIRST character of the whole input — so typing
 * `/` after any other character never opened the palette. The `/chat` terminal
 * completer finds the token wherever it sits; this is the same behaviour.
 *
 * Returns `null` when there is no active token at the caret. A token must start
 * at the beginning of the string or just after whitespace, so a URL like
 * `http://x` never opens the palette, and it must run to the caret with no
 * space in between (so `/model arg` closes it once an argument is typed).
 */
export function slashTokenAt(
  input: string,
  caret?: number,
): { token: string; start: number } | null {
  return prefixedTokenAt(input, "/", caret);
}

/**
 * Find the `@`-mention token under the caret, anywhere in the line.
 *
 * Same boundary rules as :func:`slashTokenAt` — the mention hint had the
 * identical start-anchored bug (`/^@[A-Za-z0-9_-]*$/` on the whole input), so
 * `@` typed after other text never showed the hint.
 */
export function atTokenAt(
  input: string,
  caret?: number,
): { token: string; start: number } | null {
  return prefixedTokenAt(input, "@", caret);
}

function prefixedTokenAt(
  input: string,
  prefix: string,
  caret?: number,
): { token: string; start: number } | null {
  if (!input) return null;
  const end = caret === undefined ? input.length : caret;
  if (end < 0 || end > input.length) return null;
  // Walk back from the caret to the start of the current word.
  let start = end;
  while (start > 0 && !/\s/.test(input[start - 1])) start -= 1;
  const word = input.slice(start, end);
  const escaped = prefix.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  if (!new RegExp(`^${escaped}[A-Za-z0-9_-]*$`).test(word)) return null;
  // Require a real boundary: start-of-input or whitespace before the prefix.
  if (start > 0 && !/\s/.test(input[start - 1])) return null;
  return { token: word, start };
}

/**
 * Filter the catalog for a composer token like "/mod": prefix matches first
 * (registry order kept), then substring matches. Empty query (bare "/")
 * returns the head of the registry.
 */
export function filterSlashCommands(query: string, limit = 12): SlashCommandItem[] {
  const q = query.toLowerCase().replace(/^\//, "");
  const prefix: SlashCommandItem[] = [];
  const sub: SlashCommandItem[] = [];
  for (const item of SLASH_COMMANDS) {
    const bare = item.name.slice(1).toLowerCase();
    if (bare.startsWith(q)) prefix.push(item);
    else if (q !== "" && bare.includes(q)) sub.push(item);
  }
  return [...prefix, ...sub].slice(0, limit);
}
