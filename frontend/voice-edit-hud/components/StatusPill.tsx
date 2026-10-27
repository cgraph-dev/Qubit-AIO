import { levelPercent, stateLabel, STATE_LABELS } from '@/lib/format';
import type { HudState } from '@/lib/types';

interface StatusPillProps {
  state: HudState;
  level: number;
}

export function StatusPill({ state, level }: StatusPillProps) {
  const known = state in STATE_LABELS;
  const tone = known ? state : 'idle';
  return (
    <span
      className={`pill pill--${tone}`}
      data-status-pill
      data-testid="status-pill"
      role="status"
      aria-live="polite"
    >
      <span className="pill__dot" aria-hidden="true" />
      <span className="pill__label">{stateLabel(state)}</span>
      <span className="pill__level" aria-label={`Input level ${levelPercent(level)} percent`}>
        LVL {levelPercent(level)}
      </span>
    </span>
  );
}
