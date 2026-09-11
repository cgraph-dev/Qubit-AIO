/* Qubit Chat — overlay plugin bundle (dependency-free IIFE).
 * Uses only window.__HERMES_PLUGIN_SDK__ (React, hooks, fetchJSON).
 * Backend: POST /api/qubit/chat -> voicebridge.agent.turn (same module as
 * CLI text and voice turns), with e-stop, redaction, and audit.
 * Dashboard voice control: mic button drives the browser SpeechRecognition
 * (where available) and sends the transcript through the SAME send path as
 * typed input — voice executes backend turns, never describes them. Where
 * the browser offers no speech recognition, the button reports a typed
 * unsupported state pointing at the working counterparts (CLI `qubit`
 * text/voice PTT). */
(function () {
  var SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK) return;
  var React = SDK.React;
  var hooks = SDK.hooks;
  var fetchJSON = SDK.fetchJSON;
  var C = SDK.components || {};
  var e = React.createElement;

  function sendTurn(text, session, busy, setBusy, setErr, setMsgs) {
    if (!text || busy) return;
    setBusy(true); setErr("");
    var user = {role: "user", text: text};
    setMsgs(function (prev) { return prev.concat([user]); });
    fetchJSON("/api/qubit/chat", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({text: text, session: session}),
    }).then(function (res) {
      if (res && res.ok) {
        setMsgs(function (prev) {
          return prev.concat([{role: "assistant", text: res.reply,
            meta: "model=" + (res.receipt && res.receipt.model) +
                  " elapsed=" + res.elapsed_ms + "ms"}]);
        });
      } else {
        setErr("[" + (res && res.code) + "] " + ((res && (res.error || res.detail)) || "turn failed"));
      }
      setBusy(false);
    }).catch(function (ex) {
      setErr(String((ex && ex.message) || ex));
      setBusy(false);
    });
  }

  function QubitChat() {
    var st = hooks.useState([]);
    var msgs = st[0], setMsgs = st[1];
    var ist = hooks.useState("");
    var input = ist[0], setInput = ist[1];
    var sst = hooks.useState("dashboard-chat");
    var session = sst[0], setSession = sst[1];
    var bst = hooks.useState(false);
    var busy = bst[0], setBusy = bst[1];
    var est = hooks.useState("");
    var err = est[0], setErr = est[1];
    var vst = hooks.useState("idle");
    var voiceState = vst[0], setVoiceState = vst[1];
    var recRef = hooks.useRef(null);
    var heardRef = hooks.useRef("");
    var watchdogRef = hooks.useRef(null);

    function send() {
      var text = (input || "").trim();
      if (!text || busy) return;
      setInput("");
      sendTurn(text, session, busy, setBusy, setErr, setMsgs);
    }

    function stopVoice() {
      try {
        if (recRef.current) recRef.current.stop();
      } catch (ex) { /* already stopped */ }
    }

    function clearVoiceWatchdog() {
      if (watchdogRef.current) {
        clearTimeout(watchdogRef.current);
        watchdogRef.current = null;
      }
    }

    function armVoiceWatchdog(rec) {
      /* Sliding 60s watchdog: the engine normally ends the session itself
       * (final result, no-speech, error). If it goes silent — no mic,
       * headless browser, permission limbo — stop and say so in typed
       * form instead of hanging in "listening" forever. Reset on every
       * result so long utterances keep working. */
      clearVoiceWatchdog();
      watchdogRef.current = setTimeout(function () {
        watchdogRef.current = null;
        try { rec.stop(); } catch (ex) { /* noop */ }
        recRef.current = null;
        setVoiceState("idle");
        setErr("[VOICE_TIMEOUT] No speech heard within 60s — the microphone " +
          "may be unavailable. Type below, or use CLI `qubit` voice instead.");
      }, 60000);
    }

    function startVoice() {
      if (busy || voiceState === "listening") return;
      var SR = (typeof window !== "undefined") &&
        (window.SpeechRecognition || window.webkitSpeechRecognition);
      if (!SR) {
        setVoiceState("unsupported");
        setErr("[VOICE_UNSUPPORTED] This browser offers no speech recognition. " +
          "Type below, or use CLI `qubit` text / push-to-talk voice instead.");
        return;
      }
      setErr("");
      var rec;
      try {
        rec = new SR();
      } catch (ex) {
        setVoiceState("unsupported");
        setErr("[VOICE_UNSUPPORTED] Could not start speech recognition: " +
          String((ex && ex.message) || ex));
        return;
      }
      rec.lang = "en-US";
      rec.interimResults = true;
      rec.maxAlternatives = 1;
      recRef.current = rec;
      heardRef.current = "";
      setVoiceState("listening");
      armVoiceWatchdog(rec);
      rec.onresult = function (ev) {
        armVoiceWatchdog(rec);
        var interim = "", fin = "";
        for (var i = ev.resultIndex; i < ev.results.length; i++) {
          var r = ev.results[i];
          if (r.isFinal) fin += r[0].transcript;
          else interim += r[0].transcript;
        }
        var heard = (fin || interim).trim();
        if (heard) { heardRef.current = heard; setInput(heard); }
        if (fin.trim()) {
          try { rec.stop(); } catch (ex2) { /* noop */ }
        }
      };
      rec.onerror = function (ev) {
        var code = ev && ev.error;
        clearVoiceWatchdog();
        recRef.current = null;
        setVoiceState("idle");
        if (code === "not-allowed" || code === "service-not-allowed") {
          setErr("[VOICE_DENIED] Microphone blocked. Allow mic access, or type " +
            "below / use CLI `qubit` voice instead.");
        } else if (code === "no-speech" || code === "aborted") {
          setErr("[VOICE_NO_SPEECH] No speech heard — try again, or type below.");
        } else {
          setErr("[VOICE_ERROR] Speech recognition failed (" + code + "). " +
            "Type below, or use CLI `qubit` voice instead.");
        }
      };
      rec.onend = function () {
        clearVoiceWatchdog();
        recRef.current = null;
        /* onend fires after stop(); the final transcript is already in
         * heardRef (set by onresult), so send it through the SAME backend
         * turn path as typed input — voice executes, never describes. */
        var said = (heardRef.current || "").trim();
        heardRef.current = "";
        setVoiceState("idle");
        if (said) {
          setInput("");
          sendTurn(said, session, false, setBusy, setErr, setMsgs);
        }
      };
      try {
        rec.start();
      } catch (ex) {
        recRef.current = null;
        setVoiceState("idle");
        setErr("[VOICE_ERROR] Could not start the microphone: " +
          String((ex && ex.message) || ex));
      }
    }

    function toggleVoice() {
      if (voiceState === "listening") stopVoice();
      else startVoice();
    }

    hooks.useEffect(function () {
      return function () {
        clearVoiceWatchdog();
        try { if (recRef.current) recRef.current.abort(); } catch (ex) { /* noop */ }
        recRef.current = null;
      };
    }, []);

    var voiceLabel = voiceState === "listening" ? "Stop voice input" : "Voice input";
    var voiceText = voiceState === "listening" ? "Listening… (click to stop)" : "Voice";

    return e("div", {style: {display: "flex", flexDirection: "column", gap: 12, padding: 16, maxWidth: 860}},
      e("h2", null, "Qubit Chat"),
      e("p", {style: {opacity: 0.75}},
        "Native agent turns on the shared Qubit backend (same module as CLI and voice). ",
        "The built-in Chat tab keeps the Hermes xterm surface; raw PTY attach is refused by Qubit shell policy."),
      e("label", null, "Session ",
        e(C.Input || "input", {value: session, onChange: function (ev) { setSession(ev.target.value); },
          style: {maxWidth: 260}})),
      e("div", {style: {display: "flex", flexDirection: "column", gap: 8, minHeight: 120}},
        msgs.length === 0
          ? e("p", {style: {opacity: 0.6}}, "No messages yet — ask something.")
          : msgs.map(function (m, i) {
              return e("div", {key: i, style: {whiteSpace: "pre-wrap"}},
                e("strong", null, m.role === "user" ? "You: " : "Qubit: "),
                e("span", null, m.text),
                m.meta ? e("div", {style: {fontSize: 12, opacity: 0.6}}, m.meta) : null);
            })),
      err ? e("div", {role: "alert", style: {color: "#e5484d"}}, err) : null,
      e("div", {style: {display: "flex", gap: 8}},
        e(C.Input || "input", {value: input, disabled: busy,
          "aria-label": "Message Qubit",
          onChange: function (ev) { setInput(ev.target.value); },
          onKeyDown: function (ev) { if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); send(); } },
          placeholder: "Message Qubit… (Enter to send, or use Voice)", style: {flex: 1}}),
        e(C.Button || "button", {onClick: toggleVoice, disabled: busy,
          "aria-label": voiceLabel,
          title: "Speak instead of typing (browser speech recognition; same backend turn)"},
          voiceText),
        e(C.Button || "button", {onClick: send, disabled: busy},
          busy ? "Sending…" : "Send"))
    );
  }

  window.__HERMES_PLUGINS__.register("qubit-chat", QubitChat);

  /* Slot widget: explicit pointer on the built-in /chat tab. The xterm PTY
   * mirror cannot attach under Qubit shell policy (typed PTY_NO_SHELL from
   * the backend); this banner directs operators to the working path. */
  function ChatNotice() {
    return e("div", {role: "note", style: {margin: 12, padding: 10,
      border: "1px solid", borderRadius: 8, fontSize: 13}},
      e("strong", null, "Qubit note: "),
      e("span", null, "the Hermes xterm session needs a raw PTY, which Qubit " +
        "refuses by shell policy. Use the Qubit Chat tab for native agent " +
        "turns on the same backend as CLI and voice."));
  }
  if (window.__HERMES_PLUGINS__ && window.__HERMES_PLUGINS__.registerSlot) {
    window.__HERMES_PLUGINS__.registerSlot("qubit-chat", "chat:top", ChatNotice);
  }
})();
