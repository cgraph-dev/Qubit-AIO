/**
 * Global host for the floating voice waveform widget.
 *
 * Mounted ONCE in the App shell (next to `PluginSlot name="overlay"`), so the
 * widget shows on ALL routes — it is not page-scoped. The host owns no voice
 * logic of its own; it only READS existing signals (never writes them):
 *
 * - listening ← `[data-testid="voice-status"][data-state]` (ChatPage's derived
 *   `voiceState` convention) via MutationObserver;
 * - thinking  ← `.qchat__transcript[aria-busy]` (ChatPage turn-in-flight
 *   `busy` — the "Qubit is thinking…" row) via MutationObserver;
 * - speaking  ← `window.speechSynthesis.speaking` or a playing `<audio>`
 *   element (Talk/VoiceEdit TTS paths), polled at 1 Hz — no sockets, in line
 *   with WS_SINGLE_PATH (poll/event-driven like the status consumers).
 *
 * Click (no drag) forwards to the existing Voice button
 * (`button[aria-label="Voice input" | "Stop voice input"]` — the same
 * `toggleVoice` the button fires; ChatPage is persistently mounted by the
 * shell, so the target exists even on other routes). No ChatPage logic, no
 * state machine, no backend, and no settings key is touched.
 */
import { useCallback, useEffect, useState } from "react";
import { VoiceWaveform } from "./VoiceWaveform";
import {
  resolveWaveformState,
  turnBusyFromAriaBusy,
  waveformFromVoiceStatusAttr,
  type WaveformState,
} from "@/lib/voice/waveform-state";
import "./VoiceWaveform.css";

const VOICE_STATUS_SELECTOR = '[data-testid="voice-status"]';
const TRANSCRIPT_SELECTOR = ".qchat__transcript";
const VOICE_BUTTON_SELECTOR =
  'button[aria-label="Voice input"], button[aria-label="Stop voice input"]';

function readVoiceStatusAttr(): string | null {
  try {
    return document.querySelector(VOICE_STATUS_SELECTOR)?.getAttribute("data-state") ?? null;
  } catch {
    return null;
  }
}

function readTranscriptBusy(): string | null {
  try {
    return document.querySelector(TRANSCRIPT_SELECTOR)?.getAttribute("aria-busy") ?? null;
  } catch {
    return null;
  }
}

function readPlaybackActive(): boolean {
  try {
    if (window.speechSynthesis?.speaking === true) return true;
    const audios = document.querySelectorAll("audio");
    for (const audio of Array.from(audios)) {
      if (!audio.paused && !audio.ended && audio.currentTime > 0) return true;
    }
  } catch {
    /* DOM unavailable — not speaking */
  }
  return false;
}

function deriveGlobalState(): WaveformState {
  const statusAttr = readVoiceStatusAttr();
  const listening = waveformFromVoiceStatusAttr(statusAttr);
  if (listening === "listening") return "listening";
  const busy = turnBusyFromAriaBusy(readTranscriptBusy());
  return resolveWaveformState({
    transcriptionState: statusAttr,
    turnBusy: busy,
    speaking: readPlaybackActive(),
  });
}

export function VoiceWaveformHost() {
  const [waveform, setWaveform] = useState<WaveformState>(() => deriveGlobalState());

  useEffect(() => {
    const refresh = () => {
      setWaveform((prev) => {
        const next = deriveGlobalState();
        return next === prev ? prev : next;
      });
    };
    refresh();
    const observer = new MutationObserver(refresh);
    try {
      observer.observe(document.body, {
        subtree: true,
        attributes: true,
        attributeFilter: ["data-state", "aria-busy"],
      });
    } catch {
      /* body unavailable in some test shells — interval still covers it */
    }
    const timer = window.setInterval(refresh, 1000); // playback poll (no sockets)
    return () => {
      observer.disconnect();
      window.clearInterval(timer);
    };
  }, []);

  const forwardToVoiceButton = useCallback(() => {
    try {
      const button = document.querySelector<HTMLButtonElement>(VOICE_BUTTON_SELECTOR);
      button?.click();
    } catch {
      /* ChatPage host not mounted yet — nothing to toggle */
    }
  }, []);

  return (
    <div className="vw-host" data-testid="voice-waveform-host">
      <VoiceWaveform state={waveform} onToggle={forwardToVoiceButton} />
    </div>
  );
}
