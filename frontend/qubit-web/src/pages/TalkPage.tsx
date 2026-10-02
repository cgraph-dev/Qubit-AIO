/**
 * Talk console (route /qubit-talk).
 *
 * Layout mirrors the upstream talk UI (`ui/src/ui/views/chat.ts`
 * realtime-talk block + `ui/src/ui/chat/realtime-talk.ts` session
 * lifecycle), rebranded to the Qubit voice: a live talk-status strip on
 * top, then the Talk options panel (Voice / Model primary row plus an
 * Advanced disclosure over the resolved speech config), then the client
 * writes, the read-only wake status, and the disabled-with-reason rows.
 *
 * Behavior is unchanged from the Qubit backend contract below:
 * the talk mode read (`talk.mode`), the speech-profile catalog + resolved
 * config (`talk.catalog` / `talk.config`), the client steer / transcript /
 * tool-call writes through the canonical session + broker (confirm-gated),
 * and local TTS speech (`talk.speak`, confirm-gated).
 *
 * The realtime/duplex lane is removed: every `talk.session.*` row plus the
 * client create/close pair render disabled with the exact backend prerequisite
 * (never called). Wake state is consumed READ-ONLY here (`voicewake.get`);
 * this page performs zero writes to wake state — the live-tuned wake chain
 * is untouchable from here.
 */
import { useCallback, useEffect, useState } from "react";

import { Button } from "@nous-research/ui/ui/components/button";
import { Spinner } from "@nous-research/ui/ui/components/spinner";

import {
  DISABLED_TALK_ACTIONS,
  appendTalkTranscript,
  callTalkTool,
  getTalkCatalog,
  getTalkConfig,
  getTalkMode,
  getVoicewake,
  speakTalk,
  steerTalkClient,
  type TalkCatalogResponse,
  type TalkConfigResponse,
  type TalkModeResponse,
  type VoicewakeStatus,
} from "@/lib/ocFamilyTalkApi";
import { refusalOrError } from "@/lib/policy-refusal";
import { cn } from "@/lib/utils";

/** Button press IS the confirmation (same pattern as the product pages). */
const CONFIRM_YES = () => true;

function errText(e: unknown, fallback: string): string {
  return refusalOrError(e, fallback);
}

function asText(value: unknown): string {
  return typeof value === "string" ? value : "";
}

export default function TalkPage() {
  const [mode, setMode] = useState<TalkModeResponse | null>(null);
  const [modeState, setModeState] = useState<"loading" | "ready" | "error">("loading");
  const [modeError, setModeError] = useState<string | null>(null);
  const [requestedMode, setRequestedMode] = useState("");

  const [catalog, setCatalog] = useState<TalkCatalogResponse | null>(null);
  const [config, setConfig] = useState<TalkConfigResponse | null>(null);
  const [wake, setWake] = useState<VoicewakeStatus | null>(null);
  const [wakeState, setWakeState] = useState<"loading" | "ready" | "error">("loading");
  const [wakeError, setWakeError] = useState<string | null>(null);

  const [sessionKey, setSessionKey] = useState("");
  const [steerText, setSteerText] = useState("");
  const [transcriptText, setTranscriptText] = useState("");
  const [transcriptRole, setTranscriptRole] = useState("user");
  const [toolName, setToolName] = useState("");
  const [toolArgs, setToolArgs] = useState("{}");
  const [speakText, setSpeakText] = useState("");
  const [speakUrl, setSpeakUrl] = useState<string | null>(null);

  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setModeState("loading");
    setModeError(null);
    try {
      const res = await getTalkMode(requestedMode.trim() || undefined);
      setMode(res);
      setModeState("ready");
    } catch (e) {
      setModeError(errText(e, "Talk mode failed to load"));
      setModeState("error");
    }
    try {
      const [cat, cfg] = await Promise.all([getTalkCatalog(), getTalkConfig()]);
      setCatalog(cat);
      setConfig(cfg);
    } catch {
      // Catalog/config are supplementary; the mode read above is authoritative.
    }
  }, [requestedMode]);

  const loadWake = useCallback(async () => {
    setWakeState("loading");
    setWakeError(null);
    try {
      const res = await getVoicewake();
      setWake(res);
      setWakeState("ready");
    } catch (e) {
      setWakeError(errText(e, "Wake status failed to load"));
      setWakeState("error");
    }
  }, []);

  useEffect(() => {
    void load();
    void loadWake();
  }, [load, loadWake]);

  const runWrite = async (verb: string, fn: () => Promise<unknown>) => {
    setBusy(verb);
    setNotice(null);
    setError(null);
    try {
      const res = await fn();
      const detail = (res as { detail?: unknown }).detail;
      setNotice(`${verb} settled ✓${asText(detail) ? ` — ${asText(detail)}` : ""}`);
    } catch (e) {
      setError(errText(e, `${verb} failed`));
    } finally {
      setBusy(null);
    }
  };

  const handleSteer = () =>
    runWrite("talk.client.steer", () =>
      steerTalkClient(
        { sessionKey: sessionKey.trim(), text: steerText.trim() },
        CONFIRM_YES,
      ),
    );

  const handleTranscript = () =>
    runWrite("talk.client.transcript", () =>
      appendTalkTranscript(
        {
          sessionKey: sessionKey.trim(),
          text: transcriptText.trim(),
          role: transcriptRole.trim() || "user",
        },
        CONFIRM_YES,
      ),
    );

  const handleToolCall = () => {
    let args: Record<string, unknown> = {};
    try {
      const parsed: unknown = JSON.parse(toolArgs.trim() || "{}");
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        args = parsed as Record<string, unknown>;
      }
    } catch {
      setError("Tool args must be a JSON object.");
      return;
    }
    return runWrite("talk.client.toolCall", () =>
      callTalkTool(
        {
          toolName: toolName.trim(),
          args,
          sessionKey: sessionKey.trim() || undefined,
        },
        CONFIRM_YES,
      ),
    );
  };

  const handleSpeak = async () => {
    setBusy("talk.speak");
    setNotice(null);
    setError(null);
    setSpeakUrl(null);
    try {
      const res = await speakTalk(speakText.trim(), CONFIRM_YES);
      if (res.data_url) setSpeakUrl(res.data_url);
      setNotice(`talk.speak settled ✓${res.provider ? ` via ${res.provider}` : ""}`);
    } catch (e) {
      setError(errText(e, "talk.speak failed"));
    } finally {
      setBusy(null);
    }
  };

  const profiles = Array.isArray(catalog?.profiles) ? catalog.profiles : [];

  const wakeEngine =
    typeof wake?.engine === "string" && wake.engine ? wake.engine : null;

  const statusText =
    modeState === "loading" || wakeState === "loading"
      ? "Connecting…"
      : modeState === "error"
        ? (modeError ?? "Talk mode failed to load")
        : wakeState === "error"
          ? (wakeError ?? "Wake status failed to load")
          : [
              asText(mode?.effectiveMode) ? `Mode ${asText(mode?.effectiveMode)}` : null,
              wakeEngine ? `wake ${wakeEngine}` : null,
            ]
              .filter(Boolean)
              .join(" · ") || "Talk console ready";

  const statusIsError = modeState === "error" || wakeState === "error";

  const speechOut =
    config?.speech_out && typeof config.speech_out === "object"
      ? (config.speech_out as Record<string, unknown>)
      : null;
  const speechIn =
    config?.speech_in && typeof config.speech_in === "object"
      ? (config.speech_in as Record<string, unknown>)
      : null;
  const audioStatus =
    config?.audio && typeof config.audio === "object"
      ? (config.audio as Record<string, unknown>)
      : null;

  return (
    <div className={cn("flex min-h-0 w-full min-w-0 flex-1 flex-col gap-3 pt-1 sm:pt-2")}>
      <div className="flex items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-medium tracking-[0.08em]">Talk console</h2>
          <p className="text-xs text-text-tertiary">
            Voice and speech settings, profile catalog, and client writes through the canonical session.
          </p>
        </div>
        <Button
          ghost
          size="sm"
          onClick={() => {
            void load();
            void loadWake();
          }}
          disabled={modeState === "loading"}
          aria-label="Refresh talk console"
        >
          {modeState === "loading" ? <Spinner /> : "refresh"}
        </Button>
      </div>

      {notice ? (
        <p role="status" className="text-xs text-text-secondary">
          {notice}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      ) : null}

      {/* ── talk status strip (live, read-only; mirrors upstream talk-status) ── */}
      <section
        aria-label="Talk status"
        className="border border-border bg-background-base/60 px-3 py-2"
      >
        <p
          role={statusIsError ? "alert" : "status"}
          className={cn(
            "text-xs",
            statusIsError ? "text-destructive" : "text-text-secondary",
          )}
        >
          {modeState === "loading" || wakeState === "loading" ? (
            <span className="flex items-center gap-2">
              <Spinner className="shrink-0" /> {statusText}
            </span>
          ) : (
            statusText
          )}
        </p>
      </section>

      {/* ── talk.mode (read-only) ── */}
      <section
        aria-label="Talk mode"
        className="border border-border bg-background-base/60 px-3 py-2"
      >
        <h3 className="text-xs font-medium text-text-secondary">Mode</h3>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <input
            type="text"
            value={requestedMode}
            onChange={(e) => setRequestedMode(e.target.value)}
            placeholder="requested mode (optional)"
            aria-label="Requested talk mode"
            className="min-w-0 flex-1 border border-border bg-background-base px-2 py-1 font-mono text-xs"
          />
          <Button ghost size="sm" onClick={() => void load()} aria-label="Read talk mode">
            read
          </Button>
        </div>
        {modeState === "loading" ? (
          <p className="mt-1 flex items-center gap-2 text-xs text-text-tertiary">
            <Spinner className="shrink-0" /> Reading mode…
          </p>
        ) : modeState === "error" ? (
          <p role="alert" className="mt-1 text-xs text-destructive">
            {modeError}
          </p>
        ) : mode ? (
          <dl className="mt-1 grid grid-cols-1 gap-1 text-xs text-text-secondary sm:grid-cols-3">
            <div>
              <dt className="text-text-tertiary">effective</dt>
              <dd className="font-mono">{asText(mode.effectiveMode) || "—"}</dd>
            </div>
            <div>
              <dt className="text-text-tertiary">requested</dt>
              <dd className="font-mono">{asText(mode.requestedMode) || "—"}</dd>
            </div>
            <div>
              <dt className="text-text-tertiary">code</dt>
              <dd className="font-mono">{asText(mode.code) || "—"}</dd>
            </div>
          </dl>
        ) : null}
        {asText(mode?.detail) ? (
          <p className="mt-1 text-[0.6875rem] text-text-tertiary">{asText(mode?.detail)}</p>
        ) : null}
      </section>

      {/* ── Talk options (read-only; mirrors upstream talk-options panel) ── */}
      <section
        aria-label="Talk options"
        className="border border-border bg-background-base/60 px-3 py-2"
      >
        <h3 className="text-xs font-medium text-text-secondary">Talk options</h3>
        <div className="mt-1 grid grid-cols-1 gap-1 text-xs text-text-secondary sm:grid-cols-3">
          <div>
            <dt className="text-text-tertiary">Voice</dt>
            <dd className="font-mono">
              {profiles.length === 0
                ? "—"
                : profiles
                    .map((p, i) => asText(p.id) || `profile ${i}`)
                    .join(", ")}
            </dd>
          </div>
          <div>
            <dt className="text-text-tertiary">Model</dt>
            <dd className="font-mono">{asText(mode?.effectiveMode) || "—"}</dd>
          </div>
          <div>
            <dt className="text-text-tertiary">Wake engine (read-only)</dt>
            <dd className="font-mono">{wakeEngine ?? "—"}</dd>
          </div>
        </div>
        <details className="mt-1">
          <summary className="cursor-pointer text-xs text-text-secondary">Advanced</summary>
          <div className="mt-1 grid grid-cols-1 gap-1 text-xs text-text-secondary sm:grid-cols-3">
            <div>
              <dt className="text-text-tertiary">Speech-in</dt>
              <dd className="font-mono">
                {speechIn
                  ? Object.keys(speechIn).slice(0, 4).join(", ") || "resolved"
                  : "—"}
              </dd>
            </div>
            <div>
              <dt className="text-text-tertiary">Speech-out</dt>
              <dd className="font-mono">
                {speechOut
                  ? Object.keys(speechOut).slice(0, 4).join(", ") || "resolved"
                  : "—"}
              </dd>
            </div>
            <div>
              <dt className="text-text-tertiary">Audio</dt>
              <dd className="font-mono">
                {audioStatus
                  ? Object.keys(audioStatus).slice(0, 4).join(", ") || "resolved"
                  : "—"}
              </dd>
            </div>
          </div>
        </details>
      </section>

      {/* ── talk.catalog + talk.config (read-only) ── */}
      <section
        aria-label="Speech catalog and config"
        className="border border-border bg-background-base/60 px-3 py-2"
      >
        <h3 className="text-xs font-medium text-text-secondary">Speech catalog</h3>
        {profiles.length === 0 ? (
          <p className="mt-1 text-xs text-text-tertiary">No speech profiles reported.</p>
        ) : (
          <ul className="mt-1 flex flex-col gap-1" aria-label="Speech profiles">
            {profiles.map((p, i) => (
              <li
                key={asText(p.id) || `profile-${i}`}
                className="flex flex-wrap items-center gap-x-3 gap-y-1 border border-border px-2 py-1 text-xs"
              >
                <span className="font-mono text-text-secondary">{asText(p.id) || `profile ${i}`}</span>
                {asText(p.kind) ? (
                  <span className="text-text-tertiary">{asText(p.kind)}</span>
                ) : null}
              </li>
            ))}
          </ul>
        )}
        {config ? (
          <p className="mt-1 text-[0.6875rem] text-text-tertiary">
            Config resolved: speech-in + speech-out + audio status served read-only.
          </p>
        ) : null}
      </section>

      {/* ── client writes (confirm-gated) ── */}
      <section
        aria-label="Talk client writes"
        className="border border-border bg-background-base/60 px-3 py-2"
      >
        <h3 className="text-xs font-medium text-text-secondary">Client writes</h3>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <input
            type="text"
            value={sessionKey}
            onChange={(e) => setSessionKey(e.target.value)}
            placeholder="session key"
            aria-label="Session key"
            className="min-w-0 flex-1 border border-border bg-background-base px-2 py-1 font-mono text-xs"
          />
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input
            type="text"
            value={steerText}
            onChange={(e) => setSteerText(e.target.value)}
            placeholder="steer text (recorded for the next turn)"
            aria-label="Steer text"
            className="min-w-0 flex-1 border border-border bg-background-base px-2 py-1 text-xs"
          />
          <Button
            ghost
            size="sm"
            onClick={() => void handleSteer()}
            disabled={busy !== null || !sessionKey.trim() || !steerText.trim()}
            aria-label="Send talk steer"
          >
            {busy === "talk.client.steer" ? <Spinner /> : "steer"}
          </Button>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input
            type="text"
            value={transcriptRole}
            onChange={(e) => setTranscriptRole(e.target.value)}
            placeholder="role"
            aria-label="Transcript role"
            className="w-24 border border-border bg-background-base px-2 py-1 font-mono text-xs"
          />
          <input
            type="text"
            value={transcriptText}
            onChange={(e) => setTranscriptText(e.target.value)}
            placeholder="finalized transcript text"
            aria-label="Transcript text"
            className="min-w-0 flex-1 border border-border bg-background-base px-2 py-1 text-xs"
          />
          <Button
            ghost
            size="sm"
            onClick={() => void handleTranscript()}
            disabled={busy !== null || !sessionKey.trim() || !transcriptText.trim()}
            aria-label="Append transcript"
          >
            {busy === "talk.client.transcript" ? <Spinner /> : "append"}
          </Button>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input
            type="text"
            value={toolName}
            onChange={(e) => setToolName(e.target.value)}
            placeholder="tool name"
            aria-label="Tool name"
            className="min-w-0 flex-1 border border-border bg-background-base px-2 py-1 font-mono text-xs"
          />
          <input
            type="text"
            value={toolArgs}
            onChange={(e) => setToolArgs(e.target.value)}
            placeholder='args JSON, e.g. {"path": "notes.txt"}'
            aria-label="Tool args JSON"
            className="min-w-0 flex-1 border border-border bg-background-base px-2 py-1 font-mono text-xs"
          />
          <Button
            ghost
            size="sm"
            onClick={() => void handleToolCall()}
            disabled={busy !== null || !toolName.trim()}
            aria-label="Run talk tool call"
          >
            {busy === "talk.client.toolCall" ? <Spinner /> : "run tool"}
          </Button>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input
            type="text"
            value={speakText}
            onChange={(e) => setSpeakText(e.target.value)}
            placeholder="text to speak via local TTS"
            aria-label="Speak text"
            className="min-w-0 flex-1 border border-border bg-background-base px-2 py-1 text-xs"
          />
          <Button
            ghost
            size="sm"
            onClick={() => void handleSpeak()}
            disabled={busy !== null || !speakText.trim()}
            aria-label="Speak text"
          >
            {busy === "talk.speak" ? <Spinner /> : "speak"}
          </Button>
        </div>
        {speakUrl ? (
          <audio controls src={speakUrl} className="mt-2 w-full" aria-label="Spoken audio" />
        ) : null}
      </section>

      {/* ── voicewake.get (live read-only) ── */}
      <section
        aria-label="Wake status"
        className="border border-border bg-background-base/60 px-3 py-2"
      >
        <h3 className="text-xs font-medium text-text-secondary">Wake status (read-only)</h3>
        {wakeState === "loading" ? (
          <p className="mt-1 flex items-center gap-2 text-xs text-text-tertiary">
            <Spinner className="shrink-0" /> Reading wake status…
          </p>
        ) : wakeState === "error" ? (
          <p role="alert" className="mt-1 text-xs text-destructive">
            {wakeError}
          </p>
        ) : wake ? (
          <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap text-xs text-text-secondary">
            {JSON.stringify(wake, null, 2)}
          </pre>
        ) : null}
        <p className="mt-1 text-[0.6875rem] text-text-tertiary">
          This page only reads wake state. It never writes wake triggers, routing, or
          voice settings — the live-tuned wake chain is managed elsewhere.
        </p>
      </section>

      {/* ── disabled-with-reason (never called) ── */}
      <section
        aria-label="Unavailable talk operations"
        className="border border-border bg-background-base/40 px-3 py-2"
      >
        <h3 className="text-xs font-medium text-text-secondary">Unavailable (operator-owned)</h3>
        <ul className="mt-1 flex flex-col gap-1" aria-label="Disabled talk actions">
          {DISABLED_TALK_ACTIONS.map((a) => (
            <li
              key={a.action}
              className="flex flex-wrap items-center gap-x-3 gap-y-1 border border-border px-2 py-1 text-xs"
            >
              <span className="font-mono text-text-secondary">{a.action}</span>
              <span className="font-mono text-text-tertiary">{a.code}</span>
              <button
                type="button"
                disabled
                data-state="refused"
                title={`${a.code}: ${a.reason}`}
                aria-label={`${a.action} unavailable`}
                aria-disabled="true"
                className="ml-auto cursor-not-allowed border border-border px-2 py-1 text-xs text-text-tertiary opacity-60"
              >
                unavailable
              </button>
              <span className="w-full text-text-tertiary">
                Needs {a.reason}. Counterpart: {a.counterpart}.
              </span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
