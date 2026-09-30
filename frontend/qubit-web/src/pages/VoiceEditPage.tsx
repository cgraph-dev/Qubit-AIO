/** Qubit Settings — system-wide voice typing / selected-text editing.
 *
 *  Two parts: (1) the prompt + routing settings store, and (2) a live "try it"
 *  panel that drives the same begin/capture/transform/preview/apply backend the
 *  desktop helper uses. The feature is OFF by default; enabling it here is the
 *  explicit opt-in.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { VoiceEditPreview } from "@/components/VoiceEditPreview";
import { copyTextToClipboard } from "@/lib/clipboard";
import {
  voiceEdit,
  type PromptRow,
  type TransformPreview,
  type VoiceEditSettings,
  type VoiceLogEntry,
} from "@/lib/voiceEdit";

function logTime(ts?: number): string {
  if (typeof ts !== "number") return "";
  try {
    return new Date(ts * 1000).toLocaleTimeString();
  } catch {
    return "";
  }
}

function logEventName(event?: string): string {
  return String(event ?? "")
    .replace("qubit_compat.voice_edit.", "")
    .replace("voice_edit.helper.", "helper.")
    .replace("voice_edit.", "");
}

const LOG_FIELDS = [
  "engine",
  "state",
  "chars",
  "chars_in",
  "chars_out",
  "code",
  "ok",
  "app_class",
  "selected",
  "forced",
  "peak",
  "action",
  "model",
  "text",
  "command",
  "dictated",
  "inserted",
  "transcript",
];

function logFields(e: VoiceLogEntry): string {
  return LOG_FIELDS.filter((k) => e[k] !== undefined && e[k] !== "")
    .map((k) => `${k}=${String(e[k])}`)
    .join(" ");
}

function logIsError(e: VoiceLogEntry): boolean {
  const name = String(e.event ?? "");
  return Boolean(e.code) || e.ok === false || name.endsWith(".error") ||
    name.endsWith(".mic_error");
}

export default function VoiceEditPage() {
  const [settings, setSettings] = useState<VoiceEditSettings | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");

  // try-it panel
  const [original, setOriginal] = useState("");
  const [command, setCommand] = useState("make this clearer");
  const [preview, setPreview] = useState<TransformPreview | null>(null);
  const [requestId, setRequestId] = useState("");
  const originalRef = useRef<HTMLTextAreaElement>(null);

  // prompt editor
  const [editing, setEditing] = useState("");
  const [draft, setDraft] = useState("");
  const [importJson, setImportJson] = useState("");

  // voice activity log
  const [logEvents, setLogEvents] = useState<VoiceLogEntry[]>([]);
  const [logErrors, setLogErrors] = useState(0);
  const [logError, setLogError] = useState("");

  const load = useCallback(async () => {
    try {
      const out = await voiceEdit.settings();
      setSettings(out.settings);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const out = await voiceEdit.log(200);
        if (!cancelled) {
          setLogEvents(out.events ?? []);
          setLogErrors(out.summary?.errors ?? 0);
          setLogError("");
        }
      } catch (e) {
        if (!cancelled) setLogError(e instanceof Error ? e.message : String(e));
      }
    };
    void tick();
    const id = setInterval(tick, 3000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const update = useCallback(
    async (patch: Record<string, unknown>, note = "saved") => {
      setBusy(true);
      try {
        const out = await voiceEdit.saveSettings(patch);
        setSettings(out.settings);
        setStatus(note);
        setError("");
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  const runPreview = useCallback(async () => {
    setBusy(true);
    setPreview(null);
    setError("");
    try {
      // Keep the selection alive across the button click so the real Ctrl+C
      // capture grabs the textarea's selected text, not the button.
      const ta = originalRef.current;
      if (ta) {
        const start = ta.selectionStart;
        const end = ta.selectionEnd;
        ta.focus();
        try {
          ta.setSelectionRange(start, end);
        } catch {
          /* selection APIs unavailable */
        }
      }
      const began = await voiceEdit.begin();
      setRequestId(began.request_id);
      // Capture the page's own selection (real Ctrl+C on X11) then transform.
      await voiceEdit.capture(began.request_id);
      const out = await voiceEdit.transform(began.request_id, command);
      if (out.mode === "preview" || out.original !== undefined) {
        setPreview(out);
      } else if (out.mode === "dictate") {
        setOriginal((out as unknown as { transcript?: string }).transcript ?? "");
        setStatus("dictated");
      } else {
        setStatus(out.detail || out.code || "no preview");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [command]);

  const applyPreview = useCallback(async () => {
    if (!requestId) return;
    setBusy(true);
    try {
      originalRef.current?.focus();
      const out = await voiceEdit.apply(requestId, true);
      setStatus(out.ok ? "applied" : `${out.code ?? "error"}`);
      if (out.ok) setOriginal(preview?.proposed ?? original);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [requestId, preview, original]);

  const copyPreview = useCallback(async () => {
    if (preview?.proposed) {
      const ok = await copyTextToClipboard(preview.proposed);
      setStatus(ok ? "copied" : "clipboard unavailable");
    }
  }, [preview]);

  const setPrompt = useCallback(
    (row: PromptRow) => {
      if (editing === row.action) {
        setEditing("");
        return;
      }
      setEditing(row.action);
      setDraft(row.effective_text);
    },
    [editing],
  );

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6 p-4" data-testid="voice-edit-page">
      <header>
        <h1 className="text-lg font-semibold">Voice Typing &amp; Editing</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          System-wide: hold Right-Ctrl anywhere to dictate, or select text and speak an
          editing command. Disabled by default. No selected text is logged or persisted.
          After enabling, start the desktop helper with{" "}
          <code className="rounded bg-muted px-1">qubit-voice-typing</code>.
        </p>
      </header>

      {error ? (
        <p className="rounded bg-destructive/10 px-3 py-2 text-sm text-destructive" role="alert">
          {error}
        </p>
      ) : null}

      {settings ? (
        <>
          <section className="rounded-lg border border-border p-4">
            <h2 className="mb-3 text-sm font-semibold">Enablement &amp; routing</h2>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                data-testid="voice-edit-enabled"
                checked={settings.enabled}
                onChange={(e) => void update({ enabled: e.target.checked })}
              />
              Enable system-wide voice typing / editing
            </label>
            <label className="mt-2 flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                data-testid="voice-edit-wake"
                checked={Boolean(settings.wake?.enabled)}
                onChange={(e) =>
                  void update({ wake: { enabled: e.target.checked } })
                }
              />
              Hands-free wake command
            </label>
            <p className="mt-1 text-xs text-muted-foreground">
              Say <strong>“Hey Qubit, …”</strong> without pressing Right-Ctrl.
              The segment is transcribed locally by the same engine and only the
              wake phrase triggers a command; other speech is discarded.
            </p>
            <label className="mt-2 flex items-center gap-2 text-sm">
              Brain routing (local model decides ambiguous commands)
              <input
                type="checkbox"
                data-testid="voice-edit-brain"
                checked={settings.brain_router !== false}
                onChange={(e) => void update({ brain_router: e.target.checked })}
              />
            </label>
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <label className="text-sm">
                Routing
                <select
                  className="mt-1 w-full rounded border border-border bg-background p-1"
                  data-testid="voice-edit-routing"
                  value={settings.routing}
                  onChange={(e) => void update({ routing: e.target.value })}
                >
                  <option value="auto">auto</option>
                  <option value="local">local only</option>
                  <option value="api">API only</option>
                </select>
              </label>
              <label className="text-sm">
                Insertion method
                <select
                  className="mt-1 w-full rounded border border-border bg-background p-1"
                  value={settings.insertion_method}
                  onChange={(e) => void update({ insertion_method: e.target.value })}
                >
                  <option value="clipboard_paste">clipboard paste</option>
                  <option value="typed">simulated typing</option>
                </select>
              </label>
              <label className="text-sm">
                Local profile
                <input
                  className="mt-1 w-full rounded border border-border bg-background p-1"
                  value={settings.local_profile}
                  onChange={(e) => void update({ local_profile: e.target.value })}
                />
              </label>
              <label className="text-sm">
                API profile
                <input
                  className="mt-1 w-full rounded border border-border bg-background p-1"
                  value={settings.api_profile}
                  onChange={(e) => void update({ api_profile: e.target.value })}
                />
              </label>
              <label className="text-sm">
                Hotkey mode
                <select
                  className="mt-1 w-full rounded border border-border bg-background p-1"
                  value={settings.hotkey_mode ?? "hold"}
                  onChange={(e) => void update({ hotkey_mode: e.target.value })}
                >
                  <option value="hold">hold Right-Ctrl</option>
                  <option value="toggle">toggle Right-Ctrl</option>
                </select>
              </label>
              <label className="text-sm">
                Microphone / audio source
                <input
                  className="mt-1 w-full rounded border border-border bg-background p-1"
                  placeholder="system default"
                  value={settings.asr_device ?? ""}
                  onChange={(e) => void update({ asr_device: e.target.value })}
                />
                <span className="text-xs text-muted-foreground">
                  PulseAudio/PipeWire source name; blank uses the system default.
                </span>
              </label>
              <label className="text-sm">
                STT mode
                <select
                  className="mt-1 w-full rounded border border-border bg-background p-1"
                  data-testid="voice-edit-stt-mode"
                  value={settings.stt_mode ?? "accurate"}
                  onChange={(e) => void update({ stt_mode: e.target.value })}
                >
                  <option value="accurate">Accurate (whole-utterance, best quality)</option>
                  <option value="streaming">Streaming (live interim)</option>
                </select>
                <span className="text-xs text-muted-foreground">
                  {(settings.stt_mode ?? "accurate") === "streaming"
                    ? "Streaming decodes live partials through the streaming engine."
                    : "Accurate decodes the whole utterance with local faster-whisper."}
                </span>
              </label>
              <label className="text-sm">
                Accurate model (faster-whisper)
                <select
                  className="mt-1 w-full rounded border border-border bg-background p-1"
                  data-testid="voice-edit-stt-model"
                  value={settings.stt_model ?? "small"}
                  onChange={(e) => void update({ stt_model: e.target.value })}
                >
                  <option value="tiny">tiny ≈ 0.1 GB RAM/VRAM</option>
                  <option value="base">base ≈ 0.2 GB RAM/VRAM</option>
                  <option value="small">small ≈ 0.5 GB RAM/VRAM</option>
                  <option value="medium">medium ≈ 1.5 GB RAM/VRAM</option>
                  <option value="large-v3">large-v3 ≈ 3.1 GB RAM/VRAM</option>
                </select>
                {(settings.stt_mode ?? "accurate") === "streaming" ? (
                  <span className="text-xs text-muted-foreground">
                    Not used while Streaming is selected; used when Accurate is chosen.
                  </span>
                ) : null}
              </label>
              <label className="text-sm">
                Command router
                <select
                  className="mt-1 w-full rounded border border-border bg-background p-1"
                  data-testid="voice-edit-router"
                  value={settings.router ?? "deterministic"}
                  onChange={(e) => void update({ router: e.target.value })}
                >
                  <option value="deterministic">Deterministic</option>
                  <option value="brain">Brain (local model)</option>
                </select>
                <span className="text-xs text-muted-foreground">
                  Brain uses the local model only to disambiguate an uncertain
                  command; it never bypasses confirmation or e-stop.
                </span>
              </label>
            </div>
            <div className="mt-3 flex flex-col gap-2 text-sm">
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={settings.allow_api}
                  onChange={(e) => void update({ allow_api: e.target.checked })}
                />
                Allow API transformations (privacy)
              </label>
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  data-testid="voice-edit-confirm"
                  checked={settings.confirm_before_replace}
                  onChange={(e) => void update({ confirm_before_replace: e.target.checked })}
                />
                Confirm before replacing selected text
              </label>
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={settings.auto_send}
                  onChange={(e) => void update({ auto_send: e.target.checked })}
                />
                Auto-send after dictation (never presses Enter unless enabled)
              </label>
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  data-testid="voice-edit-block-fullscreen"
                  checked={settings.block_fullscreen !== false}
                  onChange={(e) =>
                    void update({ block_fullscreen: e.target.checked })
                  }
                />
                Never type into a full-screen app (games/video)
              </label>
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  data-testid="voice-edit-beep"
                  checked={settings.beep !== false}
                  onChange={(e) => void update({ beep: e.target.checked })}
                />
                Beep when it hears the wake word / is ready
              </label>
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  data-testid="voice-edit-debug-log"
                  checked={Boolean(settings.debug_log)}
                  onChange={(e) => void update({ debug_log: e.target.checked })}
                />
                Record full spoken transcripts in the log (local, for debugging)
              </label>
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  data-testid="voice-edit-noise"
                  checked={settings.noise_suppression !== false}
                  onChange={(e) =>
                    void update({ noise_suppression: e.target.checked })
                  }
                />
                Noise suppression / echo cancel (match browser capture)
              </label>
              <label className="flex items-center gap-2">
                Mic gain
                <input
                  type="number"
                  step="1"
                  min="1"
                  max="32"
                  className="w-20 rounded border border-border bg-background p-1"
                  value={settings.mic_gain ?? 6}
                  onChange={(e) =>
                    void update({ mic_gain: Number(e.target.value) })
                  }
                />
                <span className="text-xs text-muted-foreground">
                  boosts a quiet interface (6–12 typical)
                </span>
              </label>
            </div>
          </section>

          <section className="rounded-lg border border-border p-4">
            <h2 className="mb-3 text-sm font-semibold">Default prompts</h2>
            <ul className="flex flex-col gap-2" data-testid="voice-edit-prompts">
              {settings.prompts.map((row) => (
                <li key={row.action} className="rounded border border-border p-2">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm">
                      {row.title}{" "}
                      {row.customized ? (
                        <span className="text-xs text-amber-600">(customized)</span>
                      ) : null}
                    </span>
                    <div className="flex gap-2">
                      <button
                        type="button"
                        className="rounded border border-border px-2 py-0.5 text-xs"
                        onClick={() => setPrompt(row)}
                      >
                        {editing === row.action ? "Close" : "Edit copy"}
                      </button>
                      <button
                        type="button"
                        className="rounded border border-border px-2 py-0.5 text-xs"
                        disabled={!row.customized}
                        onClick={async () => {
                          await voiceEdit.restore(row.action);
                          await load();
                          setStatus("default restored");
                        }}
                      >
                        Restore default
                      </button>
                    </div>
                  </div>
                  {editing === row.action ? (
                    <div className="mt-2">
                      <textarea
                        className="h-40 w-full rounded border border-border bg-background p-2 text-xs"
                        aria-label={`Edit prompt ${row.action}`}
                        value={draft}
                        onChange={(e) => setDraft(e.target.value)}
                      />
                      <button
                        type="button"
                        className="mt-2 rounded bg-primary px-3 py-1 text-xs text-primary-foreground"
                        onClick={() =>
                          void update({ prompts: { [row.action]: draft } }, "prompt saved")
                        }
                      >
                        Save personal copy
                      </button>
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
            <div className="mt-3">
              <button
                type="button"
                className="rounded border border-border px-3 py-1 text-xs"
                onClick={() => void update({}, "defaults restored").then(load)}
              >
                Restore all defaults
              </button>
            </div>
          </section>

          <section className="rounded-lg border border-border p-4" data-testid="voice-edit-commands">
            <h2 className="mb-2 text-sm font-semibold">Voice commands</h2>
            <ul className="list-disc pl-5 text-sm text-muted-foreground">
              <li>
                Type text: <em>“Qubit, type this for me: your text here”</em> —
                inserts your words verbatim (replaces the selection if any).
              </li>
              <li>
                Rewrite the selection: <em>“make this clearer”</em>,{" "}
                <em>“fix the grammar”</em>, <em>“make it shorter”</em>,{" "}
                <em>“turn this into bullet points”</em>, <em>“translate to
                English”</em>, <em>“make this professional”</em>,{" "}
                <em>“summarize this”</em>, <em>“improve the prompt without
                changing the requirements”</em>.
              </li>
              <li>No selection: ordinary dictation is inserted at the cursor.</li>
            </ul>
          </section>

          <section className="rounded-lg border border-border p-4">
            <h2 className="mb-3 text-sm font-semibold">Try it (this page)</h2>
            <label className="text-sm">
              Original / selected text
              <textarea
                ref={originalRef}
                className="mt-1 h-24 w-full rounded border border-border bg-background p-2 text-sm"
                data-testid="voice-edit-original-input"
                value={original}
                onChange={(e) => setOriginal(e.target.value)}
              />
            </label>
            <label className="mt-2 block text-sm">
              Spoken command
              <input
                className="mt-1 w-full rounded border border-border bg-background p-2 text-sm"
                data-testid="voice-edit-command-input"
                value={command}
                onChange={(e) => setCommand(e.target.value)}
              />
            </label>
            <button
              type="button"
              className="mt-2 rounded bg-primary px-3 py-1 text-sm text-primary-foreground disabled:opacity-50"
              data-testid="voice-edit-run"
              disabled={busy}
              onClick={() => void runPreview()}
            >
              Capture selection &amp; preview
            </button>
            {preview ? (
              <div className="mt-3">
                <VoiceEditPreview
                  preview={preview}
                  busy={busy}
                  statusMessage={status}
                  onApply={() => void applyPreview()}
                  onCopy={() => void copyPreview()}
                  onEdit={async (text) => {
                    if (!requestId) return;
                    const out = await voiceEdit.edit(requestId, text);
                    setPreview({ ...preview, proposed: out.proposed });
                  }}
                  onRetry={() => void runPreview()}
                  onCancel={async () => {
                    if (requestId) await voiceEdit.cancel(requestId);
                    setPreview(null);
                  }}
                  onRevert={async () => {
                    if (!requestId) return;
                    const out = await voiceEdit.revert(requestId);
                    setStatus(out.ok ? "reverted" : String(out.code ?? ""));
                  }}
                />
              </div>
            ) : null}
          </section>

          <section
            className="rounded-lg border border-border p-4"
            data-testid="voice-edit-log"
          >
            <div className="mb-2 flex items-center justify-between gap-2">
              <h2 className="text-sm font-semibold">Voice activity</h2>
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground">
                  {logEvents.length} events ·{" "}
                  <span className={logErrors ? "text-destructive" : undefined}>
                    {logErrors} errors
                  </span>
                </span>
                <button
                  type="button"
                  className="rounded border border-border px-2 py-0.5 text-xs"
                  onClick={() => void copyTextToClipboard(JSON.stringify(logEvents, null, 2))}
                >
                  Copy log
                </button>
              </div>
            </div>
            <p className="mb-2 text-xs text-muted-foreground">
              Every wake/command/transform/insert attempt. Character counts and
              metadata only — never your spoken or selected text.
            </p>
            {logError ? (
              <p className="mb-2 text-xs text-destructive">{logError}</p>
            ) : null}
            <div className="max-h-72 overflow-auto rounded bg-muted/40 p-2 font-mono text-[11px] leading-relaxed">
              {logEvents.length === 0 ? (
                <p className="text-muted-foreground">No voice activity yet.</p>
              ) : (
                logEvents
                  .slice()
                  .reverse()
                  .map((e, i) => (
                    <div
                      key={`${e.ts}-${i}`}
                      data-testid="voice-log-row"
                      className={logIsError(e) ? "text-destructive" : undefined}
                    >
                      <span className="text-muted-foreground">
                        {logTime(e.ts)}
                      </span>{" "}
                      {logEventName(e.event)}{" "}
                      <span className="text-muted-foreground">
                        {logFields(e)}
                      </span>
                    </div>
                  ))
              )}
            </div>
          </section>

          <section className="rounded-lg border border-border p-4">
            <h2 className="mb-3 text-sm font-semibold">Export / import</h2>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                className="rounded border border-border px-3 py-1 text-xs"
                onClick={async () => {
                  const out = await voiceEdit.exportSettings();
                  setImportJson(JSON.stringify(out.export, null, 2));
                }}
              >
                Export settings
              </button>
              <button
                type="button"
                className="rounded border border-border px-3 py-1 text-xs"
                disabled={!importJson.trim()}
                onClick={async () => {
                  try {
                    const parsed = JSON.parse(importJson) as Record<string, unknown>;
                    const out = await voiceEdit.importSettings(parsed);
                    setSettings(out.settings);
                    setStatus("imported");
                    setError("");
                  } catch (e) {
                    setError(e instanceof Error ? e.message : String(e));
                  }
                }}
              >
                Import settings
              </button>
            </div>
            <textarea
              className="mt-2 h-32 w-full rounded border border-border bg-background p-2 font-mono text-xs"
              aria-label="Settings JSON"
              value={importJson}
              onChange={(e) => setImportJson(e.target.value)}
            />
          </section>
        </>
      ) : (
        <p className="text-sm text-muted-foreground">Loading…</p>
      )}
      {status && !preview ? (
        <p className="text-xs text-muted-foreground" data-testid="voice-edit-page-status">
          {status}
        </p>
      ) : null}
    </div>
  );
}
