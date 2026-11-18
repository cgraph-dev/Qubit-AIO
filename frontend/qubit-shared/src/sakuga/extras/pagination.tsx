/* SakugaPagination — previous/next plus a compact page field.
 *
 * The page field is a `SakugaInput` (text + numeric input mode, so no native
 * spinner): it accepts typing, commits on Enter or blur, and clamps to the
 * real range. onChange only fires when the committed page actually changes.
 */
import {
  createElement,
  useCallback,
  useEffect,
  useState,
  type CSSProperties,
} from 'react';
import { SakugaButton, SakugaInput } from '../elements';
import { clampNumber } from './common';

export interface SakugaPaginationProps {
  page: number;
  pageCount: number;
  onChange: (page: number) => void;
  label?: string;
  disabled?: boolean;
  className?: string;
  style?: CSSProperties;
}

export function SakugaPagination(props: SakugaPaginationProps) {
  const { page, pageCount, onChange, label = 'Pagination', disabled, className, style } = props;
  const last = Math.max(1, Math.floor(pageCount) || 1);
  const current = clampNumber(Math.floor(page) || 1, 1, last);
  const [draft, setDraft] = useState(String(current));

  useEffect(() => {
    setDraft(String(current));
  }, [current]);

  const commit = useCallback(() => {
    const parsed = Number.parseInt(draft, 10);
    if (!Number.isFinite(parsed)) {
      setDraft(String(current));
      return;
    }
    const next = clampNumber(parsed, 1, last);
    setDraft(String(next));
    if (next !== current) onChange(next);
  }, [current, draft, last, onChange]);

  const go = (next: number) => {
    const target = clampNumber(next, 1, last);
    setDraft(String(target));
    if (target !== current) onChange(target);
  };

  const goBy = (delta: 1 | -1) => {
    const typed = Number.parseInt(draft, 10);
    const base = Number.isFinite(typed) ? clampNumber(typed, 1, last) : current;
    go(base + delta);
  };

  return createElement(
    'nav',
    {
      className: ['qsk-pagination', className].filter(Boolean).join(' '),
      'aria-label': label,
      style: {
        display: 'flex',
        flexWrap: 'wrap',
        alignItems: 'center',
        gap: 8,
        ...style,
      },
    },
    createElement(SakugaButton, {
      variant: 'secondary',
      fx: false,
      type: 'button',
      disabled: disabled || current <= 1,
      'aria-label': 'Previous page',
      onClick: () => goBy(-1),
    }, '‹ Prev'),
    createElement(
      'div',
      { style: { display: 'flex', alignItems: 'center', gap: 6 } },
      createElement(SakugaInput, {
        label: `${label} page`,
        value: draft,
        inputMode: 'numeric',
        disabled,
        className: 'qsk-pagination__page',
        style: { '--sk-input-width': '70px' } as CSSProperties,
        onChange: setDraft,
        onEnter: commit,
        onBlur: commit,
      }),
      createElement('span', { style: { fontSize: 12.5, fontWeight: 600, color: 'var(--sk-dim, #8580b0)' } }, `of ${last}`),
    ),
    createElement(SakugaButton, {
      variant: 'secondary',
      fx: false,
      type: 'button',
      disabled: disabled || current >= last,
      'aria-label': 'Next page',
      onClick: () => goBy(1),
    }, 'Next ›'),
    createElement('span', { className: 'sr-only', 'aria-live': 'polite' }, `Page ${current} of ${last}`),
  );
}
