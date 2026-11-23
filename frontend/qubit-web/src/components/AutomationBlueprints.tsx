import { useCallback, useEffect, useState } from "react";
import { Clock, Wand2 } from "lucide-react";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";
import { Label } from "@nous-research/ui/ui/components/label";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button, Input, Select, TimeField, emitFx, showSakugaToast } from "@/sakuga/ui";
import { api } from "@/lib/api";
import type { AutomationBlueprint, AutomationBlueprintField } from "@/lib/api";
import { cn, themedBody } from "@/lib/utils";

interface AutomationBlueprintsProps {
  profile: string;
  /** Called after a blueprint is instantiated so the parent can refresh its job list. */
  onCreated?: () => void;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

const FIELD_TYPES = new Set<AutomationBlueprintField["type"]>([
  "time",
  "enum",
  "text",
  "weekdays",
]);

function normalizeField(
  raw: unknown,
  index: number,
): AutomationBlueprintField | null {
  if (!isRecord(raw)) return null;
  const name = asString(raw.name) || asString(raw.key);
  if (!name) return null;
  const rawType = asString(raw.type) as AutomationBlueprintField["type"];
  return {
    name,
    type: FIELD_TYPES.has(rawType) ? rawType : "text",
    label: asString(raw.label) || name || `Field ${index + 1}`,
    default: typeof raw.default === "string" ? raw.default : null,
    options: Array.isArray(raw.options) ? raw.options.map((o) => String(o)) : [],
    optional: raw.optional === true,
    strict: raw.strict !== false,
    help: asString(raw.help),
  };
}

/**
 * Normalize one catalog entry into the blueprint shape the form renders.
 *
 * The canonical backend returns ``{key,title,…,fields:[…]}``; the Qubit compat
 * catalog returns a lighter ``{name,kind,instantiate}`` shape with no
 * ``fields``. The dashboard must render either without the ``fields is not
 * iterable`` crash the unguarded form produced, so every entry is normalized
 * here and field-less entries render read-only rather than exploding.
 */
export function normalizeBlueprint(raw: unknown): AutomationBlueprint | null {
  if (!isRecord(raw)) return null;
  const key = asString(raw.key) || asString(raw.name) || asString(raw.id);
  if (!key) return null;
  const fields = Array.isArray(raw.fields) ? raw.fields : [];
  const tags = Array.isArray(raw.tags) ? raw.tags : [];
  return {
    key,
    title:
      asString(raw.title) || asString(raw.label) || asString(raw.name) || key,
    description: asString(raw.description) || asString(raw.kind),
    category: asString(raw.category),
    tags: tags.map((tag) => (typeof tag === "string" ? tag : String(tag))),
    fields: fields
      .map(normalizeField)
      .filter((field): field is AutomationBlueprintField => field !== null),
    command: asString(raw.command) || asString(raw.instantiate),
    appUrl: asString(raw.appUrl),
  };
}

/** Initial form values for a blueprint = each field's default (or ""). */
function initialValues(blueprint: AutomationBlueprint): Record<string, string> {
  const out: Record<string, string> = {};
  for (const f of blueprint.fields ?? []) out[f.name] = f.default ?? "";
  return out;
}

function FieldInput({
  id,
  field,
  value,
  onChange,
}: {
  id: string;
  field: AutomationBlueprintField;
  value: string;
  onChange: (v: string) => void;
}) {
  if (field.type === "enum" || field.type === "weekdays") {
    return (
      <Select
        id={id}
        label={field.label}
        value={value}
        onChange={(v) => onChange(v)}
        options={field.options.map((opt) => ({ value: opt, label: opt }))}
      />
    );
  }
  if (field.type === "time") {
    return (
      <TimeField
        id={id}
        label={field.label}
        value={value}
        onChange={(next) => onChange(next)}
      />
    );
  }
  // text
  return (
    <Input
      id={id}
      label={field.label}
      value={value}
      placeholder={field.help || field.label}
      onChange={(v) => onChange(v)}
      className="qsk-field"
    />
  );
}

function BlueprintCard({
  blueprint,
  profile,
  showToast,
  onCreated,
}: {
  blueprint: AutomationBlueprint;
  profile: string;
  showToast: (message: string, type: "error" | "success") => void;
  onCreated?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [values, setValues] = useState<Record<string, string>>(() => initialValues(blueprint));
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fields = blueprint.fields ?? [];
  const tags = blueprint.tags ?? [];

  const submit = useCallback(async () => {
    setSubmitting(true);
    setError(null);
    try {
      const job = await api.instantiateAutomationBlueprint({ blueprint: blueprint.key, values }, profile);
      const when = job.schedule_display ? ` — ${job.schedule_display}` : "";
      showToast(`${blueprint.title} scheduled${when}`, "success");
      setOpen(false);
      setValues(initialValues(blueprint));
      onCreated?.();
    } catch (e) {
      // 422 from the API carries the slot-level validation message.
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg.replace(/^\d+:\s*/, ""));
    } finally {
      setSubmitting(false);
    }
  }, [blueprint, values, profile, showToast, onCreated]);

  return (
    <Card className={cn("overflow-hidden", themedBody)}>
      <CardContent className="space-y-3 p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <Wand2 className="h-4 w-4 shrink-0 opacity-70" />
              <span className="font-medium">{blueprint.title}</span>
            </div>
            <p className="mt-1 text-sm opacity-70">{blueprint.description}</p>
            <div className="mt-2 flex flex-wrap gap-1">
              {tags.map((t) => (
                <Badge key={t} tone="secondary">
                  {t}
                </Badge>
              ))}
            </div>
          </div>
          {fields.length > 0 ? (
            <Button
              variant={open ? "primary" : "secondary"}
              onClick={() => setOpen((o) => !o)}
            >
              {open ? "Cancel" : "Set up"}
            </Button>
          ) : (
            <span className="shrink-0 text-xs opacity-60">
              No configurable fields
            </span>
          )}
        </div>

        {open && fields.length > 0 && (
          <div className="space-y-3 border-t pt-3">
            {fields.map((f) => (
              <div key={f.name} className="space-y-1">
                <Label htmlFor={`${blueprint.key}-${f.name}`}>{f.label}</Label>
                <FieldInput
                  id={`${blueprint.key}-${f.name}`}
                  field={f}
                  value={values[f.name] ?? ""}
                  onChange={(v) => setValues((prev) => ({ ...prev, [f.name]: v }))}
                />
                {f.help && f.type !== "text" ? (
                  <p className="text-xs opacity-60">{f.help}</p>
                ) : null}
              </div>
            ))}
            {error ? (
              <p className="text-sm text-red-500" role="alert">
                {error}
              </p>
            ) : null}
            <div className="flex items-center gap-2">
              <Button
                onClick={() => void submit()}
                disabled={submitting}
              >
                {submitting ? <Spinner /> : <Clock aria-hidden />}
                Schedule it
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/**
 * Automation Blueprints gallery — the form-where-there's-a-screen surface. Each blueprint
 * card expands into an inline form (one field per typed slot); submitting POSTs
 * to /api/cron/blueprints/instantiate which fills the blueprint and creates the job
 * via the same create_job path as everything else.
 */
export function AutomationBlueprints({ profile, onCreated }: AutomationBlueprintsProps) {
  const showToast = useCallback((message: string, kind?: "success" | "error") => {
    showSakugaToast(message);
    emitFx(kind === "error" ? "error" : "success");
  }, []);
  const [blueprints, setBlueprints] = useState<AutomationBlueprint[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getAutomationBlueprints(profile)
      .then((r) => {
        if (cancelled) return;
        const raw = Array.isArray(r?.blueprints) ? r.blueprints : [];
        setBlueprints(
          raw
            .map(normalizeBlueprint)
            .filter((b): b is AutomationBlueprint => b !== null),
        );
      })
      .catch((e) => {
        if (!cancelled) setLoadError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [profile]);

  if (loadError) {
    return <p className="text-sm text-red-500">Couldn't load blueprints: {loadError}</p>;
  }
  if (blueprints === null) {
    return (
      <div className="flex items-center gap-2 opacity-70">
        <Spinner className="h-4 w-4" /> Loading blueprints…
      </div>
    );
  }
  if (blueprints.length === 0) {
    return <p className="opacity-70">No automation blueprints available.</p>;
  }

  return (
    <>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {blueprints.map((r) => (
          <BlueprintCard
            key={r.key}
            blueprint={r}
            profile={profile}
            showToast={showToast}
            onCreated={onCreated}
          />
        ))}
      </div>
    </>
  );
}

export default AutomationBlueprints;
