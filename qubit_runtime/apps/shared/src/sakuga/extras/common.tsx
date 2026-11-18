/* Shared chrome for the Qubit-owned Sakuga extras.
 *
 * Upstream ships custom elements, not native form controls for textarea,
 * number, date/time, file or multi-select. These extras wrap real native
 * elements and compose the vendored controls around them, so the semantics
 * (value/name, form submission, keyboard, AT) stay native while the look and
 * the effects stay Sakuga. Every value below is the same compact chrome
 * `sakuga.css` applies via `::part()`; the palette variables are untouched.
 */
import { createElement, useCallback, useRef, type CSSProperties, type ReactNode } from 'react';
import { emitFxAt } from '../fx';

export const LABEL_STYLE: CSSProperties = {
  color: 'var(--sk-ink, #1d1a4a)',
  fontSize: 12.5,
  fontWeight: 600,
  lineHeight: 1.3,
};

export const DESCRIPTION_STYLE: CSSProperties = {
  color: 'var(--sk-dim, #8580b0)',
  fontSize: 12,
  lineHeight: 1.4,
};

/** The Sakuga input frame, as a plain CSS object so extras work without CSS. */
export const FIELD_STYLE: CSSProperties = {
  width: 'var(--sk-input-width, 230px)',
  maxWidth: '100%',
  boxSizing: 'border-box',
  padding: '7px 12px',
  border: '2.5px solid var(--sk-ink, #1d1a4a)',
  borderRadius: 'var(--sk-r, 15px)',
  background: 'var(--sk-card, #fff)',
  boxShadow: '4px 5px 0 var(--sk-soft, #b8b3ff)',
  color: 'var(--sk-ink, #1d1a4a)',
  fontFamily: 'inherit',
  fontSize: 14,
  fontWeight: 600,
  lineHeight: 1.35,
  outline: 'none',
};

export const ROW_STYLE: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 6,
  minWidth: 0,
};

export const HIDDEN_INPUT_STYLE: CSSProperties = {
  position: 'absolute',
  width: 1,
  height: 1,
  opacity: 0,
  pointerEvents: 'none',
};

export interface ExtraFieldProps {
  label?: string;
  htmlFor?: string;
  description?: ReactNode;
  children?: ReactNode;
  className?: string;
  style?: CSSProperties;
}

/** A label + control + optional description, the dense settings-row shape. */
export function ExtraField(props: ExtraFieldProps) {
  const { label, htmlFor, description, children, className, style } = props;
  return createElement(
    'div',
    {
      className: ['qsk-extra', className].filter(Boolean).join(' '),
      style: { display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0, ...style },
    },
    label ? createElement('label', { htmlFor, style: LABEL_STYLE }, label) : null,
    children,
    description ? createElement('span', { style: DESCRIPTION_STYLE }, description) : null,
  );
}

/** A keystroke cue, coalesced so a fast typist does not spawn a cue per key. */
export function useTypeFx(): (el: Element | null) => void {
  const last = useRef(0);
  return useCallback((el: Element | null) => {
    const now = Date.now();
    if (now - last.current < 70) return;
    last.current = now;
    void emitFxAt('type', el, { scale: 0.5 });
  }, []);
}

/** A focus cue (three wedges and a puff, the upstream input reaction). */
export function useFocusFx(): (el: Element | null) => void {
  return useCallback((el: Element | null) => {
    void emitFxAt('focus', el, { scale: 0.6 });
  }, []);
}

/** Clamp a value into the optional bounds. */
export function clampNumber(value: number, min?: number, max?: number): number {
  let next = value;
  if (typeof min === 'number') next = Math.max(min, next);
  if (typeof max === 'number') next = Math.min(max, next);
  return next;
}
