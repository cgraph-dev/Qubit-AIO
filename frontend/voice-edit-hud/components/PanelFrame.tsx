import type { ReactNode } from 'react';

interface PanelFrameProps {
  index: string;
  label: string;
  meta?: ReactNode;
  tone?: 'magenta' | 'cyan' | 'violet';
  className?: string;
  children: ReactNode;
  bodyClassName?: string;
}

/** One clipped-corner HUD panel with an index, micro label and live meta. */
export function PanelFrame({
  index,
  label,
  meta,
  tone = 'magenta',
  className = '',
  children,
  bodyClassName = '',
}: PanelFrameProps) {
  return (
    <section
      className={`panel panel--${tone} ${className}`.trim()}
      data-reveal
    >
      <div className="panel__surface">
        <span className="panel__grid" aria-hidden="true" />
        <header className="panel__head">
          <span className="panel__index" aria-hidden="true">
            {index}
          </span>
          <h2 className="panel__label">{label}</h2>
          <div className="panel__meta">{meta}</div>
        </header>
        <div className={`panel__body ${bodyClassName}`.trim()}>{children}</div>
        <span className="panel__tick panel__tick--tl" aria-hidden="true" />
        <span className="panel__tick panel__tick--br" aria-hidden="true" />
      </div>
    </section>
  );
}
