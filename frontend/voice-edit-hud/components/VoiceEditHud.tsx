'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { canRunAction } from '@/lib/actions';
import { stateDetail } from '@/lib/format';
import { useClock } from '@/lib/useClock';
import { usePrefersReducedMotion } from '@/lib/usePrefersReducedMotion';
import { useVoiceSocket } from '@/lib/useVoiceSocket';
import { isTypingTarget } from '@/lib/actions';
import type { ClientAction } from '@/lib/types';
import { ActionBar } from './ActionBar';
import { EditorOverlay } from './EditorOverlay';
import { HistoryConsole } from './HistoryConsole';
import { HudBackground } from './HudBackground';
import { MetaChips } from './MetaChips';
import { NoticeBanner } from './NoticeBanner';
import { PanelFrame } from './PanelFrame';
import { PreviewPanel, type PreviewView } from './PreviewPanel';
import { StatusPill } from './StatusPill';
import { TopBar } from './TopBar';
import { Transcript } from './Transcript';
import { VoiceRing } from './VoiceRing';

interface VoiceEditHudProps {
  /** Force the fixture feed (used by tests and static screenshots). */
  mock?: boolean;
}

const PHASES: Record<string, string> = {
  idle: '00',
  listening: '01',
  processing: '02',
  preview: '03',
  clarify: '03',
  done: '04',
  error: '!!',
  interrupted: '!!',
  disabled: '--',
};

export function VoiceEditHud({ mock }: VoiceEditHudProps) {
  const reduced = usePrefersReducedMotion();
  const clock = useClock();
  const {
    state,
    send,
    restartMock,
    simulateRefusal,
    clearHistory,
    dismissError,
    dismissNotice,
    isMock,
    connected,
  } = useVoiceSocket({ mock });
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [previewView, setPreviewView] = useState<PreviewView>('diff');
  const [localNotice, setLocalNotice] = useState<string | null>(null);

  const preview = state.preview;
  const availability = useMemo(
    () => ({
      state: state.state,
      hasPreview: Boolean(preview),
      connected,
    }),
    [state.state, preview, connected],
  );

  const showNotice = useCallback((message: string) => {
    setLocalNotice(message);
  }, []);

  useEffect(() => {
    if (!localNotice) return;
    const timer = setTimeout(() => setLocalNotice(null), 3400);
    return () => clearTimeout(timer);
  }, [localNotice]);

  const handleAction = useCallback(
    (action: ClientAction) => {
      if (action === 'edit') {
        setEditorOpen(true);
        return;
      }
      if (action === 'copy') {
        send('copy');
        if (isMock) {
          const clipboard = navigator.clipboard;
          if (clipboard?.writeText) {
            clipboard
              .writeText(preview?.proposed ?? '')
              .then(() => showNotice('Proposal copied to the clipboard.'))
              .catch(() =>
                showNotice('Copy request sent; the clipboard write was blocked.'),
              );
          } else {
            showNotice('Copy request sent; the Clipboard API is unavailable.');
          }
        } else {
          showNotice('Copy sent to the helper.');
        }
        return;
      }
      send(action);
    },
    [send, isMock, preview, showNotice],
  );

  const handleEditorSubmit = useCallback(
    (text: string) => {
      send('edit_submit', { text });
      setEditorOpen(false);
      setPreviewView('diff');
      showNotice('Manual edit staged for review.');
    },
    [send, showNotice],
  );

  const handleReplay = useCallback(() => {
    restartMock();
    setEditorOpen(false);
    setPreviewView('diff');
    showNotice('Mock fixture restarted.');
  }, [restartMock, showNotice]);

  // Entrance + ambient scan motion (never for reduced-motion users).
  useEffect(() => {
    if (reduced) return;
    let cancelled = false;
    let context: { revert: () => void } | undefined;
    // If the ticker is throttled (background tab, headless capture), the
    // panels must still become visible: drop the entrance's inline styles.
    const safety = window.setTimeout(() => {
      rootRef.current
        ?.querySelectorAll<HTMLElement>('[data-reveal]')
        .forEach((element) => {
          element.style.opacity = '';
          element.style.transform = '';
        });
    }, 1800);
    void import('gsap').then(({ gsap }) => {
      if (cancelled) return;
      context = gsap.context(() => {
        gsap.from('[data-reveal]', {
          y: 18,
          opacity: 0,
          duration: 0.62,
          ease: 'power3.out',
          stagger: 0.07,
          clearProps: 'transform,opacity',
        });
        gsap.fromTo(
          '.scanbeam',
          { yPercent: -60, opacity: 0 },
          {
            yPercent: 160,
            opacity: 0.7,
            duration: 7.5,
            repeat: -1,
            ease: 'none',
            delay: 0.9,
          },
        );
      }, rootRef);
    });
    return () => {
      cancelled = true;
      clearTimeout(safety);
      context?.revert();
    };
  }, [reduced]);

  // State transitions get one short, purposeful pulse.
  useEffect(() => {
    if (reduced) return;
    let cancelled = false;
    void import('gsap').then(({ gsap }) => {
      if (cancelled) return;
      gsap.fromTo(
        '[data-status-pill]',
        { scale: 0.94, opacity: 0.6 },
        {
          scale: 1,
          opacity: 1,
          duration: 0.34,
          ease: 'back.out(2.2)',
          clearProps: 'transform,opacity',
        },
      );
    });
    return () => {
      cancelled = true;
    };
  }, [state.state, reduced]);

  // A fresh preview resolves in, token by token.
  useEffect(() => {
    if (reduced || !state.preview) return;
    let cancelled = false;
    void import('gsap').then(({ gsap }) => {
      if (cancelled) return;
      const scope = rootRef.current;
      const tokens = scope?.querySelectorAll('[data-diff-token]') ?? [];
      gsap.fromTo(
        tokens,
        { opacity: 0, y: 3 },
        {
          opacity: 1,
          y: 0,
          duration: 0.26,
          stagger: 0.006,
          ease: 'power2.out',
          clearProps: 'transform,opacity',
        },
      );
      gsap.fromTo(
        '[data-preview-body]',
        { opacity: 0.35 },
        { opacity: 1, duration: 0.3, clearProps: 'opacity' },
      );
    });
    return () => {
      cancelled = true;
    };
  }, [state.previewRevision, state.preview, reduced]);

  // Keyboard contract (matches the helper overlay's shortcuts).
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (editorOpen) return;
      if (isTypingTarget(event.target)) return;
      const meta = event.ctrlKey || event.metaKey;
      const key = event.key.toLowerCase();

      if (meta && event.key === 'Enter') {
        if (canRunAction('apply', availability)) {
          event.preventDefault();
          handleAction('apply');
        }
        return;
      }
      if (meta && key === 'r') {
        if (canRunAction('retry', availability)) {
          event.preventDefault();
          handleAction('retry');
        }
        return;
      }
      if (meta && key === 'z') {
        if (canRunAction('revert', availability)) {
          event.preventDefault();
          handleAction('revert');
        }
        return;
      }
      if (event.key === 'Escape') {
        if (canRunAction('cancel', availability)) {
          event.preventDefault();
          handleAction('cancel');
        }
        return;
      }
      if (!meta && key === 'c') {
        if (canRunAction('copy', availability)) {
          event.preventDefault();
          handleAction('copy');
        }
        return;
      }
      if (!meta && key === 'e') {
        if (canRunAction('edit', availability)) {
          event.preventDefault();
          handleAction('edit');
        }
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [availability, editorOpen, handleAction]);

  const phase = PHASES[state.state] ?? '--';
  const detail =
    state.connection === 'no-token'
      ? 'No token — open this HUD from the helper link, or add ?mock=1 to preview the console.'
      : state.connection === 'closed'
        ? 'Link lost — retrying the local bridge. Nothing was applied.'
        : stateDetail(state.state, state.detail);
  const notice = state.notice ?? localNotice;

  return (
    <div className="hud" ref={rootRef}>
      <h1 className="sr-only">Qubit Voice Edit HUD</h1>
      <HudBackground reduced={reduced} />
      <div className="hud__scanlines" aria-hidden="true" />
      <div className="hud__grain" aria-hidden="true" />
      <div className="scanbeam" aria-hidden="true" />

      <div className="hud__inner">
        <TopBar
          state={state}
          clock={clock}
          isMock={isMock}
          connected={connected}
          onReplay={handleReplay}
          onRefusal={simulateRefusal}
        />

        <NoticeBanner
          error={state.error}
          notice={notice}
          onDismissError={dismissError}
          onDismissNotice={() => {
            dismissNotice();
            setLocalNotice(null);
          }}
        />

        <div className="hud__body">
          <main className="hud__col hud__col--main">
            <PanelFrame
              index="01"
              label="Voice uplink"
              tone="cyan"
              className="panel--stage"
              meta={
                <span className="panel__meta-line">
                  <span
                    className={`meta-dot ${connected ? 'meta-dot--on' : ''}`}
                    aria-hidden="true"
                  />
                  {connected ? 'UPLINK' : 'DOWNLINK'} ·{' '}
                  {isMock ? 'FIXTURE' : 'LIVE'}
                </span>
              }
            >
              <div className="stage">
                <VoiceRing state={state.state} level={state.level} reduced={reduced} />
                <div className="stage__copy">
                  <div className="stage__statusline">
                    <StatusPill state={state.state} level={state.level} />
                    <span className="stage__phase">PHASE {phase}</span>
                  </div>
                  <p className="stage__detail">{detail}</p>
                  <Transcript transcript={state.transcript} state={state.state} />
                </div>
              </div>
            </PanelFrame>

            <PanelFrame
              index="02"
              label="Proposed edit"
              tone="magenta"
              className="panel--preview"
              meta={
                <span className="panel__meta-line">
                  {preview
                    ? `${preview.proposed?.length ?? 0} CH · ${preview.detected?.action ?? 'edit'}`
                    : 'NO BUFFER'}
                </span>
              }
            >
              <PreviewPanel
                preview={preview}
                view={previewView}
                onView={setPreviewView}
              />
            </PanelFrame>
          </main>

          <aside className="hud__col hud__col--side">
            <PanelFrame
              index="03"
              label="Telemetry"
              tone="violet"
              className="panel--telemetry"
              meta={
                <span className="panel__meta-line">
                  {preview ? 'LOCKED' : 'AWAITING'}
                </span>
              }
            >
              <MetaChips preview={preview} />
            </PanelFrame>

            <PanelFrame
              index="04"
              label="History console"
              tone="violet"
              className="panel--history"
              meta={<span className="panel__meta-line">{state.history.length} LOG</span>}
            >
              <HistoryConsole
                history={state.history}
                onClear={clearHistory}
              />
            </PanelFrame>
          </aside>
        </div>

        <div className="actiondeck" data-reveal>
          <ActionBar availability={availability} onAction={handleAction} />
        </div>

        <footer className="hud__foot">
          <span className="hud__foot-brand">QUBIT // VOICE EDIT</span>
          <span className="hud__foot-rule">
            PREVIEW GATE · SAFE PASTE · LOCAL ONLY
          </span>
          <span className="hud__foot-keys">
            CTRL+ENTER APPLY · CTRL+R RETRY · CTRL+Z REVERT · ESC CANCEL · E
            EDIT · C COPY
          </span>
          <span className="hud__foot-motion">{reduced ? 'REDUCED MOTION' : 'MOTION ON'}</span>
        </footer>
      </div>

      <EditorOverlay
        open={editorOpen}
        initialText={preview?.proposed ?? ''}
        connected={connected}
        onClose={() => setEditorOpen(false)}
        onSubmit={handleEditorSubmit}
      />
    </div>
  );
}
