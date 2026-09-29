/**
 * React binding for the shared {@link TranscriptionController}.
 *
 * The controller instance is created once per component mount and disposed on
 * unmount; the hook returns the live snapshot plus the controller itself (so
 * callers can start/stop/cancel and set the requested engine).
 */
import { useEffect, useState } from "react";
import {
  TranscriptionController,
  type TranscriptionControllerOptions,
  type TranscriptionSnapshot,
} from "../lib/voice";

export interface UseTranscriptionControllerResult {
  controller: TranscriptionController;
  snapshot: TranscriptionSnapshot;
}

export function useTranscriptionController(
  options: TranscriptionControllerOptions = {},
): UseTranscriptionControllerResult {
  // The controller is created once for the component's lifetime. `options` is
  // only consulted at construction (injected in tests; empty in production).
  const [controller] = useState(
    () => new TranscriptionController(options),
  );
  const [snapshot, setSnapshot] = useState<TranscriptionSnapshot>(
    () => controller.getSnapshot(),
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
