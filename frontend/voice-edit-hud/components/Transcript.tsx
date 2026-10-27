'use client';

import { useEffect, useRef } from 'react';
import type { HudState, TranscriptState } from '@/lib/types';

interface TranscriptProps {
  transcript: TranscriptState;
  state: HudState;
}

export function Transcript({ transcript, state }: TranscriptProps) {
  const textRef = useRef<HTMLDivElement | null>(null);
  const { committed, pending } = transcript;

  useEffect(() => {
    const node = textRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [committed, pending]);

  const badge =
    pending.length > 0
      ? 'LIVE'
      : committed.length > 0
        ? 'FINAL'
        : state === 'listening'
          ? 'ARMED'
          : 'IDLE';

  return (
    <div className="transcript">
      <div className="transcript__head">
        <span className="micro-label">LIVE TRANSCRIPT</span>
        <span
          className={`transcript__badge ${
            pending ? 'transcript__badge--live' : 'transcript__badge--final'
          }`}
        >
          {badge}
        </span>
      </div>
      <div
        className="transcript__text"
        ref={textRef}
        aria-live="polite"
        aria-atomic="false"
      >
        {committed ? <span className="transcript__settled">{committed}</span> : null}
        {pending ? (
          <>
            {committed ? ' ' : null}
            <span className="transcript__tail" data-unsettled-tail>
              {pending}
            </span>
            <span className="transcript__caret" aria-hidden="true" />
          </>
        ) : null}
        {!committed && !pending ? (
          <span className="transcript__empty">
            Speak or type an edit — the recognised command lands here. The
            unsettled tail stays highlighted until the STT finalises it.
          </span>
        ) : null}
      </div>
    </div>
  );
}
