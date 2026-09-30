export * from "./types";
export { BrowserSpeechAdapter, asSpeechRecognition, BROWSER_PRIVACY_NOTICE } from "./browser-speech-adapter";
export {
  LocalStreamingSTTAdapter,
  LOCAL_PRIVACY_NOTICE,
  resampleLinear,
} from "./local-streaming-stt-adapter";
export {
  TerminalSTTAdapter,
  terminalCaptureAvailable,
} from "./terminal-stt-adapter";
export {
  TranscriptionController,
  defaultAdapterFactory,
} from "./transcription-controller";
export type {
  AdapterFactory,
  TranscriptionControllerOptions,
} from "./transcription-controller";
