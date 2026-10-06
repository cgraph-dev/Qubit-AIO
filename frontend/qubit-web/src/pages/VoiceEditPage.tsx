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
  VOICE_EDIT_ACTIONS,
  CUSTOM_COMMAND_ACTIONS,
  clampVoiceEditNumber,
  WAKE_LIMITS,
  type CustomCommand,
  type LocalModel,
  type PromptRow,
  type TransformPreview,
  type TtsProvider,
  type VoiceCommandGroup,
  type VoiceCommandsCatalog,
  type VoiceEditSettings,
  type VoiceLogEntry,
  type WakeRoute,
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

/** Encode a wake-route target from the target-mode picker. "current" keeps the
 *  configured default target; the other modes are self-describing prefixes so
 *  the stored route reads back as `mode:value`. */
function encodeRouteTarget(mode: string, value: string): string {
  const trimmed = value.trim();
  if (mode === "current") return "current";
  return trimmed ? `${mode}:${trimmed}` : mode;
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
  auto: "Default device source (same as Raw). Choose Echo cancellation to record from the filtered source.",
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

/** Browsable voice-commands catalog + user aliases (collapsed by default).
 *  The catalog is derived live from the backend registries
 *  (`GET /api/voice-edit/commands`); aliases persist through the existing
 *  settings PUT (`custom_commands`) with a full-list replace. */
function VoiceCommandsSection({
  catalog,
  busy,
  onAdd,
  onDelete,
  aliasPhrase,
  setAliasPhrase,
  aliasAction,
  setAliasAction,
  aliasTarget,
  setAliasTarget,
}: {
  catalog: VoiceCommandsCatalog | null;
  busy: boolean;
  onAdd: (entry: CustomCommand) => void;
  onDelete: (phrase: string) => void;
  aliasPhrase: string;
  setAliasPhrase: (v: string) => void;
  aliasAction: string;
  setAliasAction: (v: string) => void;
  aliasTarget: string;
  setAliasTarget: (v: string) => void;
}) {
  const groups: VoiceCommandGroup[] = catalog?.groups ?? [];
  const aliases: CustomCommand[] = catalog?.custom_commands ?? [];
  const actions: string[] =
    catalog?.custom_actions?.length ? catalog.custom_actions : CUSTOM_COMMAND_ACTIONS;
  const canAdd =
    !busy && aliasPhrase.trim().length > 0 && aliasTarget.trim().length > 0;
  return (
    <details
      className="rounded-lg border border-border p-4"
      data-testid="voice-edit-commands"
    >
      <summary
        className="cursor-pointer text-sm font-semibold"
        data-testid="voice-edit-commands-toggle"
      >
        Voice commands
      </summary>
      <p className="mb-3 mt-1 text-xs text-muted-foreground">
        Everything the voice path understands, read live from the backend.
        Your own shortcuts (“aliases”) match first — say the phrase and the
        action runs with your target.
      </p>
      {groups.length === 0 ? (
        <p className="mb-2 text-xs text-muted-foreground">
          Catalog unavailable — the commands endpoint did not answer.
        </p>
      ) : (
        groups.map((g) => (
          <div key={g.id} className="mb-3" data-testid={`voice-commands-group-${g.id}`}>
            <h3 className="text-sm font-semibold">{g.title}</h3>
            {g.intro ? (
              <p className="mb-1 text-xs text-muted-foreground">{g.intro}</p>
            ) : null}
            <ul className="flex flex-col gap-1">
              {g.items.map((item) => (
                <li
                  key={item.name}
                  className="text-sm"
                  data-testid={`voice-command-${g.id}-${item.name}`}
                >
                  <code className="rounded bg-muted px-1">{item.name}</code>
                  <span className="text-muted-foreground"> — {item.desc}</span>
                  {item.confirm ? (
                    <span className="ml-1 text-xs text-amber-600">(asks first)</span>
                  ) : null}
                  {item.examples.length > 0 ? (
                    <span className="block pl-2 text-xs text-muted-foreground">
                      Try:{" "}
                      {item.examples.map((ex, i) => (
                        <span key={i}>
                          {i > 0 ? "; " : ""}
                          <em>“{ex.text}”</em>
                        </span>
                      ))}
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
          </div>
        ))
      )}
      <div className="mt-2 border-t border-border pt-3">
        <h3 className="text-sm font-semibold">My shortcuts</h3>
        {aliases.length === 0 ? (
          <p
            className="mb-2 text-xs text-muted-foreground"
            data-testid="voice-edit-aliases-empty"
          >
            No shortcuts yet — add one below.
          </p>
        ) : (
          <ul className="mb-2 flex flex-col gap-1">
            {aliases.map((a, i) => (
              <li
                key={`${a.phrase}-${i}`}
                className="flex items-center gap-2 text-sm"
                data-testid={`voice-edit-alias-row-${i}`}
              >
                <em>“{a.phrase}”</em>
                <span className="text-muted-foreground">
                  → {a.action} ({a.target})
                </span>
                <button
                  type="button"
                  className="rounded border border-border px-2 py-0.5 text-xs"
                  data-testid={`voice-edit-alias-delete-${i}`}
                  disabled={busy}
                  onClick={() => onDelete(a.phrase)}
                >
                  Delete
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="flex flex-wrap items-end gap-2">
          <label className="text-sm">
            Say
            <input
              className="mt-1 w-44 rounded border border-border bg-background p-1"
              data-testid="voice-edit-alias-phrase"
              placeholder="e.g. open my dashboard"
              value={aliasPhrase}
              onChange={(e) => setAliasPhrase(e.target.value)}
            />
          </label>
          <label className="text-sm">
            Do
            <select
              className="mt-1 rounded border border-border bg-background p-1"
              data-testid="voice-edit-alias-action"
              value={aliasAction}
              onChange={(e) => setAliasAction(e.target.value)}
            >
              {actions.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm">
            Target
            <input
              className="mt-1 w-44 rounded border border-border bg-background p-1"
              data-testid="voice-edit-alias-target"
              placeholder="URL, app, song, note…"
              value={aliasTarget}
              onChange={(e) => setAliasTarget(e.target.value)}
            />
          </label>
          <button
            type="button"
            className="rounded border border-border px-3 py-1 text-xs"
            data-testid="voice-edit-alias-add"
            disabled={!canAdd}
            onClick={() =>
              onAdd({
                phrase: aliasPhrase.trim(),
                action: aliasAction,
                target: aliasTarget.trim(),
              })
            }
          >
            Add shortcut
          </button>
        </div>
      </div>
    </details>
  );
}

export default function VoiceEditPage() {
  const [settings, setSettings] = useState<VoiceEditSettings | null>(null);
  const [models, setModels] = useState<LocalModel[]>([]);
  const [catalog, setCatalog] = useState<VoiceCommandsCatalog | null>(null);
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

  // wake sub-editors: drafted locally, saved as one merged wake:{...} PUT
  const [wakePhrase, setWakePhrase] = useState("hey qubit");
  const [wakeSilence, setWakeSilence] = useState("900");
  const [wakeRms, setWakeRms] = useState("0.0018");
  const [wakeEngine, setWakeEngine] = useState("auto");
  const [wakeKws, setWakeKws] = useState("0.25");
  const [wakeAsrFallback, setWakeAsrFallback] = useState(true);
  const [wakeAsrFallbackMaxS, setWakeAsrFallbackMaxS] = useState("8");
  const [wakeMaxS, setWakeMaxS] = useState("8");
  const [wakeVad, setWakeVad] = useState("auto");

  // wake words: draft for the add input; the list itself comes from settings.
  const [wakeWordDraft, setWakeWordDraft] = useState("");

  // wake routing: draft for the add form (trigger + target mode + value).
  const [routeTrigger, setRouteTrigger] = useState("");
  const [routeMode, setRouteMode] = useState("current");
  const [routeValue, setRouteValue] = useState("");

  // per-app mapping: new-row draft (app class + action)
  const [newApp, setNewApp] = useState("");
  const [newAction, setNewAction] = useState("rewrite");

  // custom alias drafts (phrase + action + target)
  const [aliasPhrase, setAliasPhrase] = useState("");
  const [aliasAction, setAliasAction] = useState(CUSTOM_COMMAND_ACTIONS[0]);
  const [aliasTarget, setAliasTarget] = useState("");

  // advanced: STT engine text is drafted, saved on blur/Enter (like detail cues).
  // service_url / stt_ws_url are PUT-only (the GET view never echoes them),
  // so they stay blank-until-edited drafts in this session.
  const [sttEngine, setSttEngine] = useState("");
  const [serviceUrl, setServiceUrl] = useState("");
  const [sttWsUrl, setSttWsUrl] = useState("");

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

  // The commands catalog is derived live on the backend; a failure must
  // never block the settings form — the section just shows a fallback note.
  const loadCommands = useCallback(async () => {
    try {
      const out = await voiceEdit.commands();
      setCatalog({
        groups: out.groups ?? [],
        custom_commands: out.custom_commands ?? [],
        custom_actions: out.custom_actions ?? [],
      });
    } catch {
      setCatalog(null);
    }
  }, []);

  useEffect(() => {
    void load();
    void loadModels();
    void loadCommands();
  }, [load, loadModels, loadCommands]);

  useEffect(() => {
    setDetailCues((settings?.detail_cues ?? []).join(", "));
  }, [settings?.detail_cues]);

  useEffect(() => {
    const w = settings?.wake;
    if (!w) return;
    setWakePhrase(w.phrase ?? "hey qubit");
    setWakeSilence(String(w.silence_ms ?? 900));
    setWakeRms(String(w.rms_gate ?? 0.0018));
    setWakeEngine(w.engine ?? "auto");
    setWakeKws(String(w.kws_threshold ?? 0.25));
    setWakeAsrFallback(w.asr_fallback !== false);
    setWakeAsrFallbackMaxS(String(w.asr_fallback_max_s ?? 8));
    setWakeMaxS(String(w.max_s ?? 8));
    setWakeVad(w.vad ?? "auto");
  }, [settings?.wake]);

  useEffect(() => {
    setSttEngine(settings?.stt_engine ?? "");
    // PUT-only keys: adopt them only if a future backend echoes them back.
    if (settings?.service_url !== undefined) setServiceUrl(settings.service_url ?? "");
    if (settings?.stt_ws_url !== undefined) setSttWsUrl(settings.stt_ws_url ?? "");
  }, [settings?.stt_engine, settings?.service_url, settings?.stt_ws_url]);

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

  // Aliases persist as a full-list replace through the existing settings
  // PUT (the backend validates each row and echoes the cleaned list).
  const currentAliases = useCallback(
    (): CustomCommand[] =>
      catalog?.custom_commands ?? settings?.custom_commands ?? [],
    [catalog, settings],
  );

  const addAlias = useCallback(
    async (entry: CustomCommand) => {
      const next = [
        ...currentAliases().filter(
          (a) => a.phrase.toLowerCase() !== entry.phrase.toLowerCase(),
        ),
        entry,
      ];
      await update({ custom_commands: next }, `saved shortcut “${entry.phrase}”`);
      setAliasPhrase("");
      setAliasTarget("");
      await loadCommands();
    },
    [currentAliases, update, loadCommands],
  );

  const deleteAlias = useCallback(
    async (phrase: string) => {
      const next = currentAliases().filter(
        (a) => a.phrase.toLowerCase() !== phrase.toLowerCase(),
      );
      await update({ custom_commands: next }, `deleted shortcut “${phrase}”`);
      await loadCommands();
    },
    [currentAliases, update, loadCommands],
  );

  // Wake sub-editors save as one merged wake:{...} PUT, clamped to the
  // backend ranges (settings.py::update_voice_edit). Partial merge is safe:
  // the backend deep-merges the wake object.
  const saveWake = useCallback(() => {
    const silence = Math.round(
      clampVoiceEditNumber(
        Number(wakeSilence), WAKE_LIMITS.silenceMs.min, WAKE_LIMITS.silenceMs.max,
      ),
    );
    const rms = clampVoiceEditNumber(
      Number(wakeRms), WAKE_LIMITS.rmsGate.min, WAKE_LIMITS.rmsGate.max,
    );
    const kws = clampVoiceEditNumber(
      Number(wakeKws), WAKE_LIMITS.kwsThreshold.min, WAKE_LIMITS.kwsThreshold.max,
    );
    const eng = wakeEngine === "kws" || wakeEngine === "asr" ? wakeEngine : "auto";
    const phrase = wakePhrase.trim().slice(0, 60) || "hey qubit";
    const asrMaxS = clampVoiceEditNumber(
      Number(wakeAsrFallbackMaxS),
      WAKE_LIMITS.asrFallbackMaxS.min, WAKE_LIMITS.asrFallbackMaxS.max,
    );
    const maxS = clampVoiceEditNumber(
      Number(wakeMaxS),
      WAKE_LIMITS.maxS.min, WAKE_LIMITS.maxS.max,
    );
    const vad = wakeVad === "silero" || wakeVad === "webrtc" || wakeVad === "energy" ? wakeVad : "auto";
    setWakeSilence(String(silence));
    setWakeRms(String(rms));
    setWakeKws(String(kws));
    setWakeEngine(eng);
    setWakePhrase(phrase);
    setWakeAsrFallbackMaxS(String(asrMaxS));
    setWakeMaxS(String(maxS));
    setWakeVad(vad);
    return update(
      {
        wake: {
          ...(settings?.wake?.enabled !== undefined
            ? { enabled: settings.wake.enabled }
            : {}),
          phrase,
          silence_ms: silence,
          rms_gate: rms,
          engine: eng,
          kws_threshold: kws,
          asr_fallback: wakeAsrFallback,
          asr_fallback_max_s: asrMaxS,
          max_s: maxS,
          vad,
        },
      },
      "wake settings saved",
    );
  }, [update, settings?.wake?.enabled, wakePhrase, wakeSilence, wakeRms, wakeEngine, wakeKws, wakeAsrFallback, wakeAsrFallbackMaxS, wakeMaxS, wakeVad]);

  const currentWakeWords = useCallback(
    (): string[] => settings?.wake_words ?? [],
    [settings?.wake_words],
  );

  const currentWakeRoutes = useCallback(
    (): WakeRoute[] => settings?.wake_routing?.routes ?? [],
    [settings?.wake_routing],
  );

  // Reconcile the live wake words onto the backend's factory defaults: add the
  // missing defaults, remove the extras. Composed from the single-item
  // add/remove PUT fields; each step adopts the server echo (no optimistic
  // state) so a mid-way failure leaves the earlier steps honestly applied.
  const resetWakeWords = useCallback(async () => {
    const current = currentWakeWords();
    const defaults = settings?.wake_words_default ?? [];
    const have = new Set(current.map((w) => w.toLowerCase()));
    const wanted = new Set(defaults.map((w) => w.toLowerCase()));
    const toAdd = defaults.filter((w) => !have.has(w.toLowerCase()));
    const toRemove = current.filter((w) => !wanted.has(w.toLowerCase()));
    if (toAdd.length === 0 && toRemove.length === 0) {
      setStatus("wake words already at defaults");
      return;
    }
    setBusy(true);
    try {
      for (const word of toAdd) {
        const out = await voiceEdit.saveSettings({ wake_words_add: word });
        setSettings(out.settings);
      }
      for (const word of toRemove) {
        const out = await voiceEdit.saveSettings({ wake_words_remove: word });
        setSettings(out.settings);
      }
      setStatus("wake words reset to defaults");
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [currentWakeWords, settings?.wake_words_default]);

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
            <div className="mt-3 grid gap-3 sm:grid-cols-2" data-testid="voice-edit-wake-settings">
              <label className="text-sm">
                Wake phrase
                <input
                  className="mt-1 w-full rounded border border-border bg-background p-1"
                  data-testid="voice-edit-wake-phrase"
                  placeholder="hey qubit"
                  value={wakePhrase}
                  onChange={(e) => setWakePhrase(e.target.value)}
                />
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  Spoken phrase that triggers a command (max 60 chars).
                </span>
              </label>
              <label className="text-sm">
                Wake engine
                <select
                  className="mt-1 w-full rounded border border-border bg-background p-1"
                  data-testid="voice-edit-wake-engine"
                  value={wakeEngine}
                  onChange={(e) => setWakeEngine(e.target.value)}
                >
                  <option value="auto">auto</option>
                  <option value="kws">kws (keyword spotter)</option>
                  <option value="asr">asr (transcript match)</option>
                </select>
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  Auto picks the spotter when its model bundle is present.
                </span>
              </label>
              <label className="text-sm">
                End-of-speech silence (ms)
                <input
                  type="number"
                  min={WAKE_LIMITS.silenceMs.min}
                  max={WAKE_LIMITS.silenceMs.max}
                  step="50"
                  className="mt-1 w-full rounded border border-border bg-background p-1"
                  data-testid="voice-edit-wake-silence"
                  value={wakeSilence}
                  onChange={(e) => setWakeSilence(e.target.value)}
                />
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  Silence that ends the wake segment (300–5000).
                </span>
              </label>
              <label className="text-sm">
                Voice floor (RMS gate)
                <input
                  type="number"
                  min={WAKE_LIMITS.rmsGate.min}
                  max={WAKE_LIMITS.rmsGate.max}
                  step="0.0001"
                  className="mt-1 w-full rounded border border-border bg-background p-1"
                  data-testid="voice-edit-wake-rms"
                  value={wakeRms}
                  onChange={(e) => setWakeRms(e.target.value)}
                />
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  Frames quieter than this count as silence (0.0005–0.2).
                </span>
              </label>
              <label className="text-sm">
                Spotter threshold
                <input
                  type="number"
                  min={WAKE_LIMITS.kwsThreshold.min}
                  max={WAKE_LIMITS.kwsThreshold.max}
                  step="0.01"
                  className="mt-1 w-full rounded border border-border bg-background p-1"
                  data-testid="voice-edit-wake-kws"
                  value={wakeKws}
                  onChange={(e) => setWakeKws(e.target.value)}
                />
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  Lower hears more (0.05–0.9).
                  {settings?.wake?.kws_available === false
                    ? " No local spotter bundle — auto falls back to transcript match."
                    : null}
                </span>
              </label>
              <label className="text-sm">
                ASR fallback for spotter misses
                <input
                  type="checkbox"
                  className="mt-1 block"
                  data-testid="voice-edit-wake-asr-fallback"
                  checked={wakeAsrFallback}
                  onChange={(e) => setWakeAsrFallback(e.target.checked)}
                />
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  When the spotter misses, transcribe short utterances once
                  and match the wake phrase (local only).
                </span>
              </label>
              <label className="text-sm">
                Fallback max length (s)
                <input
                  type="number"
                  min={WAKE_LIMITS.asrFallbackMaxS.min}
                  max={WAKE_LIMITS.asrFallbackMaxS.max}
                  step="1"
                  className="mt-1 w-full rounded border border-border bg-background p-1"
                  data-testid="voice-edit-wake-asr-fallback-max-s"
                  value={wakeAsrFallbackMaxS}
                  onChange={(e) => setWakeAsrFallbackMaxS(e.target.value)}
                />
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  Only utterances this long get a fallback probe (2–25).
                </span>
              </label>
              <label className="text-sm">
                Segmentation (VAD)
                <select
                  className="mt-1 w-full rounded border border-border bg-background p-1"
                  data-testid="voice-edit-wake-vad"
                  value={wakeVad}
                  onChange={(e) => setWakeVad(e.target.value)}
                >
                  <option value="auto">auto (neural when available)</option>
                  <option value="silero">silero (neural)</option>
                  <option value="webrtc">webrtc</option>
                  <option value="energy">energy (legacy gate)</option>
                </select>
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  Neural segmentation ignores fan/machine drone; energy hears it.
                </span>
              </label>
              <label className="text-sm">
                Longest segment (s)
                <input
                  type="number"
                  min={WAKE_LIMITS.maxS.min}
                  max={WAKE_LIMITS.maxS.max}
                  step="1"
                  className="mt-1 w-full rounded border border-border bg-background p-1"
                  data-testid="voice-edit-wake-max-s"
                  value={wakeMaxS}
                  onChange={(e) => setWakeMaxS(e.target.value)}
                />
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  Continuously-voiced audio is cut here so every segment fits a probe (2–25).
                </span>
              </label>
            </div>
            <button
              type="button"
              className="mt-2 rounded border border-border px-3 py-1 text-xs"
              data-testid="voice-edit-wake-save"
              disabled={busy}
              onClick={() => void saveWake()}
            >
              Save wake settings
            </button>
            <p className="mt-1 text-xs text-muted-foreground">
              Wake-word changes take effect after the desktop helper restarts.
            </p>
            <div className="mt-4" data-testid="voice-edit-wake-words">
              <h3 className="text-sm font-semibold">Wake words</h3>
              <p className="mb-2 mt-1 text-xs text-muted-foreground">
                Extra words or phrases that also start hands-free listening,
                alongside the wake phrase above.
              </p>
              {currentWakeWords().length === 0 ? (
                <p
                  className="mb-2 text-xs text-muted-foreground"
                  data-testid="voice-edit-wake-words-empty"
                >
                  No wake words configured.
                </p>
              ) : (
                <ul className="mb-2 flex flex-col gap-1">
                  {currentWakeWords().map((word) => (
                    <li
                      key={word}
                      className="flex items-center gap-2 text-sm"
                      data-testid={`wake-word-row-${word}`}
                    >
                      <code className="rounded bg-muted px-1">{word}</code>
                      <button
                        type="button"
                        className="rounded border border-border px-2 py-0.5 text-xs"
                        aria-label={`Remove wake word ${word}`}
                        data-testid={`wake-word-remove-${word}`}
                        disabled={busy}
                        onClick={() =>
                          void update(
                            { wake_words_remove: word },
                            `removed wake word “${word}”`,
                          )
                        }
                      >
                        ×
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              <div className="flex flex-wrap items-end gap-2">
                <label className="text-sm">
                  Add a wake word
                  <input
                    className="mt-1 w-44 rounded border border-border bg-background p-1"
                    data-testid="wake-word-input"
                    placeholder="e.g. computer"
                    value={wakeWordDraft}
                    onChange={(e) => setWakeWordDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && wakeWordDraft.trim() && !busy) {
                        const word = wakeWordDraft.trim();
                        setWakeWordDraft("");
                        void update({ wake_words_add: word }, `added wake word “${word}”`);
                      }
                    }}
                  />
                </label>
                <button
                  type="button"
                  className="rounded border border-border px-3 py-1 text-xs"
                  data-testid="wake-word-add"
                  disabled={busy || !wakeWordDraft.trim()}
                  onClick={() => {
                    const word = wakeWordDraft.trim();
                    if (!word) return;
                    setWakeWordDraft("");
                    void update({ wake_words_add: word }, `added wake word “${word}”`);
                  }}
                >
                  Add
                </button>
                <button
                  type="button"
                  className="rounded border border-border px-3 py-1 text-xs"
                  data-testid="wake-words-reset"
                  disabled={busy}
                  onClick={() => void resetWakeWords()}
                >
                  Reset to defaults
                </button>
              </div>
            </div>
            <div className="mt-4" data-testid="voice-edit-wake-routing">
              <h3 className="text-sm font-semibold">Wake routing</h3>
              <p className="mb-2 mt-1 text-xs text-muted-foreground">
                Send a spoken trigger to a specific target. “current” follows the
                configured default; the other modes name an agent or session.
              </p>
              {currentWakeRoutes().length === 0 ? (
                <p
                  className="mb-2 text-xs text-muted-foreground"
                  data-testid="voice-edit-wake-routing-empty"
                >
                  No wake routes configured.
                </p>
              ) : (
                <ul className="mb-2 flex flex-col gap-1">
                  {currentWakeRoutes().map((route, i) => (
                    <li
                      key={`${route.trigger}-${i}`}
                      className="flex items-center gap-2 text-sm"
                      data-testid={`wake-route-row-${i}`}
                    >
                      <code className="rounded bg-muted px-1">{route.trigger}</code>
                      <span className="text-muted-foreground">→ {route.target}</span>
                      <button
                        type="button"
                        className="rounded border border-border px-2 py-0.5 text-xs"
                        aria-label={`Remove route ${route.trigger}`}
                        data-testid={`wake-route-remove-${route.trigger}`}
                        disabled={busy}
                        onClick={() =>
                          void update(
                            { wake_route_remove: route.trigger },
                            `removed route “${route.trigger}”`,
                          )
                        }
                      >
                        ×
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              <div className="flex flex-wrap items-end gap-2">
                <label className="text-sm">
                  Trigger
                  <input
                    className="mt-1 w-40 rounded border border-border bg-background p-1"
                    data-testid="wake-route-trigger"
                    placeholder="e.g. hey qubit"
                    value={routeTrigger}
                    onChange={(e) => setRouteTrigger(e.target.value)}
                  />
                </label>
                <label className="text-sm">
                  Target
                  <select
                    className="mt-1 rounded border border-border bg-background p-1"
                    data-testid="wake-route-target-mode"
                    value={routeMode}
                    onChange={(e) => setRouteMode(e.target.value)}
                  >
                    <option value="current">current</option>
                    <option value="agentId">agent id</option>
                    <option value="sessionKey">session key</option>
                  </select>
                </label>
                {routeMode !== "current" ? (
                  <label className="text-sm">
                    Value
                    <input
                      className="mt-1 w-40 rounded border border-border bg-background p-1"
                      data-testid="wake-route-target-value"
                      placeholder={routeMode === "agentId" ? "agent id" : "session key"}
                      value={routeValue}
                      onChange={(e) => setRouteValue(e.target.value)}
                    />
                  </label>
                ) : null}
                <button
                  type="button"
                  className="rounded border border-border px-3 py-1 text-xs"
                  data-testid="wake-route-add"
                  disabled={
                    busy ||
                    !routeTrigger.trim() ||
                    (routeMode !== "current" && !routeValue.trim())
                  }
                  onClick={() => {
                    const trigger = routeTrigger.trim();
                    if (!trigger) return;
                    setRouteTrigger("");
                    setRouteValue("");
                    void update(
                      {
                        wake_route_add: {
                          trigger,
                          target: encodeRouteTarget(routeMode, routeValue),
                        },
                      },
                      `added route “${trigger}”`,
                    );
                  }}
                >
                  Add route
                </button>
              </div>
            </div>
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
                  data-testid="voice-edit-local-profile"
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
                  Hold to talk, or press once to start and stop. Takes effect
                  after the helper restarts.
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
                  checked={settings.noise_suppression === true}
                  onChange={(e) =>
                    void update({ noise_suppression: e.target.checked })
                  }
                />
                Noise suppression (applies on next helper start)
              </label>
            </div>
          </section>

          <section
            className="rounded-lg border border-border p-4"
            data-testid="voice-edit-per-app"
          >
            <h2 className="text-sm font-semibold">Per-app actions</h2>
            <p className="mb-3 mt-1 text-xs text-muted-foreground">
              Pin one transform action per application (matched on the focused
              window class, lower-cased). An empty pin is inert — the spoken
              command decides. Deleting a row clears its pin.
            </p>
            {Object.keys(settings.per_app ?? {}).filter((k) => (settings.per_app ?? {})[k]).length === 0 ? (
              <p className="mb-2 text-xs text-muted-foreground" data-testid="voice-edit-per-app-empty">
                No per-app pins.
              </p>
            ) : (
              <ul className="mb-2 flex flex-col gap-1">
                {Object.entries(settings.per_app ?? {})
                  .filter(([, v]) => v)
                  .map(([app, action]) => (
                    <li
                      key={app}
                      className="flex items-center gap-2 text-sm"
                      data-testid={`voice-edit-per-app-row-${app}`}
                    >
                      <code className="rounded bg-muted px-1">{app}</code>
                      <span className="text-muted-foreground">→ {action}</span>
                      <button
                        type="button"
                        className="rounded border border-border px-2 py-0.5 text-xs"
                        data-testid={`voice-edit-per-app-delete-${app}`}
                        disabled={busy}
                        onClick={() =>
                          void update(
                            { per_app: { ...(settings.per_app ?? {}), [app]: "" } },
                            `cleared pin for ${app}`,
                          )
                        }
                      >
                        Delete
                      </button>
                    </li>
                  ))}
              </ul>
            )}
            <div className="flex flex-wrap items-end gap-2">
              <label className="text-sm">
                App class
                <input
                  className="mt-1 w-40 rounded border border-border bg-background p-1"
                  data-testid="voice-edit-per-app-new-app"
                  placeholder="e.g. firefox"
                  value={newApp}
                  onChange={(e) => setNewApp(e.target.value)}
                />
              </label>
              <label className="text-sm">
                Action
                <select
                  className="mt-1 rounded border border-border bg-background p-1"
                  data-testid="voice-edit-per-app-new-action"
                  value={newAction}
                  onChange={(e) => setNewAction(e.target.value)}
                >
                  {VOICE_EDIT_ACTIONS.map((a) => (
                    <option key={a} value={a}>
                      {a}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                className="rounded border border-border px-3 py-1 text-xs"
                data-testid="voice-edit-per-app-add"
                disabled={busy || !newApp.trim()}
                onClick={() => {
                  const app = newApp.trim().toLowerCase();
                  if (!app) return;
                  setNewApp("");
                  void update(
                    { per_app: { ...(settings.per_app ?? {}), [app]: newAction } },
                    `pinned ${app} to ${newAction}`,
                  );
                }}
              >
                Add pin
              </button>
              <button
                type="button"
                className="rounded border border-border px-3 py-1 text-xs"
                data-testid="voice-edit-per-app-clear"
                disabled={busy}
                onClick={() => void update({ per_app: {} }, "cleared all per-app pins")}
              >
                Clear all
              </button>
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
                  <option value="auto">Auto (default source)</option>
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
                  data-testid="voice-edit-verbatim"
                  checked={Boolean(settings.verbatim_transcript)}
                  onChange={(e) =>
                    void update({ verbatim_transcript: e.target.checked })
                  }
                />
                Verbatim transcripts (lowercase, no punctuation — exactly what was heard)
              </label>
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
                data-testid="voice-edit-waveform-ingame"
                checked={settings.waveform_ingame === true}
                onChange={(e) => void update({ waveform_ingame: e.target.checked })}
              />
              Show orb over fullscreen games
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

          <details
            className="rounded-lg border border-border p-4"
            data-testid="voice-edit-advanced"
          >
            <summary className="cursor-pointer text-sm font-semibold">
              Advanced
            </summary>
            <p className="mb-3 mt-1 text-xs text-muted-foreground">
              Timing and behavior knobs. Values clamp to the backend ranges on
              save. Helper-connection endpoints below name helper
              infrastructure — blank uses the built-in default; changes take
              effect after the helper restarts.
            </p>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="text-sm">
                Request lifetime (s)
                <input
                  type="number"
                  min="30"
                  max="3600"
                  step="1"
                  className="mt-1 w-full rounded border border-border bg-background p-1"
                  data-testid="voice-edit-request_ttl_s"
                  value={settings.request_ttl_s ?? 300}
                  onChange={(e) =>
                    void update({
                      request_ttl_s: clampVoiceEditNumber(Number(e.target.value), 30, 3600),
                    })
                  }
                />
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  How long a voice request stays valid (30–3600).
                </span>
              </label>
              <label className="text-sm">
                Brain timeout (s)
                <input
                  type="number"
                  min="1"
                  max="60"
                  step="0.5"
                  className="mt-1 w-full rounded border border-border bg-background p-1"
                  data-testid="voice-edit-brain_timeout_s"
                  value={settings.brain_timeout_s ?? 4}
                  onChange={(e) =>
                    void update({ brain_timeout_s: Number(e.target.value) })
                  }
                />
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  How long the brain router may take to disambiguate (default 4).
                </span>
              </label>
              <label className="text-sm">
                Release tail (ms)
                <input
                  type="number"
                  min="0"
                  max="2000"
                  step="50"
                  className="mt-1 w-full rounded border border-border bg-background p-1"
                  data-testid="voice-edit-release_tail_ms"
                  value={settings.release_tail_ms ?? 500}
                  onChange={(e) =>
                    void update({
                      release_tail_ms: Math.round(
                        clampVoiceEditNumber(Number(e.target.value), 0, 2000),
                      ),
                    })
                  }
                />
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  Mic stream kept open after key release (0–2000).
                </span>
              </label>
              <label className="text-sm">
                Mic target peak
                <input
                  type="number"
                  min="0.05"
                  max="0.95"
                  step="0.05"
                  className="mt-1 w-full rounded border border-border bg-background p-1"
                  data-testid="voice-edit-mic_target_peak"
                  value={settings.mic_target_peak ?? 0.5}
                  onChange={(e) =>
                    void update({
                      mic_target_peak: clampVoiceEditNumber(Number(e.target.value), 0.05, 0.95),
                    })
                  }
                />
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  Capture level target (0.05–0.95).
                </span>
              </label>
              <label className="text-sm">
                STT engine override
                <input
                  className="mt-1 w-full rounded border border-border bg-background p-1"
                  data-testid="voice-edit-stt_engine"
                  placeholder="blank = configured default"
                  value={sttEngine}
                  onChange={(e) => setSttEngine(e.target.value)}
                  onBlur={() => void update({ stt_engine: sttEngine })}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      void update({ stt_engine: sttEngine });
                    }
                  }}
                />
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  Engine name override; blank uses the configured default.
                </span>
              </label>
              <label className="text-sm">
                Default action
                <select
                  className="mt-1 w-full rounded border border-border bg-background p-1"
                  data-testid="voice-edit-default_prompt"
                  value={settings.default_prompt ?? ""}
                  onChange={(e) => void update({ default_prompt: e.target.value })}
                >
                  {!(settings.default_prompt ?? "") ? (
                    <option value="">—</option>
                  ) : null}
                  {VOICE_EDIT_ACTIONS.map((a) => (
                    <option key={a} value={a}>
                      {a}
                    </option>
                  ))}
                </select>
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  Fallback action when no command is recognized.
                </span>
              </label>
              <label className="text-sm">
                Helper service URL
                <input
                  className="mt-1 w-full rounded border border-border bg-background p-1"
                  data-testid="voice-edit-service-url"
                  placeholder="blank = built-in default"
                  value={serviceUrl}
                  onChange={(e) => setServiceUrl(e.target.value)}
                  onBlur={() => void update({ service_url: serviceUrl })}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      void update({ service_url: serviceUrl });
                    }
                  }}
                />
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  Helper-connection endpoint for the desktop helper; blank uses
                  the built-in default. Takes effect after the helper restarts.
                </span>
              </label>
              <label className="text-sm">
                Helper STT socket URL
                <input
                  className="mt-1 w-full rounded border border-border bg-background p-1"
                  data-testid="voice-edit-stt-ws-url"
                  placeholder="blank = built-in default"
                  value={sttWsUrl}
                  onChange={(e) => setSttWsUrl(e.target.value)}
                  onBlur={() => void update({ stt_ws_url: sttWsUrl })}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      void update({ stt_ws_url: sttWsUrl });
                    }
                  }}
                />
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  Helper-connection endpoint for streaming STT; blank uses the
                  built-in default. Takes effect after the helper restarts.
                </span>
              </label>
            </div>
            <div className="mt-3 flex flex-col gap-2 text-sm">
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  data-testid="voice-edit-disable_key_repeat"
                  checked={Boolean(settings.disable_key_repeat)}
                  onChange={(e) =>
                    void update({ disable_key_repeat: e.target.checked })
                  }
                />
                Ignore OS key-repeat while Right-Ctrl is held
              </label>
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  data-testid="voice-edit-text_commands"
                  checked={Boolean(settings.text_commands)}
                  onChange={(e) =>
                    void update({ text_commands: e.target.checked })
                  }
                />
                Treat bare speech without a selection as dictation
              </label>
            </div>
          </details>

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
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    const out = await voiceEdit.restore();
                    setSettings(out.settings);
                    setStatus("defaults restored");
                    setError("");
                  } catch (e) {
                    setError(e instanceof Error ? e.message : String(e));
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                Restore all defaults
              </button>
            </div>
          </section>

          <VoiceCommandsSection
            catalog={catalog}
            busy={busy}
            onAdd={(entry) => void addAlias(entry)}
            onDelete={(phrase) => void deleteAlias(phrase)}
            aliasPhrase={aliasPhrase}
            setAliasPhrase={setAliasPhrase}
            aliasAction={aliasAction}
            setAliasAction={setAliasAction}
            aliasTarget={aliasTarget}
            setAliasTarget={setAliasTarget}
          />

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
