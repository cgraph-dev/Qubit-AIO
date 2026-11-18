import { Label } from "@nous-research/ui/ui/components/label";
import { Input, Select, Toggle } from "@/sakuga/ui";

import { rebrandOptional } from "@/lib/rebrand";

function FieldHint({ schema, schemaKey }: { schema: Record<string, unknown>; schemaKey: string }) {
  const keyPath = schemaKey.includes(".") ? schemaKey : "";
  const description = rebrandOptional(schema.description as string | undefined);

  if (!keyPath && !description) return null;

  return (
    <div className="flex flex-col gap-0.5">
      {keyPath && <span className="text-xs font-mono text-text-tertiary">{keyPath}</span>}
      {description && <span className="text-xs text-text-secondary">{description}</span>}
    </div>
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function formatScalar(value: unknown): string {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function NestedValueEditor({
  fieldKey,
  value,
  onChange,
}: {
  fieldKey: string;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  if (isRecord(value)) {
    return (
      <div className="grid gap-2 border border-border p-2">
        {Object.entries(value).map(([subKey, subVal]) => (
          <div key={subKey} className="grid gap-1">
            <Label className="text-xs text-muted-foreground">{subKey}</Label>
            <NestedValueEditor
              fieldKey={`${fieldKey}.${subKey}`}
              value={subVal}
              onChange={(next) => onChange({ ...value, [subKey]: next })}
            />
          </div>
        ))}
      </div>
    );
  }

  if (Array.isArray(value)) {
    return (
      <div className="grid gap-2">
        {value.map((item, index) => (
          <div key={`${fieldKey}.${index}`} className="grid gap-1">
            <Label className="text-xs text-muted-foreground">Item {index + 1}</Label>
            <NestedValueEditor
              fieldKey={`${fieldKey}.${index}`}
              value={item}
              onChange={(next) =>
                onChange(value.map((existing, i) => (i === index ? next : existing)))
              }
            />
          </div>
        ))}
      </div>
    );
  }

  if (typeof value === "boolean") {
    return <Toggle label={fieldKey} checked={value === true} onChange={(next) => onChange(next)} />;
  }

  if (typeof value === "number") {
    // Number inputs are outside the upstream component set; keep the native
    // control and preserve the scalar's numeric type across a round trip.
    return (
      <input
        aria-label={fieldKey}
        type="number"
        value={formatScalar(value)}
        onChange={(e) => {
          const raw = e.target.value;
          const parsed = raw === "" ? 0 : Number(raw);
          if (!Number.isNaN(parsed)) onChange(parsed);
        }}
        className="qsk-field text-xs"
      />
    );
  }

  return (
    <Input
      aria-label={fieldKey}
      label={fieldKey}
      value={formatScalar(value)}
      onChange={(raw) => onChange(raw)}
      className="qsk-field text-xs"
    />
  );
}

export function AutoField({
  schemaKey,
  schema,
  value,
  onChange,
}: AutoFieldProps) {
  const rawLabel = schemaKey.split(".").pop() ?? schemaKey;
  const label = rawLabel.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  // Associate the visible <Label> with the control so every config field has
  // an accessible name (the Label previously had no htmlFor and controls had
  // no id / aria-label — screen readers announced bare "edit"/"switch").
  const fieldId = `cfg-${schemaKey.replace(/[^a-zA-Z0-9_-]/g, "-")}`;

  if (isRecord(value) || (Array.isArray(value) && value.some((item) => isRecord(item)))) {
    return (
      <div className="grid gap-3 border border-border p-3">
        <Label className="text-xs font-medium">{label}</Label>
        <FieldHint schema={schema} schemaKey={schemaKey} />
        <NestedValueEditor fieldKey={schemaKey} value={value} onChange={onChange} />
      </div>
    );
  }

  if (schema.type === "boolean") {
    return (
      <div className="flex items-center justify-between gap-4">
        <div className="flex flex-col gap-0.5">
          <Label className="text-sm">{label}</Label>
          <FieldHint schema={schema} schemaKey={schemaKey} />
        </div>
        <Toggle
          label={label}
          checked={!!value}
          onChange={(next) => onChange(next)}
        />
      </div>
    );
  }

  if (schema.type === "select") {
    const options = (schema.options as string[]) ?? [];
    return (
      <div className="grid gap-1.5">
        <Label htmlFor={fieldId} className="text-sm">{label}</Label>
        <FieldHint schema={schema} schemaKey={schemaKey} />
        <Select
          id={fieldId}
          label={label}
          value={String(value ?? "")}
          onChange={(v) => onChange(v)}
          options={options.map((opt) => ({ value: opt, label: opt || "(none)" }))}
        />
      </div>
    );
  }

  if (schema.type === "number") {
    return (
      <div className="grid gap-1.5">
        <Label htmlFor={fieldId} className="text-sm">{label}</Label>
        <FieldHint schema={schema} schemaKey={schemaKey} />
        <input
          id={fieldId}
          type="number"
          className="qsk-field"
          value={value === undefined || value === null ? "" : String(value)}
          onChange={(e) => {
            const raw = e.target.value;
            if (raw === "") {
              onChange(0);
              return;
            }
            const n = Number(raw);
            if (!Number.isNaN(n)) {
              onChange(n);
            }
          }}
        />
      </div>
    );
  }

  if (schema.type === "text") {
    return (
      <div className="grid gap-1.5">
        <Label htmlFor={fieldId} className="text-sm">{label}</Label>
        <FieldHint schema={schema} schemaKey={schemaKey} />
        <textarea
          id={fieldId}
          className="flex min-h-[80px] w-full border border-input bg-transparent px-3 py-2 text-sm shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          value={String(value ?? "")}
          onChange={(e) => onChange(e.target.value)}
        />
      </div>
    );
  }

  if (schema.type === "list") {
    return (
      <div className="grid gap-1.5">
        <Label htmlFor={fieldId} className="text-sm">{label}</Label>
        <FieldHint schema={schema} schemaKey={schemaKey} />
        <Input
          id={fieldId}
          label={label}
          value={Array.isArray(value) ? value.join(", ") : String(value ?? "")}
          onChange={(raw) =>
            onChange(
              raw
                .split(",")
                .map((s) => s.trim())
                .filter(Boolean),
            )
          }
          placeholder="comma-separated values"
          className="qsk-field"
        />
      </div>
    );
  }

  return (
    <div className="grid gap-1.5">
      <Label htmlFor={fieldId} className="text-sm">{label}</Label>
      <FieldHint schema={schema} schemaKey={schemaKey} />
      <Input
        id={fieldId}
        label={label}
        value={String(value ?? "")}
        onChange={(v) => onChange(v)}
        className="qsk-field"
      />
    </div>
  );
}

interface AutoFieldProps {
  schemaKey: string;
  schema: Record<string, unknown>;
  value: unknown;
  onChange: (v: unknown) => void;
}
