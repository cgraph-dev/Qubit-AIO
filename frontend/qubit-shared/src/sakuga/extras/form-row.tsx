/* SakugaFormRow — a labelled row with an optional click-help bubble.
 *
 * Dense settings rows need a label, a control and sometimes an explanation.
 * The help affordance is the upstream `SakugaTooltip` (a real button with a
 * speech-bubble hint), never the only place required information lives.
 */
import { createElement, type CSSProperties, type ReactNode } from 'react';
import { SakugaTooltip } from '../elements';
import { DESCRIPTION_STYLE, LABEL_STYLE } from './common';

export interface SakugaFormRowProps {
  label: string;
  help?: string;
  htmlFor?: string;
  children?: ReactNode;
  description?: ReactNode;
  className?: string;
  style?: CSSProperties;
}

export function SakugaFormRow(props: SakugaFormRowProps) {
  const { label, help, htmlFor, children, description, className, style } = props;
  return createElement(
    'div',
    {
      className: ['qsk-form-row', className].filter(Boolean).join(' '),
      style: { display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0, ...style },
    },
    createElement(
      'div',
      { style: { display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 } },
      createElement('label', { htmlFor, style: LABEL_STYLE }, label),
      help
        ? createElement(SakugaTooltip, {
          text: help,
          label: `Help: ${label}`,
          className: 'qsk-form-row__help',
        })
        : null,
    ),
    children,
    description ? createElement('span', { style: DESCRIPTION_STYLE }, description) : null,
  );
}
