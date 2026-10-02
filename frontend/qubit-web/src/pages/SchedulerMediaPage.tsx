/**
 * Scheduler + media console (route /qubit-scheduler).
 *
 * Layout mirrors the upstream scheduler UI (`ui/src/ui/views/cron.ts`),
 * rebranded to the Qubit voice: a summary strip on top (Schedule /
 * Speech / Power + refresh), then the schedule workspace (the enabled-only
 * toggle), the speech-profile form, the read-only power state, the task
 * cancel/retry history lane, and the disabled-with-reason rows.
 *
 * Behavior is unchanged from the Qubit backend contract below:
 * the enabled-only schedule toggle (`cron.update`), the TTS provider/
 * enable/disable/persona writes through the canonical speech profiles
 * (confirm-gated), the power-state reads (`gateway.suspend.prepare` /
 * `gateway.suspend.resume`), and task cancel/retry by receipt or session
 * (confirm-gated).
 *
 * Everything without a local counterpart — per-job scratch, the suspend
 * handoff, the portal proxy, suggestion registry, dismiss registries, and the
 * mention inbox — renders disabled with the exact backend prerequisite (never
 * called). The portal rows are labeled "Qubit Gateway Portal (proxy)" to
 * disambiguate them from the billing portal on the System page.
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
import { cn } from "@/lib/utils";

/** Button press IS the confirmation (same pattern as the product pages). */
const CONFIRM_YES = () => true;

function errText(e: unknown, fallback: string): string {
  return refusalOrError(e, fallback);
}

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
    <div className={cn("flex min-h-0 w-full min-w-0 flex-1 flex-col gap-3 pt-1 sm:pt-2")}>
      <div className="flex items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-medium tracking-[0.08em]">Scheduler and media</h2>
          <p className="text-xs text-text-tertiary">
            Schedule toggles, speech profiles, power state, and task cancel/retry.
          </p>
        </div>
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

      {/* ── summary strip (live, read-only; mirrors upstream cron-summary-strip) ── */}
      <section
        aria-label="Scheduler summary"
        className="border border-border bg-background-base/60 px-3 py-2"
      >
        <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-xs">
          <div>
            <span className="text-text-tertiary">Schedule </span>
            <span
              className={cn(
                "font-mono",
                cronEnabled ? "text-text-secondary" : "text-destructive",
              )}
            >
              {cronEnabled ? "Yes" : "No"}
            </span>
          </div>
          <div>
            <span className="text-text-tertiary">Speech </span>
            <span className="font-mono text-text-secondary">{ttsProvider}</span>
          </div>
          <div>
            <span className="text-text-tertiary">Power </span>
            <span className="font-mono text-text-secondary">
              {powerState === "loading"
                ? "…"
                : powerState === "error"
                  ? "unavailable"
                  : "reported"}
            </span>
          </div>
          <span className="ml-auto text-text-tertiary">
            {powerError ?? (notice ? "settled" : "")}
          </span>
        </div>
      </section>

      {/* ── cron.update (enabled-only) ── */}
      <section
        aria-label="Schedule enabled toggle"
        className="border border-border bg-background-base/60 px-3 py-2"
      >
        <h3 className="text-xs font-medium text-text-secondary">Schedule enabled (only)</h3>
        <p className="mt-0.5 text-[0.6875rem] text-text-tertiary">
          The schedule store applies only the enabled flag (pause/resume). No other
          patch field has a local counterpart, so none is sent.
        </p>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <input
            type="text"
            inputMode="numeric"
            value={cronId}
            onChange={(e) => setCronId(e.target.value)}
            placeholder="job id"
            aria-label="Schedule job id"
            className="min-w-0 flex-1 border border-border bg-background-base px-2 py-1 font-mono text-xs"
          />
          <label className="flex items-center gap-1 text-xs text-text-secondary">
            <input
              type="checkbox"
              checked={cronEnabled}
              onChange={(e) => setCronEnabled(e.target.checked)}
              aria-label="Schedule enabled"
            />
            enabled
          </label>
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
      </section>

      {/* ── TTS writes (confirm-gated) ── */}
      <section
        aria-label="Speech profiles"
        className="border border-border bg-background-base/60 px-3 py-2"
      >
        <h3 className="text-xs font-medium text-text-secondary">Speech (TTS)</h3>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <select
            value={ttsProvider}
            onChange={(e) => setTtsProviderChoice(e.target.value)}
            aria-label="TTS provider"
            className="border border-border bg-background-base px-2 py-1 font-mono text-xs"
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
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input
            type="text"
            value={ttsProfile}
            onChange={(e) => setTtsProfile(e.target.value)}
            placeholder="speech-out profile id"
            aria-label="Speech-out profile id"
            className="min-w-0 flex-1 border border-border bg-background-base px-2 py-1 font-mono text-xs"
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
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input
            type="text"
            value={ttsPersona}
            onChange={(e) => setTtsPersonaInput(e.target.value)}
            placeholder="persona (speech-out profile id)"
            aria-label="TTS persona"
            className="min-w-0 flex-1 border border-border bg-background-base px-2 py-1 font-mono text-xs"
          />
          <input
            type="text"
            value={ttsSession}
            onChange={(e) => setTtsSession(e.target.value)}
            placeholder="session (optional)"
            aria-label="TTS persona session"
            className="min-w-0 flex-1 border border-border bg-background-base px-2 py-1 font-mono text-xs"
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
        <p className="mt-1 text-[0.6875rem] text-text-tertiary">
          Qubit keeps no global TTS auto-mode flag or persona bundle: enable/disable
          flips the per-profile flag and persona selects a profile for a session.
        </p>
      </section>

      {/* ── suspend prepare/resume (power-state read) ── */}
      <section
        aria-label="Power state"
        className="border border-border bg-background-base/60 px-3 py-2"
      >
        <h3 className="text-xs font-medium text-text-secondary">Power state (read-only)</h3>
        {powerState === "loading" ? (
          <p className="mt-1 flex items-center gap-2 text-xs text-text-tertiary">
            <Spinner className="shrink-0" /> Reading power state…
          </p>
        ) : powerState === "error" ? (
          <p role="alert" className="mt-1 text-xs text-destructive">
            {powerError}
          </p>
        ) : (
          <>
            <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap text-xs text-text-secondary">
              {JSON.stringify({ prepare, resume }, null, 2)}
            </pre>
            <p className="mt-1 text-[0.6875rem] text-text-tertiary">
              Qubit never drains or suspends its process from here; this reports the
              real local power/e-stop state only.
            </p>
          </>
        )}
      </section>

      {/* ── tasks.cancel / tasks.retry (confirm-gated) ── */}
      <section
        aria-label="Task cancel and retry"
        className="border border-border bg-background-base/60 px-3 py-2"
      >
        <h3 className="text-xs font-medium text-text-secondary">Task cancel / retry</h3>
        <p className="mt-0.5 text-[0.6875rem] text-text-tertiary">
          Cancel and retry resolve a real action receipt or an in-flight chat
          session. A bare ledger id maps to no live run and stays uncancelled.
        </p>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <input
            type="text"
            value={cancelRef}
            onChange={(e) => setCancelRef(e.target.value)}
            placeholder="receipt id"
            aria-label="Cancel receipt id"
            className="min-w-0 flex-1 border border-border bg-background-base px-2 py-1 font-mono text-xs"
          />
          <input
            type="text"
            value={cancelSession}
            onChange={(e) => setCancelSession(e.target.value)}
            placeholder="session (alternative)"
            aria-label="Cancel session"
            className="min-w-0 flex-1 border border-border bg-background-base px-2 py-1 font-mono text-xs"
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
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input
            type="text"
            value={retryRef}
            onChange={(e) => setRetryRef(e.target.value)}
            placeholder="receipt id"
            aria-label="Retry receipt id"
            className="min-w-0 flex-1 border border-border bg-background-base px-2 py-1 font-mono text-xs"
          />
          <input
            type="text"
            value={retrySession}
            onChange={(e) => setRetrySession(e.target.value)}
            placeholder="session (alternative)"
            aria-label="Retry session"
            className="min-w-0 flex-1 border border-border bg-background-base px-2 py-1 font-mono text-xs"
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
      </section>

      {/* ── disabled-with-reason (never called) ── */}
      <section
        aria-label="Unavailable scheduler operations"
        className="border border-border bg-background-base/40 px-3 py-2"
      >
        <h3 className="text-xs font-medium text-text-secondary">Unavailable (operator-owned)</h3>
        <ul className="mt-1 flex flex-col gap-1" aria-label="Disabled scheduler actions">
          {DISABLED_SCHEDULER_ACTIONS.map((a) => (
            <li
              key={a.action}
              className="flex flex-wrap items-center gap-x-3 gap-y-1 border border-border px-2 py-1 text-xs"
            >
              <span className="font-mono text-text-secondary">
                {a.action.startsWith("portal.")
                  ? `Qubit Gateway Portal (proxy) — ${a.action}`
                  : a.action}
              </span>
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
                {a.action.startsWith("portal.")
                  ? " Not the billing portal on the System page."
                  : ""}
              </span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
