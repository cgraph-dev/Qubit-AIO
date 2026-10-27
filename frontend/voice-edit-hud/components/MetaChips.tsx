import {
  elapsedLabel,
  factsDroppedCount,
  truncate,
} from '@/lib/format';
import type { PreviewPayload } from '@/lib/types';

interface MetaChipsProps {
  preview: PreviewPayload | null;
}

interface Row {
  key: string;
  label: string;
  value: string;
  tone?: 'ok' | 'warn' | 'bad' | 'accent';
}

function value(value: unknown, limit = 26): string {
  if (value === undefined || value === null || value === '') return '—';
  return truncate(String(value), limit);
}

export function MetaChips({ preview }: MetaChipsProps) {
  const detected = preview?.detected ?? {};
  const dropped = factsDroppedCount(preview);

  const rows: Row[] = [
    { key: 'action', label: 'ACTION', value: value(detected.action), tone: 'accent' },
    { key: 'style', label: 'STYLE', value: value(detected.style) },
    { key: 'language', label: 'LANGUAGE', value: value(detected.language) },
    { key: 'model', label: 'MODEL', value: value(preview?.model) },
    { key: 'provider', label: 'PROVIDER', value: value(preview?.provider) },
    {
      key: 'privacy',
      label: 'PRIVACY',
      value: value(preview?.privacy_mode),
      tone: preview?.privacy_mode ? 'ok' : undefined,
    },
    {
      key: 'locality',
      label: 'LOCALITY',
      value: value(preview?.locality),
      tone: preview?.locality === 'local' ? 'ok' : undefined,
    },
    { key: 'target', label: 'TARGET', value: value(preview?.target_app) },
    { key: 'elapsed', label: 'ELAPSED', value: elapsedLabel(preview) },
    { key: 'prompt', label: 'PROMPT', value: value(preview?.prompt_version) },
    {
      key: 'request',
      label: 'REQUEST',
      value: value(preview?.request_id, 18),
    },
    {
      key: 'chars',
      label: 'OUTPUT',
      value: preview?.proposed ? `${preview.proposed.length} CH` : '—',
    },
  ];

  const flags: Row[] = [
    {
      key: 'injection',
      label: 'INJECTION',
      value: preview ? (preview.injection_suspected ? 'FLAGGED' : 'CLEAR') : '—',
      tone: preview?.injection_suspected ? 'bad' : preview ? 'ok' : undefined,
    },
    {
      key: 'clarify',
      label: 'CLARIFY',
      value: preview ? (preview.clarification_returned ? 'ASKED' : 'NONE') : '—',
      tone: preview?.clarification_returned ? 'warn' : preview ? 'ok' : undefined,
    },
    {
      key: 'facts',
      label: 'FACTS',
      value: preview ? `${dropped} DROPPED` : '—',
      tone: dropped > 0 ? 'warn' : preview ? 'ok' : undefined,
    },
    {
      key: 'clipboard',
      label: 'CLIPBOARD',
      value: preview
        ? preview.clipboard_ok === false
          ? 'FAILED'
          : preview.clipboard_ok
            ? 'WRITTEN'
            : '—'
        : '—',
      tone:
        preview?.clipboard_ok === false
          ? 'bad'
          : preview?.clipboard_ok
            ? 'ok'
            : undefined,
    },
  ];

  return (
    <div className="meta">
      <dl className="meta__grid">
        {rows.map((row) => (
          <div key={row.key} className="meta__row">
            <dt className="meta__k">{row.label}</dt>
            <dd
              className={`meta__v ${row.tone ? `meta__v--${row.tone}` : ''}`}
              title={row.value}
            >
              {row.value}
            </dd>
          </div>
        ))}
      </dl>
      <div className="meta__flags">
        {flags.map((flag) => (
          <span
            key={flag.key}
            className={`flag ${flag.tone ? `flag--${flag.tone}` : ''}`}
            title={`${flag.label}: ${flag.value}`}
          >
            <span className="flag__k">{flag.label}</span>
            <span className="flag__v">{flag.value}</span>
          </span>
        ))}
      </div>
    </div>
  );
}
