/**
 * Dashboard waveform mount gate (`voice_edit.waveform_visible`).
 *
 * Renders `VoiceWaveformHost` only while the setting is explicitly visible.
 * Hidden by default (owner 2026-10-04: the OS orb is the only always-on
 * waveform) — a fresh/absent key or a failed fetch keeps the host unmounted.
 * The setting is re-read every 5 s (one cheap GET, no rAF) so a voice
 * "show/hide waveform" flip mounts/unmounts a loaded page live — before,
 * the read-once gate made voice flips look dead until reload (2026-10-04).
 */
import { useEffect, useState } from "react";
import { useLocation } from "react-router";
import { VoiceWaveformHost } from "@/components/VoiceWaveformHost";
import { voiceEdit } from "@/lib/voiceEdit";

export function useWaveformVisible(pollMs = 5000): boolean {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    let cancelled = false;
    const read = () => {
      voiceEdit.settings().then(
        (out) => {
          if (!cancelled) {
            setVisible(out.settings?.waveform_visible === true);
          }
        },
        () => {
          /* fetch failure keeps the last state */
        },
      );
    };
    read();
    const timer = window.setInterval(read, pollMs);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [pollMs]);
  return visible;
}

export function WaveformOverlayMount() {
  const visible = useWaveformVisible();
  const { pathname } = useLocation();
  if (!visible || pathname === "/qubit-chat") return null;
  return <VoiceWaveformHost />;
}
