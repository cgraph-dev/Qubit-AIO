import type { HudStateData } from '@/lib/types';
import { clockLabel, stateLabel } from '@/lib/format';

interface TopBarProps {
  state: HudStateData;
  clock: Date | null;
  isMock: boolean;
  connected: boolean;
  onReplay: () => void;
  onRefusal: () => void;
}

function connectionLabel(state: HudStateData): string {
  switch (state.connection) {
    case 'open':
      return 'LINK UP';
    case 'connecting':
      return 'LINKING';
    case 'closed':
      return 'LINK LOST';
    case 'no-token':
      return 'NO TOKEN';
    case 'mock':
      return 'MOCK FEED';
    default:
      return 'OFFLINE';
  }
}

function feedLabel(state: HudStateData, isMock: boolean): string {
  if (isMock) return 'FIXTURE';
  return state.connection === 'open' ? 'LIVE' : '—';
}

export function TopBar({
  state,
  clock,
  isMock,
  connected,
  onReplay,
  onRefusal,
}: TopBarProps) {
  const linkTone =
    state.connection === 'open' || state.connection === 'mock'
      ? 'ok'
      : state.connection === 'connecting'
        ? 'wait'
        : 'bad';

  return (
    <header className="topbar" data-reveal>
      <div className="brand">
        <span className="brand__mark" aria-hidden="true">
          <i />
          <i />
          <i />
        </span>
        <span className="brand__copy">
          <span className="brand__name">QUBIT</span>
          <span className="brand__sub">VOICE EDIT // HUD</span>
        </span>
      </div>

      <div className="topbar__mid" aria-hidden="true">
        <span className="tmetric">
          <span className="tmetric__k">CHANNEL</span>
          <span className="tmetric__v">/voice-edit/ws</span>
        </span>
        <span className="tmetric">
          <span className="tmetric__k">STATE</span>
          <span className="tmetric__v">{stateLabel(state.state)}</span>
        </span>
        <span className="tmetric">
          <span className="tmetric__k">FEED</span>
          <span className="tmetric__v">{feedLabel(state, isMock)}</span>
        </span>
        <span className="tmetric">
          <span className="tmetric__k">PROTOCOL</span>
          <span className="tmetric__v">{state.version || '—'}</span>
        </span>
      </div>

      <div className="topbar__right">
        {isMock ? (
          <span className="mock-tools">
            <button
              type="button"
              className="micro-btn"
              onClick={onReplay}
              title="Restart the mock fixture feed"
            >
              REPLAY
            </button>
            <button
              type="button"
              className="micro-btn micro-btn--warn"
              onClick={onRefusal}
              title="Inject a demo refusal to review the error banner"
            >
              REFUSAL
            </button>
          </span>
        ) : null}
        <span className={`link link--${linkTone}`}>
          <span className="link__dot" aria-hidden="true" />
          {connectionLabel(state)}
        </span>
        <time className="topbar__clock" dateTime={clock?.toISOString()}>
          {clock ? clockLabel(clock) : '--:--:--'}
        </time>
        <span
          className={`privacy-tag ${connected ? 'privacy-tag--on' : ''}`}
          title="The HUD only talks to the local bridge over its own origin"
        >
          LOCAL ONLY
        </span>
      </div>
    </header>
  );
}
