/**
 * React binding for the shared {@link ReadAloudController}.
 *
 * The controller is created once per mount and disposed on unmount; the hook
 * returns the live snapshot plus the controller so callers can start/toggle/
 * cancel. Synthesis goes through the EXISTING TTS path (`speakTalk` ->
 * `talk.speak`); playback uses one `HTMLAudioElement`. No second TTS stack.
 */
import { useEffect, useState } from "react";

import {
  ReadAloudController,
  audioElementPlayer,
  type ReadAloudDeps,
  type ReadAloudSnapshot,
} from "../lib/voice";
import { speakTalk } from "../lib/ocFamilyTalkApi";

/** Confirmation for the user-initiated speak action (no server prompt). */
const CONFIRM_YES = () => true;

/** Production deps: real TTS route + one audio element per utterance. */
export const defaultReadAloudDeps: ReadAloudDeps = {
  synthesize: async (text) => {
    const res = await speakTalk(text, CONFIRM_YES);
    return res.data_url ?? "";
  },
  createPlayer: () => audioElementPlayer(),
};

export interface UseReadAloudResult {
  controller: ReadAloudController;
  snapshot: ReadAloudSnapshot;
}

export function useReadAloud(
  deps: ReadAloudDeps = defaultReadAloudDeps,
): UseReadAloudResult {
  const [controller] = useState(() => new ReadAloudController(deps));
  const [snapshot, setSnapshot] = useState<ReadAloudSnapshot>(() =>
    controller.getSnapshot(),
  );
  useEffect(() => controller.subscribe(setSnapshot), [controller]);
  useEffect(
    () => () => {
      controller.dispose();
    },
    [controller],
  );
  return { controller, snapshot };
}
