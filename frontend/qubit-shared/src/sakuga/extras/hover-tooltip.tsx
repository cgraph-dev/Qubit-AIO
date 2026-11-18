/* SakugaHoverTooltip — the upstream tooltip bubble, but attached to any trigger.
 *
 * The vendored `<sakuga-tooltip>` owns its own "?" button, so it cannot wrap an
 * arbitrary icon, label or row. This Qubit extra reuses the same look (white
 * paper card, ink border, lavender shadow, manga lettering) and the same
 * interaction (hover, focus, tap, Escape) while leaving the trigger's own
 * element, handler and semantics untouched.
 */
import {
  createElement,
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { emitFxAt } from '../fx';

export interface SakugaHoverTooltipProps {
  /** The hint. Keep it short, like the upstream help bubble. */
  content: ReactNode;
  /** Optional bold first line. */
  label?: string;
  children: ReactNode;
  placement?: 'top' | 'bottom';
  className?: string;
  disabled?: boolean;
  /** Delay before hiding after the pointer leaves, ms. */
  closeDelay?: number;
}

export function SakugaHoverTooltip({
  content,
  label,
  children,
  placement = 'top',
  className,
  disabled = false,
  closeDelay = 90,
}: SakugaHoverTooltipProps) {
  const [open, setOpen] = useState(false);
  const [bump, setBump] = useState(0);
  const wrap = useRef<HTMLSpanElement | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clear = () => {
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
  };

  const show = useCallback(() => {
    if (disabled) return;
    clear();
    setOpen(true);
    if (wrap.current) void emitFxAt('tick', wrap.current, { scale: 0.45 });
  }, [disabled]);

  const hide = useCallback(() => {
    if (disabled) return;
    clear();
    timer.current = setTimeout(() => setOpen(false), closeDelay);
  }, [closeDelay, disabled]);

  useEffect(() => () => clear(), []);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  const onClick = () => {
    if (disabled) return;
    // A click is also a tap: keep it open and play a small landing reaction.
    setBump((value) => value + 1);
    setOpen(true);
    if (wrap.current) void emitFxAt('pop', wrap.current, { scale: 0.5 });
  };

  return createElement(
    'span',
    {
      ref: wrap,
      className: ['qsk-hover-tip', className].filter(Boolean).join(' '),
      'data-open': open || undefined,
      onMouseEnter: show,
      onMouseLeave: hide,
      onFocusCapture: show,
      onBlurCapture: hide,
      onClick,
    },
    children,
    open
      ? createElement(
        'span',
        {
          key: bump,
          role: 'tooltip',
          className: 'qsk-hover-tip__bubble',
          'data-placement': placement,
        },
        label
          ? createElement('span', { className: 'qsk-hover-tip__label' }, label)
          : null,
        createElement('span', { className: 'qsk-hover-tip__text' }, content),
      )
      : null,
  );
}
