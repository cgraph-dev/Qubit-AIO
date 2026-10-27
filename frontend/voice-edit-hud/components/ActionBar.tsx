'use client';

import {
  ACTION_SPECS,
  canRunAction,
  type ActionAvailability,
} from '@/lib/actions';
import type { ClientAction } from '@/lib/types';

interface ActionBarProps {
  availability: ActionAvailability;
  onAction: (action: ClientAction) => void;
}

export function ActionBar({ availability, onAction }: ActionBarProps) {
  const enabledCount = ACTION_SPECS.filter((spec) =>
    canRunAction(spec.action, availability),
  ).length;

  return (
    <div className="actions">
      <div className="actions__main" role="group" aria-label="Edit actions">
        {ACTION_SPECS.map((spec) => {
          const enabled = canRunAction(spec.action, availability);
          return (
            <button
              key={spec.action}
              type="button"
              className={[
                'hud-btn',
                spec.primary ? 'hud-btn--primary' : '',
                spec.accent ? `hud-btn--${spec.accent}` : '',
                !enabled ? 'is-disabled' : '',
              ]
                .filter(Boolean)
                .join(' ')}
              data-action={spec.action}
              disabled={!enabled}
              aria-keyshortcuts={spec.hint}
              onClick={() => onAction(spec.action)}
            >
              <span className="hud-btn__label">{spec.label}</span>
              <span className="hud-btn__hint">{spec.hint}</span>
            </button>
          );
        })}
      </div>

      <div className="actions__side">
        <span className={`actions__gate ${enabledCount > 1 ? 'is-open' : ''}`}>
          <span className="actions__gate-dot" aria-hidden="true" />
          {availability.hasPreview
            ? availability.state === 'done' ||
              availability.state === 'interrupted'
              ? 'APPLIED · REVERT OPEN'
              : 'PREVIEW ARMED'
            : 'AWAITING PREVIEW'}
        </span>
        <span className="actions__rule">
          APPLY USES THE SAFE PASTE PRIMITIVE · NOTHING IS WRITTEN WITHOUT A
          PREVIEW
        </span>
      </div>
    </div>
  );
}
