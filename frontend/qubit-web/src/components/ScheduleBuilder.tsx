import { useCallback } from "react";
import { Label } from "@nous-research/ui/ui/components/label";
import { Button, DateField, Input, NumberField, Select, TimeField } from "@/sakuga/ui";
import { useI18n } from "@/i18n";
import {
  buildScheduleString,
  DEFAULT_SCHEDULE_STATE,
  type IntervalUnit,
  type ScheduleBuilderState,
  type ScheduleMode,
  type Weekday,
  WEEKDAY_INDEXES,
} from "@/lib/schedule";

/**
 * Human-readable schedule picker for cron job create/edit flows.
 *
 * Replaces the raw "type a cron expression" input that lived inline in
 * ``CronPage``. The picker still emits a single backend-compatible
 * schedule string (see ``cron/jobs.py::parse_schedule``), but the user
 * fills out shape-appropriate inputs (time picker, weekday toggles,
 * datetime-local field) per mode.
 *
 * Architecture:
 *
 *  - The component is fully controlled. Parent owns the
 *    ``ScheduleBuilderState`` and the derived schedule string (built
 *    via ``buildScheduleString`` in render).
 *  - Mode-specific state slots (``timeOfDay``, ``weekdays``, ...) are
 *    preserved across mode switches so flipping back to a previous mode
 *    doesn't erase the user's work.
 *  - The "Custom" mode is an escape hatch — surfacing it as a normal
 *    option (instead of hiding it behind an "advanced" toggle) keeps
 *    power-user workflows discoverable without making everyone scroll
 *    past it.
 */
/** Human-readable preview; backend payloads still use buildScheduleString. */
export function describeScheduleChoice(value: ScheduleBuilderState): string {
  const time = value.timeOfDay || "09:00";
  switch (value.mode) {
    case "interval": {
      const n = value.intervalValue;
      const unit = n === 1 ? value.intervalUnit.replace(/s$/, "") : value.intervalUnit;
      return n >= 1 ? `Run every ${n} ${unit}` : "Choose a valid interval";
    }
    case "daily": return `Run every day at ${time}`;
    case "weekly": {
      const names = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
      const days = value.weekdays.slice().sort((a, b) => a - b).map((day) => names[day]);
      return `Run ${days.length ? "on " + days.join(", ") : "every day"} at ${time}`;
    }
    case "monthly": return `Run on day ${value.dayOfMonth} of each month at ${time}`;
    case "once": return value.onceAt ? `Run once on ${value.onceAt.replace("T", " at ")}` : "Choose a date and time";
    case "custom": return value.custom.trim() ? "Using a custom schedule" : "Enter your custom schedule";
  }
}

export function ScheduleBuilder({ onChange, value }: ScheduleBuilderProps) {
  const { t } = useI18n();
  const cronStrings = t.cron;
  const modeStrings = cronStrings.scheduleModes;

  const update = useCallback(
    (patch: Partial<ScheduleBuilderState>) => {
      onChange({ ...value, ...patch });
    },
    [onChange, value],
  );

  const toggleWeekday = useCallback(
    (day: Weekday) => {
      const present = value.weekdays.includes(day);
      update({
        weekdays: present
          ? value.weekdays.filter((d) => d !== day)
          : [...value.weekdays, day],
      });
    },
    [update, value.weekdays],
  );

  return (
    <div className="cron-schedule-builder grid gap-3">
      <div className="grid gap-2">
        <Label htmlFor="cron-schedule-mode">
          {cronStrings.scheduleMode ?? "Schedule"}
        </Label>
        <Select
          id="cron-schedule-mode"
          label={cronStrings.scheduleMode ?? "Schedule"}
          value={value.mode}
          onChange={(v) => update({ mode: v as ScheduleMode })}
          options={[
            { value: "interval", label: modeStrings.interval },
            { value: "daily", label: modeStrings.daily },
            { value: "weekly", label: modeStrings.weekly },
            { value: "monthly", label: modeStrings.monthly },
            { value: "once", label: modeStrings.once },
            { value: "custom", label: modeStrings.custom },
          ]}
        />
      </div>

      {value.mode === "interval" && (
        <div className="cron-interval-grid grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div className="grid gap-2">
            <NumberField
              label={modeStrings.intervalEvery}
              id="cron-interval-value"
              min={1}
              max={9999}
              value={value.intervalValue}
              onChange={(v) => {
                const n = typeof v === "number" ? v : parseInt(v, 10);
                update({
                  intervalValue: Number.isFinite(n) && n > 0 ? n : 1,
                });
              }}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="cron-interval-unit">{modeStrings.intervalUnit}</Label>
            <Select
              id="cron-interval-unit"
              label={modeStrings.intervalUnit}
              value={value.intervalUnit}
              onChange={(v) => update({ intervalUnit: v as IntervalUnit })}
              options={[
                { value: "minutes", label: modeStrings.unitMinutes },
                { value: "hours", label: modeStrings.unitHours },
                { value: "days", label: modeStrings.unitDays },
              ]}
            />
          </div>
        </div>
      )}

      {value.mode === "daily" && (
        <TimeOfDayField
          id="cron-daily-time"
          label={modeStrings.timeOfDay}
          value={value.timeOfDay}
          onChange={(timeOfDay) => update({ timeOfDay })}
        />
      )}

      {value.mode === "weekly" && (
        <>
          <div className="grid gap-2">
            <Label>{modeStrings.weekdays}</Label>
            <div
              className="flex flex-wrap gap-1.5"
              role="group"
              aria-label={modeStrings.weekdays}
            >
              {WEEKDAY_INDEXES.map((d) => {
                const isOn = value.weekdays.includes(d);
                return (
                  <Button
                    key={d}
                    variant={isOn ? "primary" : "secondary"}
                    aria-pressed={isOn}
                    onClick={() => toggleWeekday(d)}
                    className="min-w-[2.5rem] font-mono-ui text-xs uppercase"
                  >
                    {modeStrings.weekdaysShort[d]}
                  </Button>
                );
              })}
            </div>
          </div>
          <TimeOfDayField
            id="cron-weekly-time"
            label={modeStrings.timeOfDay}
            value={value.timeOfDay}
            onChange={(timeOfDay) => update({ timeOfDay })}
          />
        </>
      )}

      {value.mode === "monthly" && (
        <div className="grid grid-cols-[1fr_1fr] gap-3">
          <div className="grid gap-2">
            <NumberField
              label={modeStrings.dayOfMonth}
              id="cron-month-day"
              min={1}
              max={31}
              value={value.dayOfMonth}
              onChange={(v) => {
                const n = typeof v === "number" ? v : parseInt(v, 10);
                update({
                  dayOfMonth:
                    Number.isFinite(n) && n >= 1 && n <= 31 ? n : 1,
                });
              }}
            />
          </div>
          <TimeOfDayField
            id="cron-monthly-time"
            label={modeStrings.timeOfDay}
            value={value.timeOfDay}
            onChange={(timeOfDay) => update({ timeOfDay })}
          />
        </div>
      )}

      {value.mode === "once" && (
        <div className="grid gap-2">
          <Label htmlFor="cron-once-at">{modeStrings.onceAt}</Label>
          {/* Date + time in the Sakuga frame; the pair still emits the exact
              "YYYY-MM-DDTHH:MM" shape ``parse_schedule`` accepts. */}
          <div className="grid grid-cols-[1.2fr_1fr] gap-3">
            <DateField
              label={modeStrings.onceAt}
              id="cron-once-at"
              value={value.onceAt.slice(0, 10)}
              onChange={(date) =>
                update({ onceAt: `${date}T${value.onceAt.slice(11, 16) || "00:00"}` })
              }
            />
            <TimeField
              label="Time"
              id="cron-once-at-time"
              value={value.onceAt.slice(11, 16)}
              onChange={(time) =>
                update({
                  onceAt: `${value.onceAt.slice(0, 10) || new Date().toISOString().slice(0, 10)}T${time}`,
                })
              }
            />
          </div>
        </div>
      )}

      {value.mode === "custom" && (
        <div className="grid gap-2">
          <Label htmlFor="cron-custom-expr">{modeStrings.customLabel}</Label>
          <Input
            id="cron-custom-expr"
            label={modeStrings.customLabel}
            placeholder={modeStrings.customPlaceholder}
            value={value.custom}
            onChange={(v) => update({ custom: v })}
            className="qsk-field font-mono-ui"
          />
          <p className="text-xs text-muted-foreground">
            {modeStrings.customHint}
          </p>
        </div>
      )}

      {/* Human outcome; the backend expression stays in buildScheduleString. */}
      <div className="cron-schedule-preview" role="status" aria-live="polite">
        <span className="cron-schedule-preview__title">Your schedule</span>
        <strong>{buildScheduleString(value) ? describeScheduleChoice(value) : modeStrings.previewEmpty}</strong>
      </div>
    </div>
  );
}

function TimeOfDayField({
  id,
  label,
  onChange,
  value,
}: TimeOfDayFieldProps) {
  return (
    <div className="grid gap-2">
      <Label htmlFor={id}>{label}</Label>
      <TimeField
        label={label}
        id={id}
        value={value}
        onChange={(next) => onChange(next)}
      />
    </div>
  );
}

export { DEFAULT_SCHEDULE_STATE };

interface ScheduleBuilderProps {
  onChange: (state: ScheduleBuilderState) => void;
  value: ScheduleBuilderState;
}

interface TimeOfDayFieldProps {
  id: string;
  label: string;
  onChange: (value: string) => void;
  value: string;
}
