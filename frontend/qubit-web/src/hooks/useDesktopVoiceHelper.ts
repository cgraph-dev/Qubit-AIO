/** Is the system-wide desktop voice-typing helper live and owning Right-Ctrl?
 *
 *  When it is, `/qubit-chat` must NOT install its own in-page Right-Ctrl
 *  push-to-talk, or both would react to the same key. The desktop helper then
 *  types into whatever is focused (including this page's composer).
 */
import { useEffect, useState } from "react";
import { voiceEdit } from "@/lib/voiceEdit";

export function useDesktopVoiceHelper(active: boolean, pollMs = 15000): boolean {
  const [live, setLive] = useState(false);
  useEffect(() => {
    if (!active) {
      setLive(false);
      return;
    }
    let cancelled = false;
    const tick = async () => {
      try {
        const s = await voiceEdit.status();
        if (!cancelled) setLive(Boolean(s.enabled && s.helper_live));
      } catch {
        if (!cancelled) setLive(false);
      }
    };
    void tick();
    const id = setInterval(tick, pollMs);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [active, pollMs]);
  return live;
}
