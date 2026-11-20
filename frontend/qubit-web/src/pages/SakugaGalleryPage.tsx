/**
 * /sakuga-gallery — the live component gallery for the vendored Sakuga VFX kit.
 *
 * Hidden route (no sidebar row). It exists so every upstream control and every
 * Qubit-only extra can be exercised in the real built dashboard, against the
 * same `/sakuga-vfx/*.js` files the product uses — not a screenshot and not a
 * test double. The standalone copy of the upstream gallery (with the vendored
 * scripts loaded directly) lives at `/sakuga-gallery.html`.
 *
 * The wrappers live in `@qubit/shared/sakuga`; this page adds no behaviour to
 * them. It only wires demos, prints the real value/change each control emits,
 * and keeps the upstream white-paper palette (see `sakuga/gallery.css`).
 */
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  SettingsPage,
  SettingsPageHeader,
} from "@/components/console-ui";
import {
  Button,
  Checkbox,
  DateField,
  FileField,
  FormRow,
  Input,
  ListItem,
  MultiSelect,
  NumberField,
  Pagination,
  Progress,
  Radio,
  Rating,
  SakugaLoading,
  Select,
  Slider,
  Tabs,
  Textarea,
  TimeField,
  Toggle,
  Tooltip,
  HoverTooltip,
  emitFx,
  fireFx,
  loadSakugaComponent,
  showSakugaToast,
  type FxCue,
} from "@/sakuga/ui";
import "@/sakuga/gallery.css";

interface DemoCardProps {
  title: string;
  tag: string;
  /** What the control does upstream. */
  how: string;
  /** How Qubit uses it (one line). */
  qubit: ReactNode;
  children: ReactNode;
  said?: ReactNode;
  actions?: ReactNode;
  wide?: boolean;
  tall?: boolean;
  start?: boolean;
}

function DemoCard({
  title,
  tag,
  how,
  qubit,
  children,
  said,
  actions,
  wide,
  tall,
  start,
}: DemoCardProps) {
  return (
    <section className={`skg-card${wide ? " skg-card--wide" : ""}`}>
      <div className="skg-card__head">
        <h3 className="skg-card__title">
          {title} <span className="skg-card__tag">{tag}</span>
        </h3>
        <p className="skg-card__note">{how}</p>
        <p className="skg-card__note">
          <strong>In Qubit:</strong> {qubit}
        </p>
      </div>
      <div
        className={[
          "skg-card__stage",
          tall ? "skg-card__stage--tall" : "",
          start ? "skg-card__stage--start" : "",
        ]
          .filter(Boolean)
          .join(" ")}
      >
        {children}
      </div>
      <p className="skg-card__said" aria-live="polite">
        {said}
      </p>
      {actions ? <div className="skg-card__acts">{actions}</div> : null}
    </section>
  );
}

/** The raw `<sakuga-toast>` host, mounted and driven imperatively. */
function ToastStage({
  onReady,
}: {
  onReady: (el: HTMLElement | null) => void;
}) {
  const host = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    void loadSakugaComponent("toast").catch(() => {});
    const el = document.createElement("sakuga-toast");
    el.setAttribute("duration", "4");
    host.current?.append(el);
    onReady(el);
    return () => {
      onReady(null);
      el.remove();
    };
  }, [onReady]);
  return <div className="skg-toast-stage" ref={host} />;
}

type ToastApi = { show?: (text?: string) => void; hide?: () => void; open?: boolean };

function ButtonDemos() {
  const [said, setSaid] = useState("Click either button.");
  const primaryWrap = useRef<HTMLDivElement | null>(null);
  const doneTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (doneTimer.current) clearTimeout(doneTimer.current);
  }, []);

  const callDone = useCallback(() => {
    const el = primaryWrap.current?.querySelector("sakuga-button") as
      | (HTMLElement & { done?: () => void })
      | null;
    el?.done?.();
    setSaid("done() called — check lands for 1.8s.");
  }, []);

  return (
    <>
      <DemoCard
        title="Primary button"
        tag="<sakuga-button>"
        how="Press and hold: it squashes flat. Release: an impact frame, a burst, and ドン!; done() lands a pink check."
        qubit="The default action control. Call done() (or done on the host) only after the action actually succeeded."
        said={said}
        actions={
          <>
            <button type="button" className="skg-pill" onClick={callDone}>
              Call done() from code
            </button>
          </>
        }
      >
        <div ref={primaryWrap} style={{ display: "contents" }}>
          <Button
            fx={false}
            onClick={() => {
              setSaid("click — reaction plays; done() lands the check.");
              if (doneTimer.current) clearTimeout(doneTimer.current);
              doneTimer.current = setTimeout(callDone, 760);
            }}
          >
            Confirm
          </Button>
        </div>
      </DemoCard>

      <DemoCard
        title="Secondary button"
        tag="<sakuga-button variant=secondary>"
        how="A small squash and three wedges; no impact frame, no burst."
        qubit="Quiet, non-destructive actions: cancel, back, refresh, open."
        said="Click it."
      >
        <Button variant="secondary" fx={false} onClick={() => setSaid("secondary click")}>
          Cancel
        </Button>
      </DemoCard>
    </>
  );
}

function BooleanDemos() {
  const [toggleOn, setToggleOn] = useState(false);
  const [toggleOff, setToggleOff] = useState(true);
  const [checked, setChecked] = useState(false);
  return (
    <>
      <DemoCard
        title="Toggle"
        tag="<sakuga-toggle>"
        how="43 painted frames on: the knob smears over, slams, splashes, and the track floods. Off plays a 12-frame dash back."
        qubit="Boolean settings (notifications, auto-approve). The wrapper forwards checked/name/value into forms."
        said={`off toggle: ${toggleOn ? "on" : "off"} · on toggle: ${toggleOff ? "on" : "off"}`}
      >
        <div className="skg-field">
          <span>Starts off</span>
          <Toggle label="Off by default" checked={toggleOn} onChange={setToggleOn} />
        </div>
        <div className="skg-field">
          <span>Starts on</span>
          <Toggle label="On by default" checked={toggleOff} onChange={setToggleOff} />
        </div>
      </DemoCard>

      <DemoCard
        title="Checkbox"
        tag="<sakuga-checkbox>"
        how="Crescents swing round the box, crystals fly, the tick draws itself, ズザッ in dotted letters. Unticking is a small puff."
        qubit="Opt-ins (remember me, accept terms). The 38px white/ink box is unchanged from upstream."
        said={`checked: ${checked ? "yes" : "no"}`}
      >
        <Checkbox checked={checked} onChange={setChecked}>
          Remember me
        </Checkbox>
      </DemoCard>
    </>
  );
}


function StatesDemo() {
  return (
    <DemoCard
      title="Disabled states"
      tag="disabled"
      how="Upstream dims and blocks a disabled control; the shadow root keeps the native disabled attribute, so keyboard, pointer and form submission all refuse it."
      qubit="Busy or permission-gated actions. `disabled` reaches the element before it is connected, so no control is briefly live."
      said="Every control here is inert."
      tall
      start
    >
      <div className="skg-field">
        <span>Button</span>
        <Button disabled fx={false}>Disabled</Button>
      </div>
      <div className="skg-field">
        <span>Toggle</span>
        <Toggle label="Disabled toggle" checked={false} disabled onChange={() => {}} />
      </div>
      <div className="skg-field">
        <span>Input</span>
        <Input label="Disabled field" placeholder="Read-only" disabled value="" onChange={() => {}} />
      </div>
    </DemoCard>
  );
}

function ValueDemos() {
  const [slider, setSlider] = useState(30);
  const [input, setInput] = useState("");
  const [radio, setRadio] = useState("a");
  const [rating, setRating] = useState(0);
  return (
    <>
      <DemoCard
        title="Slider"
        tag="<sakuga-slider>"
        how="Drag fast: cumulus rolls up behind the thumb and シュッ; release while it moves and a crescent flies ahead."
        qubit="Numeric ranges (volume, thresholds). Fires change; name puts the value in the form."
        said={`value: ${slider}`}
      >
        <Slider label="Volume" value={slider} onChange={setSlider} unit="%" />
      </DemoCard>

      <DemoCard
        title="Text input"
        tag="<sakuga-input>"
        how="Every key kicks dust from the caret; focus throws three wedges off the corner."
        qubit="Single-line text fields (names, paths, search, filters). Validation hints are mirrored onto the inner input."
        said={`value: ${input || "(empty)"}`}
      >
        <Input
          label="Name"
          placeholder="Type something"
          value={input}
          onChange={setInput}
        />
      </DemoCard>

      <DemoCard
        title="Radio"
        tag="<sakuga-radio>"
        how="The picked dot springs in with a fan of wedges, two puffs and a hollow ポン. Arrow keys move between options."
        qubit="Small exclusive choices (provider, mode) where a dropdown is overkill."
        said={`value: ${radio}`}
      >
        <Radio
          label="Plan"
          value={radio}
          options={[
            { value: "a", label: "Option A" },
            { value: "b", label: "Option B" },
            { value: "c", label: "Option C" },
          ]}
          onChange={setRadio}
        />
      </DemoCard>

      <DemoCard
        title="Rating"
        tag="<sakuga-rating>"
        how="Stars pop in one after another; five gets a burst behind the last star. Clicking the lit star clears it."
        qubit="Quick quality/priority scoring. Read value (0–5); 0 means unrated."
        said={`value: ${rating} ${rating === 0 ? "(cleared)" : ""}`}
      >
        <Rating label="Rating" value={rating} onChange={setRating} />
      </DemoCard>
    </>
  );
}

function TabsDemo() {
  const [tab, setTab] = useState(0);
  const panels = ["Home panel", "Profile panel", "Settings panel"];
  return (
    <DemoCard
      title="Tabs"
      tag="<sakuga-tabs>"
      how="The raised tab slides, stretches, and lands with wedges and a puff. Arrow keys, Home and End work."
      qubit="Section switches (Config categories, Studio tabs). It only draws the tabs — the panel is yours."
      said={`panel: ${panels[tab]}`}
    >
      <div className="skg-field" style={{ width: "100%", alignItems: "center" }}>
        <Tabs
          label="Sections"
          selected={tab}
          options={[
            { value: "home", label: "Home" },
            { value: "profile", label: "Profile" },
            { value: "settings", label: "Settings" },
          ]}
          onChange={(detail) => setTab(detail.index)}
        />
        <div
          role="tabpanel"
          style={{
            marginTop: 16,
            padding: "10px 16px",
            border: "1.5px solid var(--skg-line, #dcdaf0)",
            borderRadius: 12,
            fontSize: 13,
            fontWeight: 500,
          }}
        >
          {panels[tab]}
        </div>
      </div>
    </DemoCard>
  );
}

function ProgressDemos() {
  const [value, setValue] = useState(0);
  const [done, setDone] = useState(0);
  const [busyDone, setBusyDone] = useState(false);
  return (
    <>
      <DemoCard
        title="Progress"
        tag="<sakuga-progress>"
        how="A storm cloud rides the edge, pebbles lift and ゴゴゴゴ rumbles; reaching 1.0 fires sakuga:done with a burst and a check."
        qubit="Long jobs with real numbers. Call el.set(n); listen for sakuga:done, then reset(). Never ship the demo attribute."
        said={`value: ${value.toFixed(2)} · sakuga:done fired: ${done}`}
        wide
        tall
        actions={
          <>
            {[0, 0.25, 0.5, 0.75, 1].map((next) => (
              <button
                key={next}
                type="button"
                className="skg-pill"
                aria-pressed={next === value}
                onClick={() => setValue(next)}
              >
                set({next})
              </button>
            ))}
          </>
        }
      >
        <Progress
          label="Uploading"
          value={value}
          onDone={() => setDone((n) => n + 1)}
        />
      </DemoCard>

      <DemoCard
        title="SakugaLoading (busy)"
        tag="<SakugaLoading>"
        how="Qubit extra built on the progress bar. With no value it eases toward 90% and stops, so a loading screen never claims completion."
        qubit="Route fallbacks and unknown-length waits (Suspense fallbacks, gateway boot)."
        said={busyDone ? "sakuga:done (should not fire while busy)" : "busy: creeping toward 90%"}
        tall
      >
        <SakugaLoading
          label="Loading jobs"
          onDone={() => setBusyDone(true)}
        />
      </DemoCard>

      <DemoCard
        title="SakugaLoading (determinate)"
        tag="<SakugaLoading value={n}>"
        how="The same primitive with a real value — the caption is Qubit's, the bar and its effects are upstream."
        qubit="Waits that can report progress (uploads, migrations, batch jobs)."
        said="determinate bar at 62%"
        tall
      >
        <SakugaLoading label="Copying files" value={0.62} />
      </DemoCard>
    </>
  );
}

function ToastDemo() {
  const [toast, setToast] = useState<ToastApi | null>(null);
  const [said, setSaid] = useState("Show a toast.");
  const onReady = useCallback((el: HTMLElement | null) => setToast(el as ToastApi | null), []);

  return (
    <DemoCard
      title="Toast"
      tag="<sakuga-toast>"
      how="Streaks in as a smear, lands in dust with ズザッ, then shrinks away after duration (4s here). show() again replaces the message; hide() leaves early."
      qubit="Outcome notifications. The message lives in a status region, so it is announced. One toast at a time."
      said={said}
      actions={
        <>
          <button
            type="button"
            className="skg-pill"
            onClick={() => {
              toast?.show?.("Saved successfully!");
              setSaid("show('Saved successfully!')");
            }}
          >
            show()
          </button>
          <button
            type="button"
            className="skg-pill"
            onClick={() => {
              toast?.show?.("Replaced while visible");
              setSaid("show() again — message replaced, arrival replays");
            }}
          >
            replace
          </button>
          <button
            type="button"
            className="skg-pill"
            onClick={() => {
              toast?.hide?.();
              setSaid("hide()");
            }}
          >
            hide()
          </button>
          <button
            type="button"
            className="skg-pill"
            onClick={() => {
              showSakugaToast("Global toast via showSakugaToast()", { duration: 4 });
              setSaid("showSakugaToast() — fixed bottom-left host");
            }}
          >
            global toast
          </button>
        </>
      }
    >
      <ToastStage onReady={onReady} />
    </DemoCard>
  );
}

function TooltipDemo() {
  return (
    <DemoCard
      title="Tooltip"
      tag="<sakuga-tooltip>"
      how="Hover, focus or tap: the hint springs out like a speech bubble with puffs and a hollow カチッ. Escape closes it."
      qubit="Inline help beside a label. This is a help button, not a hover card — never the only place required information lives."
      said="Hover or focus the ? button."
      tall
    >
      <Tooltip text="We only use this to send your receipt." label="Why we ask" />
      <Tooltip text="Keyboard shortcuts" label="Shortcuts">
        i
      </Tooltip>
    </DemoCard>
  );
}

function HoverTooltipDemo() {
  return (
    <DemoCard
      title="Hover tooltip"
      tag="qsk-hover-tip"
      how="The same paper bubble, attached to any trigger: hover, focus or tap opens it, Escape closes it, and a click lands a small pop."
      qubit="Icon buttons, help labels and dense settings rows where a hover hint is better than a permanent paragraph. The trigger keeps its own element and handler."
      said="Hover the button, then click it."
      tall
    >
      <HoverTooltip label="Hover me" content="This is the upstream bubble attached to a normal Sakuga button.">
        <Button variant="secondary">Hover or click</Button>
      </HoverTooltip>
      <HoverTooltip label="Settings" placement="bottom" content="Hover hints sit above by default; placement='bottom' flips them.">
        <Toggle label="Hover the switch" checked={false} onChange={() => {}} />
      </HoverTooltip>
    </DemoCard>
  );
}

function SelectDemo() {
  const [value, setValue] = useState("openai");
  const [rows, setRows] = useState("sessions");
  return (
    <>
      <DemoCard
        title="SakugaSelect"
        tag="<SakugaSelect>"
        how="Qubit extra: a combobox trigger plus a panel with a filtering SakugaInput and a SakugaRadio list. Escape closes; the hidden input carries the form value."
        qubit="Every long `<select>` (models, providers, profiles, cron expressions). Upstream ships no select, so this composes two of its controls."
        said={`value: ${value}`}
      >
        <Select
          label="Provider"
          name="provider"
          value={value}
          options={[
            { value: "openai", label: "OpenAI" },
            { value: "anthropic", label: "Anthropic" },
            { value: "opencode-go", label: "opencode-go" },
            { value: "ollama", label: "Ollama (local)" },
            { value: "omniroute", label: "OmniRoute (fallback)" },
          ]}
          onChange={setValue}
        />
      </DemoCard>

      <DemoCard
        title="ListItem rows"
        tag="<ListItem>"
        how="Qubit extra: the Sakuga Button, flattened by the shared stylesheet into a dense list row. The press reaction and effect stay; the chrome does not."
        qubit="Sidebar and picker rows (sessions, sections, model lists). active sets aria-current and the row highlight."
        said={`row: ${[
          { id: "sessions", label: "Sessions" },
          { id: "files", label: "Files" },
          { id: "models", label: "Models" },
        ].find((row) => row.id === rows)?.label ?? rows}`}
      >
        <div className="skg-rows" role="list">
          <p className="skg-rows__title">Recent</p>
          {[
            { id: "sessions", label: "Sessions" },
            { id: "files", label: "Files" },
            { id: "models", label: "Models" },
          ].map((row) => (
            <ListItem
              key={row.id}
              role="listitem"
              active={rows === row.id}
              onClick={() => setRows(row.id)}
            >
              {row.label}
            </ListItem>
          ))}
        </div>
      </DemoCard>
    </>
  );
}

function TextareaDemo() {
  const [notes, setNotes] = useState("");
  return (
    <DemoCard
      title="SakugaTextarea"
      tag="<SakugaTextarea>"
      how="Qubit extra: a real native textarea in the Sakuga frame, with the engine's dust on input and wedges on focus. No upstream multi-line control exists."
      qubit="Long text (prompts, notes, skill bodies, descriptions). value/name, required, maxLength and form submission are native."
      said={`value: ${notes.length} chars`}
      tall
    >
      <Textarea
        label="Notes"
        name="notes"
        placeholder="Write a few lines…"
        rows={3}
        value={notes}
        onChange={setNotes}
      />
    </DemoCard>
  );
}

function NumberDemo() {
  const [retries, setRetries] = useState(2);
  return (
    <DemoCard
      title="SakugaNumber"
      tag="<SakugaNumber>"
      how="Qubit extra: a native number input with the spinner hidden and two compact Sakuga steppers. onChange reports a number, or the raw string while the field is empty."
      qubit="Numeric settings (retries, thresholds, row limits). min/max/step and form submission are native."
      said={`value: ${retries}`}
    >
      <NumberField
        label="Retries"
        name="retries"
        min={0}
        max={10}
        step={1}
        value={retries}
        onChange={(value) => setRetries(typeof value === "number" ? value : 0)}
      />
    </DemoCard>
  );
}

function DateTimeDemo() {
  const [date, setDate] = useState("2026-11-18");
  const [time, setTime] = useState("09:30");
  return (
    <DemoCard
      title="SakugaDate / SakugaTime"
      tag="<SakugaDate> <SakugaTime>"
      how="Qubit extras: native date and time inputs in the Sakuga frame, plus a small Sakuga button that calls the browser's showPicker() where it exists."
      qubit="Schedules and deadlines. The native picker owns validation; the button is an optional shortcut."
      said={`date: ${date} · time: ${time}`}
      tall
    >
      <div className="skg-field">
        <span>Date</span>
        <DateField label="Run date" name="run_date" value={date} onChange={setDate} />
      </div>
      <div className="skg-field">
        <span>Time</span>
        <TimeField label="Run time" name="run_time" value={time} onChange={setTime} />
      </div>
    </DemoCard>
  );
}

function FileDemo() {
  const [files, setFiles] = useState<string[]>([]);
  return (
    <DemoCard
      title="SakugaFile"
      tag="<SakugaFile>"
      how="Qubit extra: a Sakuga button opens the hidden native file input; the picked files appear as removable chips. Picking fires impact, removing fires pop."
      qubit="Uploads and imports. The hidden input carries name and the real FileList, so the form submits exactly what was picked."
      said={files.length ? files.join(", ") : "No files selected."}
      tall
    >
      <FileField
        label="Attachments"
        name="attachments"
        multiple
        accept=".md,.txt,.json"
        onFiles={(picked) => setFiles(picked.map((file) => file.name))}
      />
    </DemoCard>
  );
}

function MultiSelectDemo() {
  const [values, setValues] = useState<string[]>(["sessions"]);
  return (
    <DemoCard
      title="SakugaMultiSelect"
      tag="<SakugaMultiSelect>"
      how="Qubit extra: a combobox trigger plus a panel with a filtering SakugaInput and SakugaCheckbox rows. Escape/outside click closes it; one hidden input per value carries the form data."
      qubit="Any multi-value setting (tags, enabled toolsets, notification channels). Upstream ships no multi-select."
      said={`values: ${values.length ? values.join(", ") : "(none)"}`}
      tall
    >
      <MultiSelect
        label="Notify on"
        name="notify"
        values={values}
        onChange={setValues}
        options={[
          { value: "sessions", label: "Sessions" },
          { value: "files", label: "Files" },
          { value: "models", label: "Models" },
          { value: "errors", label: "Errors" },
          { value: "approvals", label: "Approvals" },
        ]}
      />
    </DemoCard>
  );
}

function FormRowDemo() {
  const [retention, setRetention] = useState("30");
  return (
    <DemoCard
      title="SakugaFormRow"
      tag="<SakugaFormRow>"
      how="Qubit extra: a label, a control and the upstream click-help bubble in one dense row, with an optional description."
      qubit="Settings rows that need an explanation without a permanent paragraph. The help text is never the only place required information lives."
      said={`retention: ${retention || "(empty)"} days`}
      tall
    >
      <FormRow
        label="Retention"
        htmlFor="skg-retention"
        help="How long completed sessions are kept before the nightly prune removes them."
        description="Used by the nightly prune job."
      >
        <Input
          id="skg-retention"
          label="Retention days"
          name="retention"
          value={retention}
          onChange={setRetention}
        />
      </FormRow>
    </DemoCard>
  );
}

function PaginationDemo() {
  const [page, setPage] = useState(3);
  return (
    <DemoCard
      title="SakugaPagination"
      tag="<SakugaPagination>"
      how="Qubit extra: previous/next Sakuga buttons and a compact Sakuga page field. The field commits on Enter or blur and clamps to the real range."
      qubit="Long lists (sessions, logs, calls). onChange only fires when the committed page changes."
      said={`page ${page} of 12`}
      tall
    >
      <Pagination page={page} pageCount={12} onChange={setPage} />
    </DemoCard>
  );
}

const FX_CUES: FxCue[] = [
  "impact",
  "success",
  "error",
  "warning",
  "celebrate",
  "pop",
  "slash",
  "tick",
  "approval",
  "agents",
];

function FxBusDemo() {
  const [last, setLast] = useState<FxCue | null>(null);
  return (
    <DemoCard
      title="Effect bus (emitFx)"
      tag="qubit:fx"
      how="Qubit extra: a named cue drawn with the upstream primitives. Every cue anchors on the viewport centre unless an element is given."
      qubit="Pages fire these for real outcomes only: success after a completed action, error on a caught failure, warning before a destructive step, approval when a gate opens. They never imply state by themselves."
      said={last ? `last cue: ${last}` : "Pick a cue."}
      actions={
        <>
          {FX_CUES.map((cue) => (
            <button
              key={cue}
              type="button"
              className="skg-pill"
              onClick={() => {
                void emitFx(cue, { x: window.scrollX + window.innerWidth / 2, y: window.scrollY + 180 });
                fireFx(cue, { silent: true });
                setLast(cue);
              }}
            >
              {cue}
            </button>
          ))}
        </>
      }
    >
      <p style={{ margin: 0, color: "var(--skg-dim, #8580b0)", fontSize: 13, textAlign: "center" }}>
        Cues draw on the page-wide canvas; they switch off under
        <code style={{ marginLeft: 4 }}>prefers-reduced-motion</code>.
      </p>
    </DemoCard>
  );
}

export default function SakugaGalleryPage() {
  return (
    <SettingsPage wide className="skg">
      <SettingsPageHeader
        description={
          <>
            Every vendored control and every Qubit extra, live in the dashboard.
            The effects are the upstream engine unchanged; the palette is
            upstream&apos;s white/lavender/violet/pink. A standalone copy that
            loads the files directly (like the upstream gallery) is at{" "}
            <a href="/sakuga-gallery.html" className="underline">
              /sakuga-gallery.html
            </a>
            .
          </>
        }
      />
      <div className="skg__grid">
        <ButtonDemos />
        <BooleanDemos />
        <StatesDemo />
        <ValueDemos />
        <TabsDemo />
        <ProgressDemos />
        <ToastDemo />
        <TooltipDemo />
        <HoverTooltipDemo />
        <SelectDemo />
        <TextareaDemo />
        <NumberDemo />
        <DateTimeDemo />
        <FileDemo />
        <MultiSelectDemo />
        <FormRowDemo />
        <PaginationDemo />
        <FxBusDemo />
      </div>
      <p className="skg-live">
        All controls load their script lazily from <code>/sakuga-vfx/&lt;name&gt;/&lt;name&gt;.js</code>{" "}
        on first render; the toggle (≈506 KB of painted frames) never loads until
        its card mounts. Reduced motion keeps every state change and drops the
        canvas effects.
      </p>
    </SettingsPage>
  );
}
