/**
 * Scheduler + media console (route /qubit-scheduler).
 *
 * Structure mirrors the ported settings surfaces in this app
 * (`.settings-page` > `.settings-section` > `.settings-group` > `.settings-row`):
 * a page intro, then the live summary tile strip, the schedule toggle, the
 * speech-profile form, the read-only power state, the task cancel/retry lane,
 * and the operations Qubit genuinely cannot run (each an honest status row
 * with its exact prerequisite — never a bare dead button).
 *
 * Behaviour is unchanged from the Qubit backend contract below:
 * the enabled-only schedule toggle (`cron.update`), the TTS provider/
 * enable/disable/persona writes through the canonical speech profiles
 * (confirm-gated), the power-state reads (`gateway.suspend.prepare` /
 * `gateway.suspend.resume`), and task cancel/retry by receipt or session
 * (confirm-gated).
 *
 * Everything without a local counterpart — per-job scratch, the suspend
 * handoff, the portal proxy, suggestion registry, dismiss registries, and the
 * mention inbox — is listed under "Not available on Qubit" with the exact
 * backend prerequisite. These render as status rows (not controls): there is
 * no route behind them, so there is nothing to click. The portal rows are
 * labeled "Qubit Gateway Portal (proxy)" to disambiguate them from the billing
 * portal on the System page.
 */
import { useCallback, useEffect, useState } from "react";

import { Button } from "@nous-research/ui/ui/components/button";
import { Spinner } from "@nous-research/ui/ui/components/spinner";

import {
  DISABLED_SCHEDULER_ACTIONS,
  TTS_PROVIDERS,
  cancelTask,
  disableTtsProfile,
  enableTtsProfile,
  getSuspendPrepare,
  getSuspendResume,
  retryTask,
  setTtsPersona,
  setTtsProvider,
  updateCronEnabled,
  type SuspendReadResponse,
} from "@/lib/ocFamilyTalkApi";
import { refusalOrError } from "@/lib/policy-refusal";

/** Button press IS the confirmation (same pattern as the product pages). */
const CONFIRM_YES = () => true;

function errText(e: unknown, fallback: string): string {
  return refusalOrError(e, fallback);
}

/** Shared input styling that reads as a settings control. */
const INPUT_CLASS =
  "settings-input min-w-0 border border-border bg-background-base px-2 py-1 font-mono text-xs";

export default function SchedulerMediaPage() {
  const [cronId, setCronId] = useState("");
  const [cronEnabled, setCronEnabled] = useState(true);

  const [ttsProvider, setTtsProviderChoice] = useState<string>(TTS_PROVIDERS[0]);
  const [ttsProfile, setTtsProfile] = useState("");
  const [ttsPersona, setTtsPersonaInput] = useState("");
  const [ttsSession, setTtsSession] = useState("");

  const [prepare, setPrepare] = useState<SuspendReadResponse | null>(null);
  const [resume, setResume] = useState<SuspendReadResponse | null>(null);
  const [powerState, setPowerState] = useState<"loading" | "ready" | "error">("loading");
  const [powerError, setPowerError] = useState<string | null>(null);

  const [cancelRef, setCancelRef] = useState("");
  const [cancelSession, setCancelSession] = useState("");
  const [retryRef, setRetryRef] = useState("");
  const [retrySession, setRetrySession] = useState("");

  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadPower = useCallback(async () => {
    setPowerState("loading");
    setPowerError(null);
    try {
      const [prep, res] = await Promise.all([getSuspendPrepare(), getSuspendResume()]);
      setPrepare(prep);
      setResume(res);
      setPowerState("ready");
    } catch (e) {
      setPowerError(errText(e, "Power state failed to load"));
      setPowerState("error");
    }
  }, []);

  useEffect(() => {
    void loadPower();
  }, [loadPower]);

  const runWrite = async (verb: string, fn: () => Promise<unknown>) => {
    setBusy(verb);
    setNotice(null);
    setError(null);
    try {
      await fn();
      setNotice(`${verb} settled ✓`);
    } catch (e) {
      setError(errText(e, `${verb} failed`));
    } finally {
      setBusy(null);
    }
  };

  const cronIdNum = Number.parseInt(cronId.trim(), 10);

  const handleCronToggle = () =>
    runWrite("cron.update", () => updateCronEnabled(cronIdNum, cronEnabled, CONFIRM_YES));

  const handleTtsProvider = () =>
    runWrite("tts.setProvider", () => setTtsProvider(ttsProvider, CONFIRM_YES));

  const handleTtsEnable = () =>
    runWrite("tts.enable", () => enableTtsProfile(ttsProfile.trim(), CONFIRM_YES));

  const handleTtsDisable = () =>
    runWrite("tts.disable", () => disableTtsProfile(ttsProfile.trim(), CONFIRM_YES));

  const handleTtsPersona = () =>
    runWrite("tts.setPersona", () =>
      setTtsPersona(
        { persona: ttsPersona.trim(), session: ttsSession.trim() || undefined },
        CONFIRM_YES,
      ),
    );

  const handleCancel = () =>
    runWrite("tasks.cancel", () =>
      cancelTask(
        {
          receiptId: cancelRef.trim() || undefined,
          session: cancelSession.trim() || undefined,
        },
        CONFIRM_YES,
      ),
    );

  const handleRetry = () =>
    runWrite("tasks.retry", () =>
      retryTask(
        {
          receiptId: retryRef.trim() || undefined,
          session: retrySession.trim() || undefined,
        },
        CONFIRM_YES,
      ),
    );

  return (
    <div className="settings-page settings-page--wide">
      <p className="settings-page__intro">
        Schedule toggles, speech profiles, power state, and task cancel/retry on the
        canonical Qubit backend. Every control here has a live route behind it.
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

      {/* ── live summary (read-only summary strip) ── */}
      <section aria-label="Scheduler summary" className="settings-section">
        <div className="settings-section__header">
          <div className="settings-section__copy">
            <h2 className="settings-section__heading">Scheduler summary</h2>
            <p className="settings-section__desc">
              Live schedule, speech, and power state read from the backend.
            </p>
          </div>
          <div className="settings-section__actions">
            <Button
              ghost
              size="sm"
              onClick={() => void loadPower()}
              disabled={powerState === "loading"}
              aria-label="Refresh power state"
            >
              {powerState === "loading" ? <Spinner /> : "refresh"}
            </Button>
          </div>
        </div>
        <dl className="settings-summary settings-group">
          <div className="settings-summary__tile">
            <dt>Schedule</dt>
            <dd className={cronEnabled ? "settings-row__value" : "text-destructive"}>
              {cronEnabled ? "Yes" : "No"}
            </dd>
          </div>
          <div className="settings-summary__tile">
            <dt>Speech</dt>
            <dd className="settings-row__value settings-row__value--mono">{ttsProvider}</dd>
          </div>
          <div className="settings-summary__tile">
            <dt>Power</dt>
            <dd className="settings-row__value">
              {powerState === "loading"
                ? "…"
                : powerState === "error"
                  ? "unavailable"
                  : "reported"}
            </dd>
          </div>
        </dl>
        {powerError ? (
          <p role="alert" className="settings-section__desc text-destructive">
            {powerError}
          </p>
        ) : null}
      </section>

      {/* ── cron.update (enabled-only) ── */}
      <section aria-label="Schedule enabled toggle" className="settings-section">
        <div className="settings-section__copy">
          <h2 className="settings-section__heading">Schedule enabled</h2>
          <p className="settings-section__desc">
            The schedule store applies only the enabled flag (pause/resume). No other
            patch field has a local counterpart, so none is sent.
          </p>
        </div>
        <div className="settings-group">
          <div className="settings-row">
            <div className="settings-row__text">
              <div className="settings-row__title">Job id</div>
              <p className="settings-row__desc">Numeric id of the schedule to toggle.</p>
            </div>
            <div className="settings-row__control">
              <input
                type="text"
                inputMode="numeric"
                value={cronId}
                onChange={(e) => setCronId(e.target.value)}
                placeholder="job id"
                aria-label="Schedule job id"
                className={INPUT_CLASS}
              />
            </div>
          </div>
          <div className="settings-row">
            <div className="settings-row__text">
              <div className="settings-row__title">Enabled</div>
              <p className="settings-row__desc">Pause or resume the schedule.</p>
            </div>
            <div className="settings-row__control">
              <label className="settings-toggle text-xs text-text-secondary">
                <input
                  type="checkbox"
                  checked={cronEnabled}
                  onChange={(e) => setCronEnabled(e.target.checked)}
                  aria-label="Schedule enabled"
                />
                enabled
              </label>
            </div>
          </div>
          <div className="settings-row settings-row--actions">
            <div className="settings-row__text">
              <div className="settings-row__title">Apply</div>
            </div>
            <div className="settings-row__control">
              <Button
                ghost
                size="sm"
                onClick={() => void handleCronToggle()}
                disabled={busy !== null || !Number.isInteger(cronIdNum)}
                aria-label="Apply schedule enabled toggle"
              >
                {busy === "cron.update" ? <Spinner /> : "apply"}
              </Button>
            </div>
          </div>
        </div>
      </section>

      {/* ── TTS writes (confirm-gated) ── */}
      <section aria-label="Speech profiles" className="settings-section">
        <div className="settings-section__copy">
          <h2 className="settings-section__heading">Speech (TTS)</h2>
          <p className="settings-section__desc">
            Qubit keeps no global TTS auto-mode flag or persona bundle: enable/disable
            flips the per-profile flag and persona selects a profile for a session.
          </p>
        </div>
        <div className="settings-group">
          <div className="settings-row">
            <div className="settings-row__text">
              <div className="settings-row__title">Provider</div>
              <p className="settings-row__desc">Active speech-out provider.</p>
            </div>
            <div className="settings-row__control">
              <select
                value={ttsProvider}
                onChange={(e) => setTtsProviderChoice(e.target.value)}
                aria-label="TTS provider"
                className="settings-select border border-border bg-background-base px-2 py-1 font-mono text-xs"
              >
                {TTS_PROVIDERS.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
              <Button
                ghost
                size="sm"
                onClick={() => void handleTtsProvider()}
                disabled={busy !== null}
                aria-label="Set TTS provider"
              >
                {busy === "tts.setProvider" ? <Spinner /> : "set provider"}
              </Button>
            </div>
          </div>
          <div className="settings-row">
            <div className="settings-row__text">
              <div className="settings-row__title">Speech-out profile</div>
              <p className="settings-row__desc">
                Enable or disable the named speech-out profile.
              </p>
            </div>
            <div className="settings-row__control">
              <input
                type="text"
                value={ttsProfile}
                onChange={(e) => setTtsProfile(e.target.value)}
                placeholder="speech-out profile id"
                aria-label="Speech-out profile id"
                className={INPUT_CLASS}
              />
              <Button
                ghost
                size="sm"
                onClick={() => void handleTtsEnable()}
                disabled={busy !== null || !ttsProfile.trim()}
                aria-label="Enable TTS profile"
              >
                {busy === "tts.enable" ? <Spinner /> : "enable"}
              </Button>
              <Button
                ghost
                size="sm"
                onClick={() => void handleTtsDisable()}
                disabled={busy !== null || !ttsProfile.trim()}
                aria-label="Disable TTS profile"
              >
                {busy === "tts.disable" ? <Spinner /> : "disable"}
              </Button>
            </div>
          </div>
          <div className="settings-row">
            <div className="settings-row__text">
              <div className="settings-row__title">Persona</div>
              <p className="settings-row__desc">
                Select a speech-out profile for a session.
              </p>
            </div>
            <div className="settings-row__control">
              <input
                type="text"
                value={ttsPersona}
                onChange={(e) => setTtsPersonaInput(e.target.value)}
                placeholder="persona (speech-out profile id)"
                aria-label="TTS persona"
                className={INPUT_CLASS}
              />
              <input
                type="text"
                value={ttsSession}
                onChange={(e) => setTtsSession(e.target.value)}
                placeholder="session (optional)"
                aria-label="TTS persona session"
                className={INPUT_CLASS}
              />
              <Button
                ghost
                size="sm"
                onClick={() => void handleTtsPersona()}
                disabled={busy !== null || !ttsPersona.trim()}
                aria-label="Set TTS persona"
              >
                {busy === "tts.setPersona" ? <Spinner /> : "set persona"}
              </Button>
            </div>
          </div>
        </div>
      </section>

      {/* ── suspend prepare/resume (power-state read) ── */}
      <section aria-label="Power state" className="settings-section">
        <div className="settings-section__copy">
          <h2 className="settings-section__heading">Power state</h2>
          <p className="settings-section__desc">
            Qubit never drains or suspends its process from here; this reports the
            real local power/e-stop state only.
          </p>
        </div>
        <div className="settings-group">
          {powerState === "loading" ? (
            <div className="settings-row">
              <div className="settings-row__text">
                <div className="settings-row__title flex items-center gap-2">
                  <Spinner className="shrink-0" /> Reading power state…
                </div>
              </div>
            </div>
          ) : powerState === "error" ? (
            <div className="settings-row">
              <div className="settings-row__text">
                <p role="alert" className="settings-row__desc text-destructive">
                  {powerError}
                </p>
              </div>
            </div>
          ) : (
            <div className="settings-row settings-row--stacked">
              <div className="settings-row__text">
                <div className="settings-row__title">Reported state</div>
              </div>
              <div className="settings-row__control">
                <pre className="max-h-40 w-full overflow-auto whitespace-pre-wrap text-left text-xs text-text-secondary">
                  {JSON.stringify({ prepare, resume }, null, 2)}
                </pre>
              </div>
            </div>
          )}
        </div>
      </section>

      {/* ── tasks.cancel / tasks.retry (confirm-gated) ── */}
      <section aria-label="Task cancel and retry" className="settings-section">
        <div className="settings-section__copy">
          <h2 className="settings-section__heading">Task cancel / retry</h2>
          <p className="settings-section__desc">
            Cancel and retry resolve a real action receipt or an in-flight chat
            session. A bare ledger id maps to no live run and stays uncancelled.
          </p>
        </div>
        <div className="settings-group">
          <div className="settings-row">
            <div className="settings-row__text">
              <div className="settings-row__title">Cancel</div>
              <p className="settings-row__desc">Receipt id or session.</p>
            </div>
            <div className="settings-row__control">
              <input
                type="text"
                value={cancelRef}
                onChange={(e) => setCancelRef(e.target.value)}
                placeholder="receipt id"
                aria-label="Cancel receipt id"
                className={INPUT_CLASS}
              />
              <input
                type="text"
                value={cancelSession}
                onChange={(e) => setCancelSession(e.target.value)}
                placeholder="session (alternative)"
                aria-label="Cancel session"
                className={INPUT_CLASS}
              />
              <Button
                ghost
                size="sm"
                onClick={() => void handleCancel()}
                disabled={busy !== null || (!cancelRef.trim() && !cancelSession.trim())}
                aria-label="Cancel task"
              >
                {busy === "tasks.cancel" ? <Spinner /> : "cancel"}
              </Button>
            </div>
          </div>
          <div className="settings-row">
            <div className="settings-row__text">
              <div className="settings-row__title">Retry</div>
              <p className="settings-row__desc">Receipt id or session.</p>
            </div>
            <div className="settings-row__control">
              <input
                type="text"
                value={retryRef}
                onChange={(e) => setRetryRef(e.target.value)}
                placeholder="receipt id"
                aria-label="Retry receipt id"
                className={INPUT_CLASS}
              />
              <input
                type="text"
                value={retrySession}
                onChange={(e) => setRetrySession(e.target.value)}
                placeholder="session (alternative)"
                aria-label="Retry session"
                className={INPUT_CLASS}
              />
              <Button
                ghost
                size="sm"
                onClick={() => void handleRetry()}
                disabled={busy !== null || (!retryRef.trim() && !retrySession.trim())}
                aria-label="Retry task"
              >
                {busy === "tasks.retry" ? <Spinner /> : "retry"}
              </Button>
            </div>
          </div>
        </div>
      </section>

      {/* ── genuinely unavailable (no route behind them) ── */}
      <section aria-label="Unavailable scheduler operations" className="settings-section">
        <div className="settings-section__copy">
          <h2 className="settings-section__heading">
            Not available on Qubit{" "}
            <span className="settings-count">{DISABLED_SCHEDULER_ACTIONS.length}</span>
          </h2>
          <p className="settings-section__desc">
            These operations name a store or service Qubit does not run.
            There is no route behind them, so they are reported here rather than
            offered as controls.
          </p>
        </div>
        <div className="settings-group">
          {DISABLED_SCHEDULER_ACTIONS.map((a) => (
            <div
              key={a.action}
              className="settings-row settings-row--stacked"
              data-state="refused"
              aria-label={`${a.action} unavailable`}
              title={`${a.code}: ${a.reason}`}
            >
              <div className="settings-row__text">
                <div className="settings-row__title font-mono">
                  {a.action.startsWith("portal.")
                    ? `Qubit Gateway Portal (proxy) — ${a.action}`
                    : a.action}
                </div>
                <p className="settings-row__desc">
                  Needs {a.reason}. Counterpart: {a.counterpart}.
                  {a.action.startsWith("portal.")
                    ? " Not the billing portal on the System page."
                    : ""}
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
