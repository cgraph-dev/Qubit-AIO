/**
 * TerminalSTTAdapter — the local desktop/terminal audio capture path.
 *
 * It uses the SAME local STT backend and the SAME WebSocket event protocol as
 * the browser `local` engine; only the capture source differs (the Qubit
 * desktop/terminal bridge instead of `getUserMedia`). No transcription logic is
 * duplicated. When no desktop bridge is present (plain web dashboard), the
 * adapter reports unavailable and the controller falls back to `local` with an
 * explicit reason — never a silent switch.
 */
import {
  LocalStreamingSTTAdapter,
  LocalSttUnavailable,
  type LocalAdapterDeps,
  type PcmCapture,
} from "./local-streaming-stt-adapter";

interface DesktopSttBridge {
  openPcmCapture(onFrame: (pcm: Int16Array) => void): PcmCapture;
}

declare global {
  interface Window {
    __QUBIT_DESKTOP_STT__?: DesktopSttBridge;
  }
}

/** True when a Qubit desktop/terminal audio bridge is present. */
export function terminalCaptureAvailable(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.__QUBIT_DESKTOP_STT__?.openPcmCapture === "function"
  );
}

function hasDesktopBridge(): boolean {
  return terminalCaptureAvailable();
}

function terminalDeps(deps: LocalAdapterDeps): LocalAdapterDeps {
  return {
    ...deps,
    // The desktop bridge owns device selection; no getUserMedia is requested.
    getUserMedia: deps.getUserMedia ?? (async () => new MediaStream()),
    createCapture:
      deps.createCapture ??
      ((_stream, onFrame) => {
        const bridge = window.__QUBIT_DESKTOP_STT__;
        if (!bridge) {
          throw new LocalSttUnavailable(
            "VOICE_TERMINAL_UNAVAILABLE",
            "desktop/terminal audio bridge is unavailable",
          );
        }
        return bridge.openPcmCapture(onFrame);
      }),
  };
}

export class TerminalSTTAdapter extends LocalStreamingSTTAdapter {
  constructor(deps: LocalAdapterDeps = {}) {
    super(terminalDeps(deps), { kind: "terminal", label: "terminal" });
  }

  isAvailable(): boolean {
    return hasDesktopBridge();
  }

  availabilityReason(): string | null {
    return hasDesktopBridge()
      ? null
      : "desktop/terminal audio bridge unavailable (using local capture)";
  }
}
