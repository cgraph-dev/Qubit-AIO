/**
 * Talk console (route /qubit-talk).
 *
 * Structure mirrors the ported settings surfaces in this app
 * (`.settings-page` > `.settings-section` > `.settings-group` > `.settings-row`):
 * a page intro + live talk-status line, then the talk mode read, the Talk
 * options panel (Voice / Model primary row plus an Advanced disclosure over
 * the resolved speech config), the client writes, the read-only wake status,
 * and the realtime operations Qubit genuinely cannot run (honest status rows
 * with the exact prerequisite — never bare dead buttons).
 *
 * Behaviour is unchanged from the Qubit backend contract below:
 * the talk mode read (`talk.mode`), the speech-profile catalog + resolved
 * config (`talk.catalog` / `talk.config`), the client steer / transcript /
 * tool-call writes through the canonical session + broker (confirm-gated),
 * and local TTS speech (`talk.speak`, confirm-gated).
 *
 * The realtime/duplex lane is removed: every `talk.session.*` row plus the
 * client create/close pair are listed under "Realtime talk (not available)"
 * with the exact backend prerequisite (never called). Wake state is consumed
 * READ-ONLY here (`voicewake.get`); this page performs zero writes to wake
 * state — the live-tuned wake chain is untouchable from here.
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

/** Button press IS the confirmation (same pattern as the product pages). */
const CONFIRM_YES = () => true;

function errText(e: unknown, fallback: string): string {
  return refusalOrError(e, fallback);
}

function asText(value: unknown): string {
  return typeof value === "string" ? value : "";
}

const INPUT_CLASS =
  "settings-input min-w-0 border border-border bg-background-base px-2 py-1 text-xs";
const MONO_INPUT_CLASS =
  "settings-input min-w-0 border border-border bg-background-base px-2 py-1 font-mono text-xs";

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
    <div className="settings-page settings-page--wide">
      <p className="settings-page__intro">
        Voice and speech settings, the profile catalog, and client writes through the
        canonical session. Wake state is read-only here.
      </p>

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

      {/* ── talk status (live, read-only) ── */}
      <section aria-label="Talk status" className="settings-section">
        <div className="settings-section__header">
          <div className="settings-section__copy">
            <h2 className="settings-section__heading">Talk status</h2>
            <p
              role={statusIsError ? "alert" : "status"}
              className={statusIsError ? "settings-section__desc text-destructive" : "settings-section__desc"}
            >
              {modeState === "loading" || wakeState === "loading" ? (
                <span className="flex items-center gap-2">
                  <Spinner className="shrink-0" /> {statusText}
                </span>
              ) : (
                statusText
              )}
            </p>
          </div>
          <div className="settings-section__actions">
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
        </div>
      </section>

      {/* ── talk.mode (read-only) ── */}
      <section aria-label="Talk mode" className="settings-section">
        <div className="settings-section__copy">
          <h2 className="settings-section__heading">Mode</h2>
          <p className="settings-section__desc">
            Effective talk mode, resolved from the local speech profile.
          </p>
        </div>
        <div className="settings-group">
          <div className="settings-row">
            <div className="settings-row__text">
              <div className="settings-row__title">Requested mode</div>
              <p className="settings-row__desc">Optional override, applied on read.</p>
            </div>
            <div className="settings-row__control">
              <input
                type="text"
                value={requestedMode}
                onChange={(e) => setRequestedMode(e.target.value)}
                placeholder="requested mode (optional)"
                aria-label="Requested talk mode"
                className={MONO_INPUT_CLASS}
              />
              <Button ghost size="sm" onClick={() => void load()} aria-label="Read talk mode">
                read
              </Button>
            </div>
          </div>
          {modeState === "loading" ? (
            <div className="settings-row">
              <div className="settings-row__text">
                <div className="settings-row__title flex items-center gap-2">
                  <Spinner className="shrink-0" /> Reading mode…
                </div>
              </div>
            </div>
          ) : modeState === "error" ? (
            <div className="settings-row">
              <div className="settings-row__text">
                <p role="alert" className="settings-row__desc text-destructive">
                  {modeError}
                </p>
              </div>
            </div>
          ) : mode ? (
            <dl className="settings-summary settings-group">
              <div className="settings-summary__tile">
                <dt>effective</dt>
                <dd className="settings-row__value settings-row__value--mono">
                  {asText(mode.effectiveMode) || "—"}
                </dd>
              </div>
              <div className="settings-summary__tile">
                <dt>requested</dt>
                <dd className="settings-row__value settings-row__value--mono">
                  {asText(mode.requestedMode) || "—"}
                </dd>
              </div>
              <div className="settings-summary__tile">
                <dt>code</dt>
                <dd className="settings-row__value settings-row__value--mono">
                  {asText(mode.code) || "—"}
                </dd>
              </div>
            </dl>
          ) : null}
          {asText(mode?.detail) ? (
            <p className="settings-section__desc">{asText(mode?.detail)}</p>
          ) : null}
        </div>
      </section>

      {/* ── Talk options (read-only) ── */}
      <section aria-label="Talk options" className="settings-section">
        <div className="settings-section__copy">
          <h2 className="settings-section__heading">Talk options</h2>
          <p className="settings-section__desc">
            The resolved voice, model, and wake engine that this console uses.
          </p>
        </div>
        <div className="settings-group">
          <div className="settings-row">
            <div className="settings-row__text">
              <div className="settings-row__title">Voice</div>
            </div>
            <div className="settings-row__control">
              <span className="settings-row__value settings-row__value--mono">
                {profiles.length === 0
                  ? "—"
                  : profiles.map((p, i) => asText(p.id) || `profile ${i}`).join(", ")}
              </span>
            </div>
          </div>
          <div className="settings-row">
            <div className="settings-row__text">
              <div className="settings-row__title">Model</div>
            </div>
            <div className="settings-row__control">
              <span className="settings-row__value settings-row__value--mono">
                {asText(mode?.effectiveMode) || "—"}
              </span>
            </div>
          </div>
          <div className="settings-row">
            <div className="settings-row__text">
              <div className="settings-row__title">Wake engine</div>
              <p className="settings-row__desc">Read-only here.</p>
            </div>
            <div className="settings-row__control">
              <span className="settings-row__value settings-row__value--mono">
                {wakeEngine ?? "—"}
              </span>
            </div>
          </div>
          <div className="settings-row settings-row--stacked">
            <div className="settings-row__text">
              <div className="settings-row__title">Advanced</div>
              <p className="settings-row__desc">Resolved speech config keys.</p>
            </div>
            <div className="settings-row__control">
              <details className="w-full">
                <summary className="cursor-pointer text-xs text-text-secondary">
                  Advanced
                </summary>
                <dl className="mt-1 grid grid-cols-1 gap-1 text-left text-xs text-text-secondary sm:grid-cols-3">
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
                </dl>
              </details>
            </div>
          </div>
        </div>
      </section>

      {/* ── talk.catalog + talk.config (read-only) ── */}
      <section aria-label="Speech catalog and config" className="settings-section">
        <div className="settings-section__copy">
          <h2 className="settings-section__heading">
            Speech catalog{" "}
            {profiles.length > 0 ? <span className="settings-count">{profiles.length}</span> : null}
          </h2>
          <p className="settings-section__desc">
            Profiles served by the canonical speech config path.
          </p>
        </div>
        <div className="settings-group">
          {profiles.length === 0 ? (
            <div className="settings-empty">No speech profiles reported.</div>
          ) : (
            profiles.map((p, i) => (
              <div className="settings-row" key={asText(p.id) || `profile-${i}`}>
                <div className="settings-row__text">
                  <div className="settings-row__title font-mono">
                    {asText(p.id) || `profile ${i}`}
                  </div>
                  {asText(p.kind) ? (
                    <p className="settings-row__desc">{asText(p.kind)}</p>
                  ) : null}
                </div>
              </div>
            ))
          )}
          {config ? (
            <div className="settings-row">
              <div className="settings-row__text">
                <p className="settings-row__desc">
                  Config resolved: speech-in + speech-out + audio status served read-only.
                </p>
              </div>
            </div>
          ) : null}
        </div>
      </section>

      {/* ── client writes (confirm-gated) ── */}
      <section aria-label="Talk client writes" className="settings-section">
        <div className="settings-section__copy">
          <h2 className="settings-section__heading">Client writes</h2>
          <p className="settings-section__desc">
            Writes land on the canonical session + broker. Each press confirms.
          </p>
        </div>
        <div className="settings-group">
          <div className="settings-row">
            <div className="settings-row__text">
              <div className="settings-row__title">Session key</div>
              <p className="settings-row__desc">Target session for the writes below.</p>
            </div>
            <div className="settings-row__control">
              <input
                type="text"
                value={sessionKey}
                onChange={(e) => setSessionKey(e.target.value)}
                placeholder="session key"
                aria-label="Session key"
                className={MONO_INPUT_CLASS}
              />
            </div>
          </div>
          <div className="settings-row">
            <div className="settings-row__text">
              <div className="settings-row__title">Steer</div>
              <p className="settings-row__desc">Recorded for the next turn.</p>
            </div>
            <div className="settings-row__control">
              <input
                type="text"
                value={steerText}
                onChange={(e) => setSteerText(e.target.value)}
                placeholder="steer text (recorded for the next turn)"
                aria-label="Steer text"
                className={INPUT_CLASS}
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
          </div>
          <div className="settings-row">
            <div className="settings-row__text">
              <div className="settings-row__title">Transcript</div>
              <p className="settings-row__desc">Append a finalized transcript row.</p>
            </div>
            <div className="settings-row__control">
              <input
                type="text"
                value={transcriptRole}
                onChange={(e) => setTranscriptRole(e.target.value)}
                placeholder="role"
                aria-label="Transcript role"
                className="settings-input w-24 border border-border bg-background-base px-2 py-1 font-mono text-xs"
              />
              <input
                type="text"
                value={transcriptText}
                onChange={(e) => setTranscriptText(e.target.value)}
                placeholder="finalized transcript text"
                aria-label="Transcript text"
                className={INPUT_CLASS}
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
          </div>
          <div className="settings-row">
            <div className="settings-row__text">
              <div className="settings-row__title">Tool call</div>
              <p className="settings-row__desc">Invoke a bound tool on the session.</p>
            </div>
            <div className="settings-row__control">
              <input
                type="text"
                value={toolName}
                onChange={(e) => setToolName(e.target.value)}
                placeholder="tool name"
                aria-label="Tool name"
                className={MONO_INPUT_CLASS}
              />
              <input
                type="text"
                value={toolArgs}
                onChange={(e) => setToolArgs(e.target.value)}
                placeholder='args JSON, e.g. {"path": "notes.txt"}'
                aria-label="Tool args JSON"
                className={MONO_INPUT_CLASS}
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
          </div>
          <div className="settings-row">
            <div className="settings-row__text">
              <div className="settings-row__title">Speak</div>
              <p className="settings-row__desc">Render spoken audio via the local TTS chain.</p>
            </div>
            <div className="settings-row__control">
              <input
                type="text"
                value={speakText}
                onChange={(e) => setSpeakText(e.target.value)}
                placeholder="text to speak via local TTS"
                aria-label="Speak text"
                className={INPUT_CLASS}
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
          </div>
          {speakUrl ? (
            <div className="settings-row settings-row--stacked">
              <div className="settings-row__text">
                <div className="settings-row__title">Spoken audio</div>
              </div>
              <div className="settings-row__control">
                <audio controls src={speakUrl} className="w-full" aria-label="Spoken audio" />
              </div>
            </div>
          ) : null}
        </div>
      </section>

      {/* ── voicewake.get (live read-only) ── */}
      <section aria-label="Wake status" className="settings-section">
        <div className="settings-section__copy">
          <h2 className="settings-section__heading">Wake status</h2>
          <p className="settings-section__desc">
            This page only reads wake state. It never writes wake triggers, routing, or
            voice settings — the live-tuned wake chain is managed elsewhere.
          </p>
        </div>
        <div className="settings-group">
          {wakeState === "loading" ? (
            <div className="settings-row">
              <div className="settings-row__text">
                <div className="settings-row__title flex items-center gap-2">
                  <Spinner className="shrink-0" /> Reading wake status…
                </div>
              </div>
            </div>
          ) : wakeState === "error" ? (
            <div className="settings-row">
              <div className="settings-row__text">
                <p role="alert" className="settings-row__desc text-destructive">
                  {wakeError}
                </p>
              </div>
            </div>
          ) : wake ? (
            <div className="settings-row settings-row--stacked">
              <div className="settings-row__text">
                <div className="settings-row__title">Reported state</div>
              </div>
              <div className="settings-row__control">
                <pre className="max-h-40 w-full overflow-auto whitespace-pre-wrap text-left text-xs text-text-secondary">
                  {JSON.stringify(wake, null, 2)}
                </pre>
              </div>
            </div>
          ) : null}
        </div>
      </section>

      {/* ── genuinely unavailable (no route behind them) ── */}
      <section aria-label="Unavailable talk operations" className="settings-section">
        <div className="settings-section__copy">
          <h2 className="settings-section__heading">
            Realtime talk{" "}
            <span className="settings-count">{DISABLED_TALK_ACTIONS.length}</span>
          </h2>
          <p className="settings-section__desc">
            The duplex provider lane is not part of Qubit. These rows name the missing
            transport; the local /qubit-chat voice path is the counterpart. There is no
            route behind them, so they are reported rather than offered as controls.
          </p>
        </div>
        <div className="settings-group">
          {DISABLED_TALK_ACTIONS.map((a) => (
            <div
              key={a.action}
              className="settings-row settings-row--stacked"
              data-state="refused"
              aria-label={`${a.action} unavailable`}
              title={`${a.code}: ${a.reason}`}
            >
              <div className="settings-row__text">
                <div className="settings-row__title font-mono">{a.action}</div>
                <p className="settings-row__desc">
                  Needs {a.reason}. Counterpart: {a.counterpart}.
                </p>
              </div>
              <div className="settings-row__control">
                <span className="settings-status settings-status--warn" role="note">
                  <span className="settings-status__dot" aria-hidden="true" />
                  {a.code}
                </span>
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
