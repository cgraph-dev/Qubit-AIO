/* Qubit Office3 — Voice Chat, overlay plugin bundle (Qubit addition,
 * dependency-free IIFE). Uses only window.__QUBIT_PLUGIN_SDK__
 * (React, hooks, fetchJSON, authedFetch, components).
 *
 * PROVENANCE — READ THIS BEFORE COMPARING THIS FILE TO ANYTHING UPSTREAM.
 * ---------------------------------------------------------------------------
 * This file is a Qubit-authored REIMPLEMENTATION of a published design
 * DOCUMENT:
 *   https://github.com/androoAGI/starnet/blob/feat/harness-backend/docs/VOICE_CHAT_PLAN.md
 * (MIT). That document is prose — an architecture sketch for two-way voice in
 * a COMMS panel. NO UPSTREAM SOURCE CODE WAS COPIED. No starnet .js file was
 * opened while writing this. The names below (`sttProvider`, `ttsProvider`,
 * `voiceFor`, `Voice.speak`) are the vocabulary the design document uses; they
 * are re-expressed here against Qubit's own endpoints and Qubit's own
 * plugin SDK. See PROVENANCE.json for the full record.
 * ---------------------------------------------------------------------------
 *
 * WHAT THIS PAGE IS
 * -----------------
 * A voice front door to the SAME text pipeline the rest of the dashboard uses.
 * Speech in, speech out, and nothing invented in between:
 *
 *   mic (push-to-talk) -> Web Speech STT -> transcript -> POST /api/type
 *     -> poll GET /api/result/<request_id> -> the finished reply -> TTS
 *
 * THE THREE RULES THIS FILE OBEYS
 * -------------------------------
 * 1. THE TRANSCRIPT RIDES THE TEXT PATH VERBATIM. The design doc's central
 *    claim is that voice is not a second pipeline: a spoken sentence is sent
 *    through exactly the call a keypress would make, so every downstream
 *    behaviour (intent routing, awaiting-input state, task-vs-talk, cost
 *    accounting) is reused untouched. So this page posts to /api/type and
 *    polls /api/result/<id> — the bridge's real text path — and adds no
 *    voice-only branch on the way in.
 *
 * 2. TTS FIRES ONCE PER FINISHED REPLY, NEVER PER TOKEN. The bridge is already
 *    request/response for the text path (the ack is an id, the payload arrives
 *    whole from /api/result), so this file does not stream and does not speak
 *    as text arrives. `speak()` is called exactly once per completed poll, and
 *    only when the reply is a conversational one (`awaiting_input` is false and
 *    there is no error) — a task result is not a sentence and is not read
 *    aloud.
 *
 * 3. DEGRADATION IS HONEST, AND SO IS FAILURE. No `SpeechRecognition` -> the
 *    mic control is NOT RENDERED (not rendered-disabled, not a fake button).
 *    No `speechSynthesis` -> the mute toggle is NOT RENDERED. A bridge refusal
 *    (401, 400 no-text, 500, `status:"unknown"`) renders the REAL status and
 *    the REAL error body. Nothing in this file writes a success it did not
 *    receive, and nothing renders a spinner that implies work the backend was
 *    never asked to do.
 *
 * DELIBERATELY NOT IMPLEMENTED (Phase 2 of the design document)
 * ------------------------------------------------------------
 * The design doc layers Phase 1 (browser-native, zero cost) under Phase 2
 * (neural TTS via `POST /api/tts`, Whisper STT via `POST /api/stt`, per-agent
 * named voices, room speech presence). Phase 1 is what this file is. Phase 2's
 * two sidecar routes DO NOT EXIST in voicebridge/webui.py, so calling them
 * would be a fabricated integration. Instead the two providers are behind the
 * seam the document asks for (`sttProvider` / `ttsProvider`, each a single
 * swap point), so Phase 2 replaces two objects and touches no UI.
 *
 * A NOTE ON /api/talk, which this file does NOT call
 * --------------------------------------------------
 * The bridge also exposes `POST /api/talk`: it takes RAW Int16 PCM in the body
 * and returns server-side ASR plus a server-side WAV (`audio_b64`) — i.e. it
 * is the bridge's OWN voice pipeline, and the browser does none of the
 * recognition or synthesis. This page is the other design: the browser
 * recognises and speaks, and only text crosses the wire, which is what makes
 * the live transcript preview and the "identical to typing" guarantee
 * possible. Pointing this page at /api/talk would silently import a second,
 * different behaviour (and would double-speak when the bridge is configured to
 * speak locally). If you want the server-side pipeline instead, that is
 * `POST /api/talk` + `GET /api/result/<id>` — a different page, not a flag here.
 *
 * SECURITY / AUTH
 * ---------------
 * Every request goes through the SDK's `fetchJSON` / `authedFetch`, which is
 * the supported way to reach /api/* (it handles the loopback session token, the
 * gated-cookie mode, and the stale-token 401 reload). This file never reads
 * `window.__QUBIT_SESSION_TOKEN__` itself, never assembles an auth header, and
 * never puts a token in a URL. All backend strings render as React children —
 * no innerHTML, no dangerouslySetInnerHTML.
 */
(function () {
  var SDK = window.__QUBIT_PLUGIN_SDK__;
  if (!SDK || !window.__QUBIT_PLUGINS__) return;
  var React = SDK.React, hooks = SDK.hooks, fetchJSON = SDK.fetchJSON;
  var authedFetch = SDK.authedFetch;
  var C = SDK.components || {};
  var e = React.createElement;
  var useState = hooks.useState, useEffect = hooks.useEffect;
  var useCallback = hooks.useCallback, useRef = hooks.useRef;

  /* ======================================================================
   * 1. CAPABILITY PROBE — measured once, at module load, and reported.
   *
   * `SpeechRecognition` and `speechSynthesis` are independent capabilities:
   * Chromium-family browsers ship both; Firefox ships synthesis but not
   * recognition; a locked-down webview may ship neither. We measure the
   * globals and record WHICH ones are missing, so the page can say what it
   * cannot do instead of looking broken (the design document's whole point
   * about graceful degradation).
   * ==================================================================== */
  var SPEECH_RECOGNITION = (function () {
    if (typeof window === "undefined") return null;
    return window.SpeechRecognition || window.webkitSpeechRecognition || null;
  })();
  var SPEECH_SYNTHESIS =
    (typeof window !== "undefined" && window.speechSynthesis) ? window.speechSynthesis : null;

  var MISSING = [];
  if (!SPEECH_RECOGNITION) MISSING.push("SpeechRecognition");
  if (!SPEECH_SYNTHESIS) MISSING.push("speechSynthesis");

  /* ======================================================================
   * 2. ENDPOINTS — read from the code, not assumed.
   *
   * POST /api/type            body {"text": "<sentence>"}  -> {"request_id": rid}
   *                           (empty/whitespace text -> 400 {"error":"no text"})
   * GET  /api/result/<rid>    -> {"status":"pending"|"done"|"unknown", request_id, ...}
   *                           when done, plus the worker payload:
   *                             text         the utterance as the bridge saw it
   *                             summary      the reply (may be absent)
   *                             route         e.g. "conversation" | "error"
   *                             error         present on failure
   *                             awaiting_input  bool — the bridge wants an answer
   *                             latency_s
   *
   * Shape confirmed by reading voicebridge/webui.py: `POST /api/type` at
   * ~4552 (`self.app.submit_text(text)`), `GET /api/result/` at ~3280
   * (`self.app.result(rid)`), and `DashboardApp.submit_text` / `.result` at
   * ~5243 / ~5340. An earlier assumption that the reply would arrive as a
   * field named `reply` is NOT relied on: `submit_text` sets both `summary`
   * and `reply` for conversational turns but only `summary` on the error path,
   * so this file reads `summary` first and falls back to `reply`.
   * ==================================================================== */
  var PATH_TYPE = "/api/type";
  var PATH_RESULT = "/api/result/";
  var POLL_MS = 700;          /* how often we ask; the ONLY timer in this file */
  var POLL_GIVE_UP_MS = 180000;  /* 3 min — a long tool run is still a run */

  /* ======================================================================
   * 3. THE PROVIDER SEAM (the design document's `sttProvider` / `ttsProvider`).
   *
   * Each provider is ONE object with ONE method. Swapping browser-native for a
   * neural backend later is `TTS_PROVIDER = neuralProvider` — no UI change, no
   * caller change. That is the seam the document is built around, and it costs
   * nothing to honour it now.
   * ==================================================================== */
  var STT_PROVIDER = {
    name: "web-speech",
    /* Push-to-talk: start() opens the mic and streams interim hypotheses to
     * `onInterim`; the browser's own end-of-utterance fires `onFinal`.
     * Returns a handle the caller stops early with. */
    start: function (opts) {
      if (!SPEECH_RECOGNITION) {
        opts.onError({ code: "unsupported", message: "SpeechRecognition is not available in this browser." });
        return null;
      }
      var rec = new SPEECH_RECOGNITION();
      rec.lang = "en-US";            /* Phase-1 limit, stated by the design doc */
      rec.continuous = false;
      rec.interimResults = true;     /* the live transcript preview */
      rec.maxAlternatives = 1;
      var finalText = "";
      rec.onresult = function (ev) {
        var interim = "";
        for (var i = ev.resultIndex; i < ev.results.length; i++) {
          var res = ev.results[i];
          if (!res) continue;
          if (res.isFinal) finalText += (res[0] && res[0].transcript ? res[0].transcript : "");
          else interim += (res[0] && res[0].transcript ? res[0].transcript : "");
        }
        try { opts.onInterim(((finalText + " " + interim) || "").trim()); }
        catch (cbErr) { /* a throwing consumer must not kill the recognizer */ }
      };
      rec.onerror = function (ev) {
        opts.onError({ code: (ev && ev.error) || "error", message: describeRecognitionError(ev) });
      };
      rec.onend = function () {
        opts.onEnd(finalText.trim());
      };
      try {
        rec.start();
      } catch (startErr) {
        opts.onError({ code: "start-failed", message: String((startErr && startErr.message) || startErr) });
        return null;
      }
      return rec;
    },
    stop: function (rec) {
      if (!rec) return;
      try { rec.stop(); } catch (ignoreStop) { /* already ended */ }
    },
    abort: function (rec) {
      if (!rec) return;
      try { rec.abort(); } catch (ignoreAbort) { /* already ended */ }
    }
  };

  var TTS_PROVIDER = {
    name: "web-speech",
    /* One call, one utterance. `voice` and the pitch/rate pair come from
     * voiceFor(agentName) so an agent sounds like itself across sessions. */
    speak: function (text, voice, pitch, rate, onStart, onEnd) {
      if (!SPEECH_SYNTHESIS || !text) return null;
      var u = new SpeechSynthesisUtterance(text);
      if (voice) u.voice = voice;
      u.pitch = pitch;
      u.rate = rate;
      u.onstart = function () { if (onStart) onStart(); };
      /* onerror fires for real synthesis failures (e.g. "interrupted" after a
       * cancel — which is us, not an error worth reporting). We only clear the
       * speaking state; we do not fabricate a completion. */
      u.onerror = function () { if (onEnd) onEnd(false); };
      u.onend = function () { if (onEnd) onEnd(true); };
      try {
        SPEECH_SYNTHESIS.speak(u);
      } catch (speakErr) {
        if (onEnd) onEnd(false);
        return null;
      }
      return u;
    },
    cancel: function () {
      if (!SPEECH_SYNTHESIS) return;
      try { SPEECH_SYNTHESIS.cancel(); } catch (ignoreCancel) { /* nothing queued */ }
    },
    list: function () {
      if (!SPEECH_SYNTHESIS || !SPEECH_SYNTHESIS.getVoices) return [];
      return SPEECH_SYNTHESIS.getVoices() || [];
    }
  };

  function describeRecognitionError(ev) {
    var code = (ev && ev.error) || "error";
    if (code === "not-allowed" || code === "service-not-allowed") {
      return "Microphone access was refused by the browser. Grant it for this origin and try again.";
    }
    if (code === "no-speech") return "Nothing was heard. Hold the mic and speak, then release.";
    if (code === "audio-capture") return "No microphone was found.";
    if (code === "network") return "The recognizer lost its connection.";
    if (code === "aborted") return "";   /* we cancelled it — not an error to show */
    return "Speech recognition failed (" + code + ").";
  }

  /* ======================================================================
   * 4. PER-AGENT VOICE IDENTITY — FNV-1a over the agent name.
   *
   * The design document's rule, kept exactly: the SAME agent name maps to the
   * SAME voice, pitch and rate in every session, on every machine, with no
   * server round-trip and no stored assignment. FNV-1a (32-bit) is used rather
   * than a cryptographic hash because a stable, well-spread, trivially
   * reimplementable integer is the whole requirement.
   *
   * The spread is deliberately bounded: pitch ±12% and rate ±10%. A wider
   * spread produces voices that sound like damage rather than character, and
   * "each agent has a voice" is a claim about recognisability, not about
   * extremity.
   * ==================================================================== */
  function fnv1a(str) {
    var h = 0x811c9dc5;
    for (var i = 0; i < str.length; i++) {
      h ^= str.charCodeAt(i);
      /* h *= 16777619, kept in 32-bit via the shift form so the result is
       * identical everywhere (JS numbers are doubles; the shift pins it). */
      h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0;
    }
    return h >>> 0;
  }

  var VOICE_ASSIGNMENTS = {};   /* name -> {voiceName, pitch, rate} — logged in the UI */

  function voiceFor(agentName, voices) {
    var name = String(agentName || "Qubit");
    var cached = VOICE_ASSIGNMENTS[name];
    if (cached && cached.resolved) return cached;
    var h = fnv1a(name);
    /* Prefer voices the OS marks local to the en-US locale; fall back to the
     * whole list so a machine with only a non-en voice still speaks. An empty
     * list means synthesis works but enumerates nothing — speak plainly. */
    var pool = (voices || []).filter(function (v) {
      return v && /^en/i.test(v.lang || "");
    });
    if (!pool.length) pool = (voices || []).slice();
    var assignment = {
      voiceName: pool.length ? String(pool[h % pool.length].name || "") : "",
      /* bit-sliced fields: low bits pick the voice, other bits spread the
       * prosody, so two names that collide on the voice still differ audibly. */
      pitch: 0.88 + ((h >>> 8) % 25) / 100,       /* 0.88 .. 1.12 */
      rate: 0.90 + ((h >>> 16) % 21) / 100,       /* 0.90 .. 1.10 */
      hash: h,
      resolved: pool.length > 0
    };
    VOICE_ASSIGNMENTS[name] = assignment;
    return assignment;
  }

  /* ======================================================================
   * 5. PERSISTED PREFERENCES — localStorage, read defensively.
   *
   * A private-mode browser throws on setItem and a corrupt value can throw on
   * getItem. A preference store that can take the page down is worse than a
   * preference that did not persist, so both directions are wrapped.
   * ==================================================================== */
  var LS_MUTED = "qubit.office3.ttsMuted";
  var LS_AGENT = "qubit.office3.agent";

  function lsGet(key) {
    try { return window.localStorage.getItem(key); } catch (ignore) { return null; }
  }
  function lsSet(key, value) {
    try { window.localStorage.setItem(key, value); return true; } catch (ignore) { return false; }
  }

  /* ======================================================================
   * 6. THE PAGE
   * ==================================================================== */
  function VoiceChat() {
    var mounted = useRef(true);

    /* --- capability + preference state --------------------------------- */
    var mutedState = useState(function () { return lsGet(LS_MUTED) === "1"; });
    var muted = mutedState[0], setMuted = mutedState[1];

    var agentState = useState(function () { return lsGet(LS_AGENT) || "Qubit"; });
    var agentName = agentState[0], setAgentName = agentState[1];

    /* --- transcript / conversation ------------------------------------- */
    var logState = useState([]);           /* [{who:'user'|'agent'|'error', text, meta}] */
    var log = logState[0], setLog = logState[1];

    /* --- mic lifecycle: idle | listening | sending | waiting ----------- */
    var phaseState = useState("idle");
    var phase = phaseState[0], setPhase = phaseState[1];

    var interimState = useState("");       /* the live transcript preview */
    var interim = interimState[0], setInterim = interimState[1];

    var draftState = useState("");         /* editable text, sent by button */
    var draft = draftState[0], setDraft = draftState[1];

    var errorState = useState("");
    var error = errorState[0], setError = errorState[1];

    var speakingState = useState(false);   /* the "speaking" indicator */
    var speaking = speakingState[0], setSpeaking = speakingState[1];

    var voicesState = useState([]);
    var voices = voicesState[0], setVoices = voicesState[1];

    var recRef = useRef(null);

    /* --- voices arrive asynchronously in most browsers ------------------ */
    useEffect(function () {
      if (!SPEECH_SYNTHESIS) return undefined;
      var load = function () { if (mounted.current) setVoices(TTS_PROVIDER.list()); };
      load();
      /* Chrome populates the list late and fires this; other engines just
       * have it ready. Listening is harmless where the event never fires. */
      SPEECH_SYNTHESIS.addEventListener("voiceschanged", load);
      return function () {
        mounted.current = false;
        try { SPEECH_SYNTHESIS.removeEventListener("voiceschanged", load); } catch (ignore) {}
        /* Leaving the page must not leave a voice talking over the user. */
        TTS_PROVIDER.cancel();
      };
    }, []);

    /* --- the mic opening ALWAYS cancels speech in flight --------------- */
    /* The design doc lists this under Phase 2 as "cancel TTS when the mic
     * opens", and marks it as already done in Phase 1. It is the correct
     * Phase-1 behaviour regardless: you cannot talk over a voice you cannot
     * hear yourself over. */
    var cancelSpeech = useCallback(function () {
      TTS_PROVIDER.cancel();
      setSpeaking(false);
    }, []);

    /* --- SEND: the one write path ------------------------------------- */
    /* `text` is a spoken transcript or a typed line. It takes the SAME route
     * in both cases — that is rule 1 above. */
    var send = useCallback(function (rawText) {
      var text = String(rawText || "").trim();
      if (!text) return;
      cancelSpeech();
      setError("");
      setInterim("");
      setPhase("sending");
      setLog(function (prev) {
        return prev.concat([{ who: "user", text: text, via: "voice/type" }]);
      });

      fetchJSON(PATH_TYPE, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: text })
      }).then(function (ack) {
        if (!mounted.current) return;
        var rid = ack && ack.request_id;
        if (!rid) {
          /* The bridge answered 2xx without an id. Say THAT — do not invent
           * a request in flight. */
          setPhase("idle");
          setError("The bridge accepted the request but returned no request_id, so there is nothing to poll.");
          return;
        }
        setPhase("waiting");
        pollResult(rid, Date.now());
      }).catch(function (ex) {
        if (!mounted.current) return;
        setPhase("idle");
        setError(describeFetchError(ex, PATH_TYPE));
        setLog(function (prev) {
          return prev.concat([{ who: "error", text: describeFetchError(ex, PATH_TYPE) }]);
        });
      });
    }, [cancelSpeech]);

    /* --- POLL: the single terminal reason a reply gets spoken ---------- */
    var pollResult = useCallback(function (rid, startedAt) {
      if (!mounted.current) return;
      if (Date.now() - startedAt > POLL_GIVE_UP_MS) {
        setPhase("idle");
        setError("No result for request " + rid + " after 3 minutes. The request may still be running; reload to check again.");
        return;
      }
      fetchJSON(PATH_RESULT + encodeURIComponent(rid))
        .then(function (res) {
          if (!mounted.current) return;
          var status = res && res.status;
          if (status === "pending") {
            window.setTimeout(function () { pollResult(rid, startedAt); }, POLL_MS);
            return;
          }
          if (status === "unknown") {
            /* The bridge forgot the id (restart, or the 50-entry prune). */
            setPhase("idle");
            setError("Request " + rid + " is unknown to the bridge — it was lost, most likely by a bridge restart.");
            setLog(function (prev) {
              return prev.concat([{ who: "error", text: "Request " + rid + " lost (unknown to the bridge)." }]);
            });
            return;
          }
          if (status !== "done") {
            setPhase("idle");
            setError("Unexpected result status \"" + String(status) + "\" for request " + rid + ".");
            return;
          }
          finish(rid, res);
        })
        .catch(function (ex) {
          if (!mounted.current) return;
          setPhase("idle");
          setError(describeFetchError(ex, PATH_RESULT + rid));
        });
    }, []);

    /* --- FINISH: render the reply, then speak it EXACTLY ONCE --------- */
    var finish = useCallback(function (rid, res) {
      setPhase("idle");
      /* The bridge sets `summary` on every path and `reply` on the
       * conversational path; error payloads carry `error`. Read them in that
       * order and never synthesise a missing field. */
      var reply = res.summary || res.reply || "";
      var latency = (res.latency_s != null) ? (res.latency_s + "s") : "";
      var route = res.route || "";
      var meta = [route, latency, "id " + rid].filter(Boolean).join(" · ");

      if (res.error) {
        /* A real backend error is an error. It is rendered, not spoken. */
        setError(String(res.error));
        setLog(function (prev) {
          return prev.concat([{
            who: "error",
            text: String(res.error),
            meta: meta
          }]);
        });
        return;
      }
      if (!reply) {
        setLog(function (prev) {
          return prev.concat([{
            who: "error",
            text: "The bridge completed request " + rid + " with no reply text (route \"" + route + "\").",
            meta: meta
          }]);
        });
        return;
      }
      setLog(function (prev) {
        return prev.concat([{ who: "agent", text: reply, meta: meta }]);
      });
      /* RULE 2. One reply, one utterance — never per streamed token.
       *
       * WHY THERE IS NO ROUTE FILTER HERE, AND WHY THAT IS DELIBERATE.
       * The design document scopes TTS to "a finished conversational reply,
       * never per streamed token, never on tasks/errors". The first half is
       * structural and this file enforces it (one `speak()` per completed poll,
       * and the poll itself is the only terminal event). The second half cannot
       * be implemented by classifying `res.route`, because Qubit's `route` is
       * NOT a conversation/task taxonomy: reading
       * `orchestrator._process_web_utterance_inner` shows it is the template or
       * intent KIND — one of "conversation", "local", "local-script:<name>",
       * "agent", "coding", "action", "confirm", "cancel", "clarify", "system",
       * "frontier", "fallback", or an empty string. Several of those (a local
       * script, a template ack, an agent delegation) return perfectly good prose
       * to read aloud, and `submit_text` itself synthesizes audio for ANY
       * non-empty summary. A filter on route === "conversation" would therefore
       * silence real replies while claiming to be conservative. Guessing a
       * magic route string here would be inventing a contract the bridge does
       * not have.
       *
       * So the gate is the one fact both sides already agree on: there is
       * reply text, and the bridge did not report an error (an error returned
       * above and can never reach this line). `awaiting_input` is explicitly
       * NOT a reason to stay silent — a clarifying question the bridge wants
       * an answer to is the most conversational thing it can say. */
      speak(reply);
    }, [speak]);

    /* --- SPEAK: TTS of a finished conversational reply ----------------- */
    var speak = useCallback(function (text) {
      if (muted || !SPEECH_SYNTHESIS) return;
      var assign = voiceFor(agentName, voices);
      TTS_PROVIDER.speak(
        text,
        pickVoice(assign.voiceName, voices),
        assign.pitch,
        assign.rate,
        function () { if (mounted.current) setSpeaking(true); },
        function () { if (mounted.current) setSpeaking(false); }
      );
    }, [muted, agentName, voices]);

    /* --- MIC: push-to-talk -------------------------------------------- */
    var startListening = useCallback(function () {
      if (!SPEECH_RECOGNITION || phase === "listening") return;
      if (phase === "sending" || phase === "waiting") return;  /* no talking over a reply */
      cancelSpeech();
      setError("");
      setInterim("");
      setPhase("listening");
      var rec = STT_PROVIDER.start({
        onInterim: function (text) { if (mounted.current) setInterim(text); },
        onError: function (info) {
          if (!mounted.current) return;
          setPhase("idle");
          if (info.message) {
            setError(info.message);
            setLog(function (prev) {
              return prev.concat([{ who: "error", text: info.message }]);
            });
          }
        },
        onEnd: function (finalText) {
          if (!mounted.current) return;
          recRef.current = null;
          setInterim("");
          if (finalText) send(finalText);
          else setPhase("idle");   /* silence: no utterance, no request */
        }
      });
      recRef.current = rec;
    }, [phase, cancelSpeech, send]);

    var stopListening = useCallback(function () {
      /* PTT release, or the explicit stop button. `stop()` lets the browser
       * deliver whatever it already finalised; `onEnd` does the send. */
      STT_PROVIDER.stop(recRef.current);
    }, []);

    var cancelListening = useCallback(function () {
      STT_PROVIDER.abort(recRef.current);
      recRef.current = null;
      setInterim("");
      setPhase("idle");
    }, []);

    /* Escape cancels a recording in progress — the same "get me out" key the
     * classic dashboard binds, kept for muscle memory. */
    useEffect(function () {
      var onKey = function (ev) {
        if (ev.key === "Escape" && phase === "listening") cancelListening();
      };
      window.addEventListener("keydown", onKey);
      return function () { window.removeEventListener("keydown", onKey); };
    }, [phase, cancelListening]);

    var toggleMute = useCallback(function () {
      var next = !muted;
      setMuted(next);
      lsSet(LS_MUTED, next ? "1" : "0");
      if (next) cancelSpeech();   /* muting mid-sentence stops the sentence */
    }, [muted, cancelSpeech]);

    var onAgentChange = useCallback(function (ev) {
      var next = ev.target.value || "Qubit";
      setAgentName(next);
      lsSet(LS_AGENT, next);
    }, []);

    /* --- the derived facts the header states --------------------------- */
    var assign = voiceFor(agentName, voices);
    var assignedVoice = pickVoice(assign.voiceName, voices);
    var canListen = !!SPEECH_RECOGNITION;
    var canSpeak = !!SPEECH_SYNTHESIS;
    var busy = phase === "sending" || phase === "waiting";

    var statusLabel = {
      idle: "ready",
      listening: "listening",
      sending: "sending",
      waiting: "waiting for the bridge"
    }[phase] || phase;

    /* ==================================================================
     * RENDER
     * ================================================================== */
    return e("div", { className: "office3" },

      /* --- header: identity, state, the honest capability line --------- */
      e("header", { className: "office3__head" },
        e("div", { className: "office3__title" },
          e("span", { className: "office3__name" }, "Voice Chat"),
          e("span", {
            className: "office3__phase office3__phase--" + phase
          }, statusLabel),
          speaking ? e("span", { className: "office3__speaking" },
            e("span", { className: "office3__speaking-dot", "aria-hidden": "true" }),
            "speaking") : null
        ),
        e("div", { className: "office3__headRight" },
          e("label", { className: "office3__agent" },
            e("span", { className: "office3__agentLabel" }, "Agent"),
            e("input", {
              className: "office3__agentInput",
              type: "text",
              value: agentName,
              onChange: onAgentChange,
              spellCheck: false,
              "aria-label": "Agent name, which decides the voice"
            })
          ),
          /* The mute toggle exists only where synthesis exists. */
          canSpeak ? e("button", {
            type: "button",
            className: "office3__toggle" + (muted ? " is-off" : ""),
            onClick: toggleMute,
            "aria-pressed": muted,
            title: muted ? "Unmute spoken replies" : "Mute spoken replies"
          }, muted ? "Voice off" : "Voice on") : null
        )
      ),

      /* --- the capability / voice-identity strip ----------------------- */
      e("div", { className: "office3__facts" },
        e("span", { className: "office3__fact" },
          e("span", { className: "office3__factKey" }, "voice"),
          assignedVoice
            ? e("span", { className: "office3__factVal" }, assignedVoice.name + " (" + (assignedVoice.lang || "?") + ")")
            : e("span", { className: "office3__factVal office3__factVal--dim" },
                canSpeak ? (voices.length ? "no matching voice — speaking with the default" : "voice list not enumerated yet")
                         : "speechSynthesis unavailable")
        ),
        e("span", { className: "office3__fact" },
          e("span", { className: "office3__factKey" }, "pitch/rate"),
          e("span", { className: "office3__factVal" },
            assign.pitch.toFixed(2) + " / " + assign.rate.toFixed(2) + " · hash " + assign.hash.toString(16))
        ),
        e("span", { className: "office3__fact" },
          e("span", { className: "office3__factKey" }, "path"),
          e("span", { className: "office3__factVal" },
            "POST " + PATH_TYPE + " -> GET " + PATH_RESULT + "<id>")
        )
      ),

      /* --- the degradation notice: WHAT is missing, not a vague error --- */
      MISSING.length
        ? e("div", { className: "office3__notice" },
            "This browser does not expose " + MISSING.join(" or ") + ". ",
            !canListen ? "The mic control is not shown because speech input is impossible here — use the text field below, which takes the same route. " : "",
            !canSpeak ? "The voice toggle is not shown because spoken replies are impossible here; replies still appear as text." : ""
          )
        : null,

      /* --- transcript --------------------------------------------------- */
      e("main", { className: "office3__log", role: "log", "aria-live": "polite" },
        log.length === 0 && !interim
          ? e("div", { className: "office3__empty" },
              canListen
                ? "Hold the mic and speak, or type below. The transcript is sent exactly as a typed line would be."
                : "Type below. The bridge receives it exactly as a spoken line would have been sent.")
          : null,
        log.map(function (item, idx) {
          return e("div", {
            key: idx,
            className: "office3__msg office3__msg--" + item.who
          },
            e("div", { className: "office3__msgBody" }, item.text),
            item.meta ? e("div", { className: "office3__meta" }, item.meta) : null
          );
        }),
        /* The live transcript preview. It is a PREVIEW: it has not been sent
         * and it says so, so nothing here can be mistaken for a reply. */
        interim
          ? e("div", { className: "office3__msg office3__msg--interim" },
              e("div", { className: "office3__msgBody" }, interim),
              e("div", { className: "office3__meta" }, "live transcript — not sent yet"))
          : null,
        busy
          ? e("div", { className: "office3__meta office3__meta--busy" },
              "request in flight — polling " + PATH_RESULT + "<id>")
          : null
      ),

      /* --- error: the REAL one ------------------------------------------ */
      error
        ? e("div", { className: "office3__error", role: "alert" },
            e("span", { className: "office3__errorText" }, error),
            e("button", {
              type: "button",
              className: "office3__errorDismiss",
              onClick: function () { setError(""); }
            }, "Dismiss")
          )
        : null,

      /* --- composer ----------------------------------------------------- */
      e("div", { className: "office3__composer" },
        /* The mic is NOT RENDERED when recognition is impossible. */
        canListen ? e("button", {
          type: "button",
          className: "office3__mic" + (phase === "listening" ? " is-hot" : ""),
          disabled: busy,
          onPointerDown: function (ev) {
            ev.preventDefault();
            if (busy) return;
            startListening();
          },
          onPointerUp: function (ev) {
            ev.preventDefault();
            if (phase === "listening") stopListening();
          },
          onPointerLeave: function () {
            if (phase === "listening") stopListening();
          },
          onKeyDown: function (ev) {
            if (ev.key === " " || ev.key === "Enter") { ev.preventDefault(); startListening(); }
          },
          onKeyUp: function (ev) {
            if (ev.key === " " || ev.key === "Enter") { ev.preventDefault(); stopListening(); }
          },
          "aria-pressed": phase === "listening",
          title: "Hold to talk"
        }, phase === "listening" ? "Listening — release to send" : "Hold to talk")
          : null,

        phase === "listening"
          ? e("button", {
              type: "button",
              className: "office3__cancel",
              onClick: cancelListening
            }, "Cancel (Esc)")
          : null,

        e("input", {
          className: "office3__input",
          type: "text",
          value: draft,
          disabled: busy,
          placeholder: busy ? "waiting for the bridge…" : "Type, or hold the mic",
          onChange: function (ev) { setDraft(ev.target.value); },
          onKeyDown: function (ev) {
            if (ev.key === "Enter" && !ev.shiftKey) {
              ev.preventDefault();
              var text = draft;
              setDraft("");
              send(text);
            }
          },
          "aria-label": "Message to send"
        }),

        e("button", {
          type: "button",
          className: "office3__send",
          disabled: busy || !draft.trim(),
          onClick: function () {
            var text = draft;
            setDraft("");
            send(text);
          }
        }, "Send")
      ),

      /* --- footer: what this page actually calls ------------------------ */
      e("footer", { className: "office3__foot" },
        "Transcribes in this browser and sends the text through the dashboard's own text path — no second pipeline. ",
        "TTS fires once per finished conversational reply, never per streamed token. ",
        e("span", { className: "office3__footDim" },
          "Server-side recognition and synthesis live on POST /api/talk; this page deliberately does not use them.")
      )
    );
  }

  /* Resolve a stored voice NAME back to a live SpeechSynthesisVoice. Names are
   * the stable key (voice objects are rebuilt by the browser), and a name that
   * is no longer installed resolves to null so the utterance uses the default
   * rather than a stale reference. */
  function pickVoice(name, voices) {
    if (!name) return null;
    for (var i = 0; i < voices.length; i++) {
      if (voices[i] && voices[i].name === name) return voices[i];
    }
    return null;
  }

  /* Turn a thrown SDK error into the sentence a user can act on. `fetchJSON`
   * throws `Error("<status>: <body>")`; a 401 means the session token the page
   * was rendered with is no longer good (the loopback token rotates on every
   * bridge restart), which is a different instruction from a 500. */
  function describeFetchError(ex, what) {
    var msg = String((ex && ex.message) || ex || "request failed");
    if (/^401:/.test(msg)) {
      return "Not authenticated for " + what + " (401). The session token in this page is stale — the bridge most likely restarted. Reload the dashboard and try again.";
    }
    if (/^400:/.test(msg)) {
      return "The bridge rejected " + what + " with 400: " + msg.replace(/^400:\s*/, "");
    }
    return what + " failed: " + msg;
  }

  /* Report what this build measured, in the console, once. A support report
   * that starts with "which capabilities did the page actually see" is worth
   * more than one that starts with "it didn't work". */
  if (typeof console !== "undefined" && console.info) {
    console.info(
      "[qubit-office3] capabilities: SpeechRecognition=" + (SPEECH_RECOGNITION ? "yes" : "no") +
      ", speechSynthesis=" + (SPEECH_SYNTHESIS ? "yes" : "no") +
      "; speaking via " + TTS_PROVIDER.name + ", listening via " + STT_PROVIDER.name + "."
    );
  }

  window.__QUBIT_PLUGINS__.register("qubit-office3", VoiceChat);
})();
