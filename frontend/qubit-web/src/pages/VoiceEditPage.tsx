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
  type LocalModel,
  type PromptRow,
  type TransformPreview,
  type TtsProvider,
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

/** Human size for a local model, e.g. 2_000_000_000 -> "≈1.9 GB". */
function formatModelSize(bytes: number): string {
  if (!bytes || bytes <= 0) return "size unknown";
  const gb = bytes / 1024 ** 3;
  return `≈${gb.toFixed(gb >= 10 ? 0 : 1)} GB`;
}

/** Parse a comma-separated tag input into a clean, de-duplicated list. */
function splitCues(raw: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const part of raw.split(",")) {
    const cue = part.trim();
    if (cue && !seen.has(cue)) {
      seen.add(cue);
      out.push(cue);
    }
  }
  return out;
}

/** One-line explanation for each text-to-speech provider. */
const TTS_PROVIDER_HELP: Record<TtsProvider, string> = {
  piper: "Fast and fully local — lowest latency, clear but synthetic.",
  kokoro: "Natural-sounding and local, but slower to start.",
  edge: "Natural and fast, but streams the audio over the internet.",
  hybrid: "Tries the local provider first and falls back to the API when needed.",
  openai: "High-quality cloud voices; sends the answer text to the API.",
};

const CAPTURE_SOURCE_HELP: Record<string, string> = {
  auto: "Browser-grade capture: noise suppression + high-pass + auto gain (what Firefox does).",
  raw: "Unprocessed device input; loudest but noisiest — good only for a clean external mic.",
  aec: "Processed source with echo cancellation; removes speaker output the mic re-hears.",
};

/** A select over the local models actually installed on this machine.
 *  Value "" means "auto / use the configured default". A previously saved
 *  model that is no longer installed is still shown so the setting is not
 *  silently lost. */
function ModelSelect({
  label,
  testId,
  value,
  models,
  autoLabel,
  help,
  onChange,
}: {
  label: string;
  testId: string;
  value: string;
  models: LocalModel[];
  autoLabel: string;
  help: string;
  onChange: (value: string) => void;
}) {
  // Ollama lists tags as `name:latest` while the config stores `name`; match
  // and de-duplicate on the base name so a configured model never looks
  // "not installed" just because of the `:tag` suffix.
  const base = (s: string) => s.split(":")[0];
  const seen = new Map<string, LocalModel>();
  for (const m of models) {
    const b = base(m.name);
    if (b && !seen.has(b)) seen.set(b, { name: b, size_bytes: m.size_bytes });
  }
  const opts = [...seen.values()];
  const installed = !value || opts.some((m) => m.name === base(value));
  return (
    <label className="text-sm">
      {label}
      <select
        className="mt-1 w-full rounded border border-border bg-background p-1"
        data-testid={testId}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">{autoLabel}</option>
        {value && !installed ? (
          <option value={value}>{value} (not installed on this machine)</option>
        ) : null}
        {opts.map((m) => (
          <option key={m.name} value={m.name}>
            {m.name} ({formatModelSize(m.size_bytes)})
          </option>
        ))}
      </select>
      <span className="mt-0.5 block text-xs text-muted-foreground">{help}</span>
    </label>
  );
}

/** One titled settings section with a one-line explanation. */
function Section({
  title,
  intro,
  testId,
  children,
}: {
  title: string;
  intro: string;
  testId?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-border p-4" data-testid={testId}>
      <h2 className="text-sm font-semibold">{title}</h2>
      <p className="mb-3 mt-1 text-xs text-muted-foreground">{intro}</p>
      {children}
    </section>
  );
}

export default function VoiceEditPage() {
  const [settings, setSettings] = useState<VoiceEditSettings | null>(null);
  const [models, setModels] = useState<LocalModel[]>([]);
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

  // brain section: comma-separated detail cues, edited as free text
  const [detailCues, setDetailCues] = useState("");

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

  // The installed-model list is per-machine and best-effort: a failure must
  // never block the settings form — the pickers just show "Auto / configured".
  const loadModels = useCallback(async () => {
    try {
      const out = await voiceEdit.models();
      setModels(out.models ?? []);
    } catch {
      setModels([]);
    }
  }, []);

  useEffect(() => {
    void load();
    void loadModels();
  }, [load, loadModels]);

  useEffect(() => {
    setDetailCues((settings?.detail_cues ?? []).join(", "));
  }, [settings?.detail_cues]);

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

  const sttMode = settings?.stt_mode ?? "accurate";
  const ttsProvider: TtsProvider = settings?.tts_provider ?? "piper";
  const captureSource = settings?.capture_source ?? "auto";

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
          <section
            className="rounded-lg border border-border p-4"
            data-testid="voice-edit-section-general"
          >
            <h2 className="text-sm font-semibold">Enablement &amp; operation</h2>
            <p className="mb-3 mt-1 text-xs text-muted-foreground">
              Master switches and how Qubit types into other apps. Voice typing
              stays off until you enable it here.
            </p>
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
                Insertion method
                <select
                  className="mt-1 w-full rounded border border-border bg-background p-1"
                  value={settings.insertion_method}
                  onChange={(e) => void update({ insertion_method: e.target.value })}
                >
                  <option value="clipboard_paste">clipboard paste</option>
                  <option value="typed">simulated typing</option>
                </select>
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  How dictated text is placed into the focused app.
                </span>
              </label>
              <label className="text-sm">
                Local profile
                <input
                  className="mt-1 w-full rounded border border-border bg-background p-1"
                  value={settings.local_profile}
                  onChange={(e) => void update({ local_profile: e.target.value })}
                />
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  Named local brain profile used for on-machine work.
                </span>
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
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  Hold to talk, or press once to start and stop.
                </span>
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
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  Brain uses the local model only to disambiguate an uncertain
                  command; it never bypasses confirmation or e-stop.
                </span>
              </label>
            </div>
            <div className="mt-3 flex flex-col gap-2 text-sm">
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
                  data-testid="voice-edit-noise"
                  checked={settings.noise_suppression !== false}
                  onChange={(e) =>
                    void update({ noise_suppression: e.target.checked })
                  }
                />
                Noise suppression / echo cancel (match browser capture)
              </label>
            </div>
          </section>

          <Section
            title="Hearing you (speech → text)"
            testId="voice-edit-section-hearing"
            intro="How Qubit listens to your microphone and turns speech into text. This runs locally on your machine."
          >
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="text-sm">
                Speech engine
                <select
                  className="mt-1 w-full rounded border border-border bg-background p-1"
                  data-testid="voice-edit-stt-mode"
                  value={settings.stt_mode ?? "accurate"}
                  onChange={(e) => void update({ stt_mode: e.target.value })}
                >
                  <option value="accurate">Accurate (whole-utterance, best quality)</option>
                  <option value="streaming">Streaming (live interim)</option>
                </select>
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  {sttMode === "streaming"
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
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  {sttMode === "streaming"
                    ? "Not used while Streaming is selected; used when Accurate is chosen."
                    : "Bigger models transcribe more accurately but use more RAM/VRAM."}
                </span>
              </label>
              <label className="text-sm">
                Microphone / audio source
                <input
                  className="mt-1 w-full rounded border border-border bg-background p-1"
                  placeholder="system default"
                  value={settings.asr_device ?? ""}
                  onChange={(e) => void update({ asr_device: e.target.value })}
                />
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  PulseAudio/PipeWire source name; blank uses the system default.
                </span>
              </label>
              <label className="text-sm">
                Capture source
                <select
                  className="mt-1 w-full rounded border border-border bg-background p-1"
                  data-testid="voice-edit-capture-source"
                  value={captureSource}
                  onChange={(e) => void update({ capture_source: e.target.value })}
                >
                  <option value="auto">Auto (browser-grade AGC + noise suppression)</option>
                  <option value="raw">Raw device input</option>
                  <option value="aec">Echo cancellation</option>
                </select>
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  {CAPTURE_SOURCE_HELP[captureSource] ?? CAPTURE_SOURCE_HELP.auto}
                </span>
              </label>
            </div>
            <div className="mt-3 flex flex-col gap-2 text-sm">
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  data-testid="voice-edit-agc"
                  checked={Boolean(settings.agc)}
                  onChange={(e) => void update({ agc: e.target.checked })}
                />
                Automatic gain control (level out quiet and loud speech)
              </label>
              <label className="flex items-center gap-2">
                Mic gain
                <input
                  type="number"
                  step="1"
                  min="1"
                  max="32"
                  data-testid="voice-edit-mic-gain"
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
          </Section>

          <Section
            title="The brain (which model answers)"
            testId="voice-edit-section-brain"
            intro="Pick which model answers voice questions. The models listed below are installed on this machine; a model that is missing later falls back to Qubit's configured default."
          >
            <div className="grid gap-3 sm:grid-cols-2">
              <ModelSelect
                label="Local model (this machine)"
                testId="voice-edit-local-model"
                value={settings.local_model ?? ""}
                models={models}
                autoLabel="Auto / configured"
                help="Base model for local voice work; Auto uses Qubit's configured local model."
                onChange={(v) => void update({ local_model: v })}
              />
              <ModelSelect
                label="Chat model"
                testId="voice-edit-chat-model"
                value={settings.chat_model ?? ""}
                models={models}
                autoLabel="Auto / configured"
                help="Model that answers ordinary voice questions; Auto reuses the configured model."
                onChange={(v) => void update({ chat_model: v })}
              />
              <ModelSelect
                label="Detail model"
                testId="voice-edit-detail-model"
                value={settings.detail_model ?? ""}
                models={models}
                autoLabel="Auto / configured"
                help="Model used for “give me more detail” answers; Auto reuses the chat model."
                onChange={(v) => void update({ detail_model: v })}
              />
              <label className="text-sm">
                Detail cues
                <input
                  className="mt-1 w-full rounded border border-border bg-background p-1"
                  data-testid="voice-edit-detail-cues"
                  placeholder="more detail, explain further"
                  value={detailCues}
                  onChange={(e) => setDetailCues(e.target.value)}
                  onBlur={() => void update({ detail_cues: splitCues(detailCues) })}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      void update({ detail_cues: splitCues(detailCues) });
                    }
                  }}
                />
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  Comma-separated phrases that ask for a longer, more detailed answer.
                </span>
              </label>
              <label className="text-sm">
                Recent turns of context
                <input
                  type="number"
                  min="2"
                  max="40"
                  step="1"
                  data-testid="voice-edit-voice-recent-turns"
                  className="mt-1 w-full rounded border border-border bg-background p-1"
                  value={settings.voice_recent_turns ?? 4}
                  onChange={(e) =>
                    void update({ voice_recent_turns: Number(e.target.value) })
                  }
                />
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  How many recent exchanges the voice brain sees (2–40).
                </span>
              </label>
              <label className="text-sm">
                Temperature
                <div className="mt-1 flex items-center gap-2">
                  <input
                    type="range"
                    min="0"
                    max="2"
                    step="0.1"
                    className="flex-1"
                    data-testid="voice-edit-local-temperature"
                    value={settings.local_temperature ?? 0.7}
                    onChange={(e) =>
                      void update({ local_temperature: Number(e.target.value) })
                    }
                  />
                  <span className="w-10 text-right text-xs text-muted-foreground">
                    {(settings.local_temperature ?? 0.7).toFixed(1)}
                  </span>
                </div>
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  Lower is focused and repeatable; higher is more creative (0–2).
                </span>
              </label>
              <label className="text-sm">
                Routing
                <select
                  className="mt-1 w-full rounded border border-border bg-background p-1"
                  data-testid="voice-edit-routing"
                  value={settings.routing}
                  onChange={(e) => void update({ routing: e.target.value })}
                >
                  <option value="local">Local-first</option>
                  <option value="auto">Auto</option>
                  <option value="api">API only</option>
                </select>
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  Local-first keeps answers on this machine; API only uses your API
                  profile; Auto chooses per request.
                </span>
              </label>
              <label className="text-sm">
                API profile
                <input
                  className="mt-1 w-full rounded border border-border bg-background p-1"
                  data-testid="voice-edit-api-profile"
                  value={settings.api_profile}
                  onChange={(e) => void update({ api_profile: e.target.value })}
                />
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  Named provider profile used when routing to the API.
                </span>
              </label>
            </div>
            <div className="mt-3 flex flex-col gap-2 text-sm">
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  data-testid="voice-edit-allow-api"
                  checked={settings.allow_api}
                  onChange={(e) => void update({ allow_api: e.target.checked })}
                />
                Allow API transformations (may send text off this machine)
              </label>
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  data-testid="voice-edit-local-think"
                  checked={Boolean(settings.local_think)}
                  onChange={(e) => void update({ local_think: e.target.checked })}
                />
                Let the local model “think” before answering (slower, more careful)
              </label>
            </div>
          </Section>

          <Section
            title="Talking back (voice responses)"
            testId="voice-edit-section-tts"
            intro="Spoken answers use a text-to-speech provider. Pick one that fits your machine and privacy needs."
          >
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                data-testid="voice-edit-speak-answers"
                checked={Boolean(settings.speak_answers)}
                onChange={(e) => void update({ speak_answers: e.target.checked })}
              />
              Speak answers aloud instead of only showing them
            </label>
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <label className="text-sm">
                Voice provider
                <select
                  className="mt-1 w-full rounded border border-border bg-background p-1"
                  data-testid="voice-edit-tts-provider"
                  value={ttsProvider}
                  onChange={(e) => void update({ tts_provider: e.target.value })}
                >
                  <option value="piper">piper — fast, local</option>
                  <option value="kokoro">kokoro — natural, slower, local</option>
                  <option value="edge">edge — natural, fast, needs internet</option>
                  <option value="hybrid">hybrid — local first, API fallback</option>
                  <option value="openai">openai — cloud voices</option>
                </select>
                <span
                  className="mt-0.5 block text-xs text-muted-foreground"
                  data-testid="voice-edit-tts-provider-help"
                >
                  {TTS_PROVIDER_HELP[ttsProvider]}
                </span>
              </label>
              <label className="text-sm">
                Voice
                <input
                  className="mt-1 w-full rounded border border-border bg-background p-1"
                  data-testid="voice-edit-tts-voice"
                  placeholder="en_US-amy-medium"
                  value={settings.tts_voice ?? ""}
                  onChange={(e) => void update({ tts_voice: e.target.value })}
                />
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  Provider-specific voice name; blank uses the provider default.
                </span>
              </label>
              {ttsProvider === "kokoro" ? (
                <label className="text-sm">
                  Kokoro speaker
                  <input
                    type="number"
                    min="0"
                    max="10"
                    step="1"
                    className="mt-1 w-full rounded border border-border bg-background p-1"
                    data-testid="voice-edit-tts-kokoro-sid"
                    value={settings.tts_kokoro_voice_sid ?? 1}
                    onChange={(e) =>
                      void update({
                        tts_kokoro_voice_sid: Number(e.target.value),
                      })
                    }
                  />
                  <span className="mt-0.5 block text-xs text-muted-foreground">
                    Kokoro speaker id (0–10).
                  </span>
                </label>
              ) : null}
            </div>
          </Section>

          <Section
            title="Panel &amp; diagnostics"
            testId="voice-edit-section-panel"
            intro="Control the on-screen panel and local debugging. Nothing here is sent anywhere."
          >
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                data-testid="voice-edit-history-panel"
                checked={Boolean(settings.history_panel)}
                onChange={(e) => void update({ history_panel: e.target.checked })}
              />
              Show the on-screen voice history panel
            </label>
            <label className="mt-2 flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                data-testid="voice-edit-debug-log"
                checked={Boolean(settings.debug_log)}
                onChange={(e) => void update({ debug_log: e.target.checked })}
              />
              Record full spoken transcripts in the log (local, for debugging)
            </label>
            <p className="mt-1 text-xs text-muted-foreground">
              Debug logging stores what was heard in the local activity log — it can
              include sensitive speech, so leave it off unless troubleshooting.
            </p>
          </Section>

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
