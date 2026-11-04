/**
 * /guide — how to actually use Qubit.
 *
 * This replaces the old sidebar "Documentation" entry, which pointed at
 * /docs. That page is not broken — it renders the served OpenAPI document
 * (450 paths / 520 operations) as a searchable endpoint dump — but it answers
 * a question only a developer consuming the HTTP API asks. A person opening
 * the sidebar wants to know where to type, how to talk to Qubit, how to make
 * it do something later, and how to give it a memory.
 *
 * /docs stays mounted as the API reference (linked from the footer of this
 * page and from the copy in the docs row); /guide is the human entry point.
 *
 * Every claim here is one the dashboard backs: the routes named in the "Where
 * things live" rows are the sidebar's own paths, and the voice claims match
 * the Voice & Speech surface.
 */
import {
  SettingsPage,
  SettingsPageHeader,
  SettingsRow,
  SettingsSection,
  SettingsValue,
} from "@/components/console-ui";

function RouteLink({ to, children }: { to: string; children?: React.ReactNode }) {
  return (
    <a href={to} className="btn btn--ghost btn--sm">
      {children ?? to}
    </a>
  );
}

export default function GuidePage() {
  return (
    <SettingsPage wide>
      <SettingsPageHeader
        title="Guide"
        description="Qubit is one agent with several ways in: a terminal chat, a voice bridge, scheduled jobs, and surfaces that show you what it is doing. Start where you work most — everything below is already running."
      />

      <SettingsSection
        title="Start here"
        testId="guide-start"
        description="Three ways to talk to the same agent. They share one session store, so a conversation started in one is visible from the others."
      >
        <SettingsRow
          title="Chat"
          description={
            <span>
              The embedded terminal — this is Qubit itself, not a web rewrite.
              Type a request, watch tools run, approve what needs approving.
              Everything else in the dashboard is a view onto this agent.
            </span>
          }
          control={<RouteLink to="/chat">Open Chat</RouteLink>}
          testId="guide-row-chat"
        />
        <SettingsRow
          title="Voice & Speech"
          description={
            <span>
              Hold <SettingsValue mono>Right Ctrl</SettingsValue> (or click
              the orb) to speak, and Qubit answers out loud. Wake word, speech
              settings and voice editing live here. The same lane answers
              across the bridge, so voice and text share one context.
            </span>
          }
          control={<RouteLink to="/qubit-talk">Open voice</RouteLink>}
          testId="guide-row-voice"
        />
        <SettingsRow
          title="Qubit Chat"
          description="A separate voice-first conversation surface with the reports and work panel beside it. Use it when you want the conversation and its artifacts on one screen instead of a terminal."
          control={<RouteLink to="/qubit-chat">Open Qubit Chat</RouteLink>}
          testId="guide-row-qubit-chat"
        />
      </SettingsSection>

      <SettingsSection
        title="Make it do things for you"
        description="Anything you would otherwise re-explain belongs in one of these, not in a new chat message."
        testId="guide-automate"
      >
        <SettingsRow
          title="Tasks & Scheduling"
          description="Cron jobs, taskflows and the task board. Describe the job once, give it a schedule, and it runs without you. Each run lands an inbox entry so you can see what fired and what it produced."
          control={<RouteLink to="/qubit-scheduler">Open scheduling</RouteLink>}
          testId="guide-row-scheduler"
        />
        <SettingsRow
          title="Skills"
          description="Reusable procedures Qubit loads when a task matches. If you find yourself giving the same instructions twice, that is a skill. Installed skills are listed here; the agent can write new ones as it works."
          control={<RouteLink to="/skills">Browse skills</RouteLink>}
          testId="guide-row-skills"
        />
        <SettingsRow
          title="MCP servers"
          description="External tool servers — browsers, databases, editors — that Qubit can call. Add a server here and its tools become available in chat and voice without any code."
          control={<RouteLink to="/mcp">Manage MCP</RouteLink>}
          testId="guide-row-mcp"
        />
        <SettingsRow
          title="Channels"
          description="Connect the messaging platforms you already use so Qubit reaches you where you are, not only in the dashboard."
          control={<RouteLink to="/channels">Open channels</RouteLink>}
          testId="guide-row-channels"
        />
      </SettingsSection>

      <SettingsSection
        title="Teach it about you"
        description="Qubit is only as useful as what it remembers. These are the controls for what it keeps."
        testId="guide-memory"
      >
        <SettingsRow
          title="Memory"
          description="Memory providers plus the context store. This is where standing facts live — who you are, how you like answers, what you are building. The agent reads it every session, so put durable preferences here rather than repeating them."
          control={<RouteLink to="/qubit-memory">Open memory</RouteLink>}
          testId="guide-row-memory"
        />
        <SettingsRow
          title="Sessions"
          description="Past conversations with full transcripts. Resume a thread instead of starting over — the agent keeps the earlier context."
          control={<RouteLink to="/sessions">Open sessions</RouteLink>}
          testId="guide-row-sessions"
        />
        <SettingsRow
          title="Files"
          description="The workspace Qubit reads and writes, including artifacts it produced for you. Anything created during a run is reachable here."
          control={<RouteLink to="/files">Open files</RouteLink>}
          testId="guide-row-files"
        />
      </SettingsSection>

      <SettingsSection
        title="Understand what it did"
        description="When an answer looks wrong, one of these pages usually says why."
        testId="guide-observe"
      >
        <SettingsRow
          title="Logs"
          description="Agent, gateway and error logs. First place to look when a run failed silently."
          control={<RouteLink to="/logs">Open logs</RouteLink>}
          testId="guide-row-logs"
        />
        <SettingsRow
          title="Analytics"
          description="Token usage and cost over time, per day and per model. Use it to see which model your routine work actually lands on."
          control={<RouteLink to="/analytics">Open analytics</RouteLink>}
          testId="guide-row-analytics"
        />
        <SettingsRow
          title="Models"
          description="The model catalog and how each task routes. Change the default here, or pin a stronger model to a specific job."
          control={<RouteLink to="/models">Open models</RouteLink>}
          testId="guide-row-models"
        />
        <SettingsRow
          title="System"
          description="Machine and runtime status: what is running, what version, what is healthy. If the dashboard is behaving oddly, start here."
          control={<RouteLink to="/system">Open system</RouteLink>}
          testId="guide-row-system"
        />
      </SettingsSection>

      <SettingsSection
        title="Configure it"
        description="Set these once, then leave them alone."
        testId="guide-configure"
      >
        <SettingsRow
          title="Config"
          description="Agent configuration — model, reasoning effort, display, delegation."
          control={<RouteLink to="/config">Open config</RouteLink>}
          testId="guide-row-config"
        />
        <SettingsRow
          title="Keys"
          description="API keys and secrets, stored in the runtime environment. Add a provider key here and the matching models appear under Models."
          control={<RouteLink to="/env">Open keys</RouteLink>}
          testId="guide-row-keys"
        />
        <SettingsRow
          title="Profiles"
          description="Isolated setups — separate config, skills, memory and jobs. Useful when one Qubit should behave differently per project or person."
          control={<RouteLink to="/profiles">Open profiles</RouteLink>}
          testId="guide-row-profiles"
        />
      </SettingsSection>

      <SettingsSection
        title="Reference"
        description="Technical surfaces. Most people never need these; they are here because developers building on Qubit do."
        testId="guide-reference"
      >
        <SettingsRow
          title="API reference"
          description="Every HTTP endpoint Qubit serves, generated from the live route table. It is a browseable endpoint dump — accurate, but not a how-to. Start with this guide for usage."
          control={<RouteLink to="/docs">Open API reference</RouteLink>}
          testId="guide-row-docs"
        />
      </SettingsSection>
    </SettingsPage>
  );
}
