// ../../apps/desktop/src/plugins/qubit-tv/plugin.jsx
import { host, PALETTE_AREA, TITLEBAR_AREAS } from "@qubit/plugin-sdk";
import { useEffect, useRef, useState } from "react";
import { jsx, jsxs } from "react/jsx-runtime";
var MASCOT = "/qubit.png";
var services = [
  ["youtube", "YouTube", "https://www.youtube.com/", false],
  ["tv", "YouTube TV", "https://tv.youtube.com/", true],
  ["netflix", "Netflix", "https://www.netflix.com/", true],
  ["prime-video", "Prime Video", "https://www.primevideo.com/", true],
  ["disney-plus", "Disney+", "https://www.disneyplus.com/", true],
  ["hulu", "Hulu", "https://www.hulu.com/", true],
  ["hbo-max", "HBO Max", "https://www.hbomax.com/", true],
  ["apple-tv", "Apple TV+", "https://tv.apple.com/", true],
  ["peacock", "Peacock", "https://www.peacocktv.com/", true],
  ["paramount-plus", "Paramount+", "https://www.paramountplus.com/", true]
];
var DRM_UNAVAILABLE = "Protected playback (Widevine/CDM) is unavailable in this build. This service can open in the native player, but sign-in and protected playback are not supported here.";
var h = jsx;
var s = (type, props, ...children) => {
  const { key, ...rest } = props || {};
  return jsxs(type, { ...rest, children }, key);
};
var button = (label, onClick, disabled = false, aria = void 0) => h("button", { type: "button", onClick, disabled, "aria-label": aria, children: label });
var STYLES = `.qubit-tv-shell{height:100%;overflow:auto;background:var(--ui-bg-app,#101117);color:var(--ui-text-primary,#f4f4f6);font:13px/1.45 Inter,system-ui,sans-serif;padding:16px;box-sizing:border-box}.qubit-tv-shell button{border:1px solid #ffffff26;border-radius:9px;background:#ffffff0d;color:inherit;padding:7px 10px;cursor:pointer}.qubit-tv-shell button:disabled{opacity:.4;cursor:not-allowed}.qubit-tv-head,.qubit-tv-agent,.qubit-tv-controls,.qubit-tv-grid-head,.qubit-tv-toolrow{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.qubit-tv-head{border-bottom:1px solid #ffffff18;padding-bottom:12px}.qubit-tv-head>div{display:grid;flex:1}.qubit-tv-head small,.qubit-tv-agent small,.qubit-tv-note{color:#a6a8b6}.qubit-tv-avatar{width:38px;height:38px;object-fit:cover;border-radius:50%;border:2px solid #fb68a8}.qubit-tv-surface,.qubit-tv-surface-placeholder{display:block;width:100%;height:min(43vh,420px);min-height:200px;margin:12px 0;border-radius:14px;background:#06070a;overflow:hidden}.qubit-tv-surface-placeholder{display:grid;place-items:center;color:#a6a8b6}.qubit-tv-grid-wrap,.qubit-tv-agent{border:1px solid #ffffff17;border-radius:14px;background:#ffffff06;padding:12px;margin-top:12px}.qubit-tv-grid-head h3{margin:0;flex:1}.qubit-tv-grid-head input,.qubit-tv-toolrow input{background:#0005;color:inherit;border:1px solid #ffffff26;border-radius:8px;padding:8px}.qubit-tv-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(145px,1fr));gap:8px;margin:12px 0}.qubit-tv-card{display:flex;justify-content:space-between;align-items:center;padding:10px;border-radius:11px;background:linear-gradient(135deg,#f725851f,#4361ee1a);border:1px solid #ffffff16}.qubit-tv-card.active{border-color:#f72585aa}.qubit-tv-card .qubit-tv-open{border:0;background:transparent;font-weight:650}.qubit-tv-agent{align-items:flex-start}.qubit-tv-agent>div:first-child{display:grid;min-width:160px;margin-right:auto}.qubit-tv-agent p{flex-basis:100%;margin:4px 0;color:#c5c7d2}.qubit-tv-drm{flex-basis:100%;margin:2px 0;padding:8px 10px;border-radius:9px;border:1px solid #f5a52455;background:#f5a52414;color:#f7c873}.qubit-tv-jobs{width:100%;list-style:none;padding:0}.qubit-tv-jobs li{display:flex;align-items:center;gap:8px;padding:7px 0;border-top:1px solid #ffffff16}.qubit-tv-required{max-width:520px;margin:12vh auto;text-align:center;padding:28px;border:1px solid #ffffff1e;border-radius:20px;background:linear-gradient(140deg,#e6308a21,#5842ef24)}.qubit-tv-required p{color:#b9bbc8}.qubit-tv-mascot{width:88px;height:88px;object-fit:cover;border-radius:50%;border:3px solid #fb68a8}.qubit-tv-dialog{position:fixed;inset:0;background:#0009;display:grid;place-items:center;z-index:20}.qubit-tv-dialog>div{max-width:440px;padding:22px;border-radius:16px;background:#20212a;border:1px solid #ffffff2a}`;
function UnsupportedPane({ reason = "This desktop host does not provide QubitTV media support." }) {
  return s("section", { className: "qubit-tv-shell" }, h("style", { children: STYLES }), s("div", { className: "qubit-tv-required" }, h("img", { src: MASCOT, alt: "", className: "qubit-tv-mascot" }), h("h2", { children: "QubitTV needs host media support" }), h("p", { children: reason })));
}
function MediaPane({ media, ctx }) {
  const player = media.usePlayer();
  const snap = player.snapshot;
  const [filter, setFilter] = useState("");
  const [favorites, setFavorites] = useState(() => ctx.storage.get("favorites", []));
  const [favoritesOnly, setFavoritesOnly] = useState(false);
  const [message, setMessage] = useState("");
  const [context, setContext] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [helperCapabilities, setHelperCapabilities] = useState(null);
  const [busy, setBusy] = useState(false);
  const [askBusy, setAskBusy] = useState(false);
  const askRequest = useRef(null);
  const [setupBusy, setSetupBusy] = useState(false);
  const [clearConfirm, setClearConfirm] = useState(false);
  const [seek, setSeek] = useState("");
  const [now, setNow] = useState(Date.now());
  const generation = useRef(0);
  const requestKey = `${snap?.guestId ?? "none"}:${snap?.source ?? "none"}:${snap?.safeOrigin ?? "none"}:${player.linked}`;
  const keyRef = useRef(requestKey);
  keyRef.current = requestKey;
  const available = Boolean(player.available);
  const canAgent = available && player.linked;
  const opened = Boolean(snap && snap.status !== "closed" && snap.source);
  const service = services.find((row) => row[0] === snap?.source);
  const protectedService = Boolean(service && service[3]);
  const drmUnavailable = Boolean(snap?.runtime?.state === "unsupported" || protectedService);
  const contextCurrent = Boolean(context && context.source === snap?.source && Date.parse(context.expiresAt) > now);
  useEffect(() => () => {
    generation.current += 1;
  }, []);
  useEffect(() => {
    generation.current += 1;
    askRequest.current = null;
    setAskBusy(false);
    setContext(null);
    setJobs([]);
    setMessage("");
    setNow(Date.now());
    setBusy(false);
    setClearConfirm(false);
  }, [requestKey]);
  useEffect(() => {
    if (!context) return void 0;
    const delay = Math.max(0, Date.parse(context.expiresAt) - Date.now());
    const timer = setTimeout(() => {
      setContext(null);
      setMessage("Media context expired. Refresh it before requesting a transcript or download.");
    }, delay);
    return () => clearTimeout(timer);
  }, [context]);
  if (!available) return h(UnsupportedPane, { reason: "The host media capability is present, but its player bridge is unavailable." });
  const command = async (value) => {
    try {
      await player.command(value);
      setMessage("");
    } catch {
      setMessage("The player action failed. Try again.");
    }
  };
  const openService = async (id) => {
    const row = services.find((item) => item[0] === id);
    try {
      await player.command({ type: "open", source: id });
      setMessage(row?.[3] ? DRM_UNAVAILABLE : "");
    } catch {
      setMessage("The player action failed. Try again.");
    }
  };
  const agentRequest = async (operation) => {
    if (!canAgent) {
      setMessage("Link this session before sharing media context.");
      return null;
    }
    if (["transcript.start", "download.start", "control"].includes(operation.type) && !contextCurrent) {
      setMessage("Media context is missing or expired. Refresh it before continuing.");
      return null;
    }
    const requestGeneration = generation.current, thisKey = keyRef.current;
    setBusy(true);
    setMessage("");
    try {
      const response = await player.request(operation);
      if (requestGeneration !== generation.current || thisKey !== keyRef.current || !player.linked) return null;
      if (response.context) {
        setContext(response.context);
        setNow(Date.now());
      }
      if (response.jobs) setJobs(response.jobs);
      if (response.job) setJobs((previous) => [response.job, ...previous.filter((job) => job.id !== response.job.id)]);
      if (response.capabilities) setHelperCapabilities(response.capabilities);
      if (response.capabilities?.reason) setMessage(response.capabilities.available ? "Media helpers are ready." : response.capabilities.reason);
      if (response.error) {
        if (["transcript.start", "download.start", "control"].includes(operation.type)) setContext(null);
        setMessage(response.error.message);
      } else if (!response.context && !response.job && !response.jobs && !response.capabilities) setMessage("Request complete.");
      return response;
    } catch {
      if (requestGeneration === generation.current && thisKey === keyRef.current) {
        if (["transcript.start", "download.start", "control"].includes(operation.type)) setContext(null);
        setMessage(["transcript.start", "download.start", "control"].includes(operation.type) ? "Media context may have changed. Refresh it before continuing." : "Qubit could not complete the media request.");
      }
      return null;
    } finally {
      if (requestGeneration === generation.current && thisKey === keyRef.current) setBusy(false);
    }
  };
  const toggleLink = async () => {
    if (!available || !player.canLink || busy) return;
    const before = player.linked;
    setBusy(true);
    setMessage("");
    try {
      if (before) await player.unlink();
      else await player.link();
      generation.current += 1;
      setContext(null);
      setJobs([]);
      setNow(Date.now());
      setMessage(before ? "Session unlinked." : "Session linked. Refresh context to inspect the current page.");
    } catch {
      setMessage("Could not update the session link.");
    } finally {
      setBusy(false);
    }
  };
  const askQubit = async () => {
    if (!canAgent || askRequest.current) return;
    const request = { generation: generation.current, key: keyRef.current };
    askRequest.current = request;
    setAskBusy(true);
    setMessage("");
    try {
      const added = await player.seedPrompt("Help me with what I am watching.");
      if (request.generation !== generation.current || request.key !== keyRef.current || !player.linked) return;
      setMessage(added ? "Prompt added to the chat." : "Could not add the prompt.");
    } catch {
      if (request.generation === generation.current && request.key === keyRef.current && player.linked) setMessage("Could not add the prompt.");
    } finally {
      if (askRequest.current === request) {
        askRequest.current = null;
        setAskBusy(false);
      }
    }
  };
  const setupHelpers = async () => {
    if (!available || !player.linked || setupBusy) {
      setMessage("Link this session before setting up media helpers.");
      return;
    }
    setSetupBusy(true);
    setMessage("");
    try {
      const result = await player.setupMediaHelpers();
      setHelperCapabilities(result);
      setMessage(result.available ? "Media helpers are ready." : result.reason || "Media helpers are unavailable.");
    } catch {
      setMessage("Media helper setup failed.");
    } finally {
      setSetupBusy(false);
    }
  };
  const clearData = async () => {
    if (!available || !player.linked || busy) {
      setMessage("Link this session before clearing player data.");
      return;
    }
    setBusy(true);
    setClearConfirm(false);
    setMessage("");
    try {
      await player.clearLocalData();
      generation.current += 1;
      setContext(null);
      setJobs([]);
      setMessage("Local player data cleared.");
    } catch {
      setMessage("Could not clear local player data.");
    } finally {
      setBusy(false);
    }
  };
  const reveal = async (id) => {
    if (!canAgent || busy) {
      setMessage("Link this session before revealing a media file.");
      return;
    }
    setBusy(true);
    try {
      if (!await player.revealMediaJob(id)) setMessage("Could not reveal that media file.");
    } catch {
      setMessage("Could not reveal that media file.");
    } finally {
      setBusy(false);
    }
  };
  const toggleFavorite = (id) => {
    const next = favorites.includes(id) ? favorites.filter((x) => x !== id) : [...favorites, id];
    setFavorites(next);
    ctx.storage.set("favorites", next);
  };
  const filtered = services.filter((row) => row[1].toLowerCase().includes(filter.trim().toLowerCase()));
  const seekTo = () => {
    const positionSeconds = Number(seek);
    if (Number.isFinite(positionSeconds) && positionSeconds >= 0 && opened) void command({ type: "media-control", action: "seek", positionSeconds });
    else setMessage("Enter a valid seek position.");
  };
  return s(
    "section",
    { className: "qubit-tv-shell" },
    h("style", { children: STYLES }),
    s(
      "header",
      { className: "qubit-tv-head" },
      h("img", { src: MASCOT, alt: "Qubit", className: "qubit-tv-avatar" }),
      s("div", {}, h("strong", { children: "QubitTV" }), h("small", { children: service ? `Now watching \xB7 ${service[1]}` : "Your streaming dashboard" })),
      button("Dock", () => command({ type: "dock" }), !available),
      button("Pop out", () => command({ type: "popout" }), !available),
      button(snap?.pinned ? "Unpin" : "Pin", () => command({ type: "pin", pinned: !snap?.pinned }), !opened),
      button(snap?.fullscreen ? "Exit fullscreen" : "Fullscreen", () => command({ type: "fullscreen", fullscreen: !snap?.fullscreen }), !opened),
      button("Close", () => command({ type: "close" }), !opened)
    ),
    media.Surface ? h(media.Surface, { className: "qubit-tv-surface", hidden: !opened || clearConfirm }) : h("div", { className: "qubit-tv-surface-placeholder", children: opened ? "Player surface is unavailable." : "Choose a service to begin." }),
    s("nav", { className: "qubit-tv-controls" }, button("Back", () => command({ type: "back" }), !snap?.canGoBack), button("Forward", () => command({ type: "forward" }), !snap?.canGoForward), button("Home", () => command({ type: "home" }), !opened), button(snap?.paused ? "Play" : "Pause", () => command({ type: "media-control", action: snap?.paused ? "play" : "pause" }), !opened), button(snap?.muted ? "Unmute" : "Mute", () => command({ type: "media-control", action: snap?.muted ? "unmute" : "mute" }), !opened), h("input", { type: "number", min: 0, value: seek, placeholder: "Seek (seconds)", onChange: (e) => setSeek(e.target.value), "aria-label": "Seek position in seconds" }), button("Seek", seekTo, !opened)),
    s(
      "div",
      { className: "qubit-tv-grid-wrap" },
      s("div", { className: "qubit-tv-grid-head" }, h("h3", { children: "Streaming services" }), h("input", { type: "search", value: filter, placeholder: "Find a service", onChange: (e) => setFilter(e.target.value) }), button(favoritesOnly ? "Show all services" : "Show favorites", () => setFavoritesOnly((value) => !value))),
      s("div", { className: "qubit-tv-grid" }, ...filtered.filter((row) => !favoritesOnly || favorites.includes(row[0])).map(([id, label]) => s("article", { className: `qubit-tv-card ${id === snap?.source ? "active" : ""}`, key: id }, h("button", { className: "qubit-tv-open", onClick: () => openService(id), children: label }), button(favorites.includes(id) ? "\u2605" : "\u2606", () => toggleFavorite(id), false, `Toggle ${label} favorite`)))),
      h("small", { className: "qubit-tv-note", children: "Services open in the host player. Widevine/CDM-protected playback is unavailable in this build and is never claimed." })
    ),
    s(
      "section",
      { className: "qubit-tv-agent" },
      s("div", {}, h("strong", { children: player.linked ? "Session linked" : "Session not linked" }), h("small", { children: "Context is shared only after you link this session." })),
      button(player.linked ? "Unlink session" : "Link session", toggleLink, !player.canLink || busy),
      button("Refresh context", () => agentRequest({ type: "context" }), !canAgent || busy),
      button(askBusy ? "Adding prompt\u2026" : "Ask Qubit", askQubit, !canAgent || askBusy),
      button("Transcript", () => contextCurrent && agentRequest({ type: "transcript.start", contextToken: context.contextToken }), !canAgent || busy || !contextCurrent || context?.metadata?.identity !== "youtube-watch" || helperCapabilities?.available === false),
      button("Download media", () => contextCurrent && agentRequest({ type: "download.start", contextToken: context.contextToken }), !canAgent || busy || !contextCurrent || context?.metadata?.identity !== "youtube-watch" || helperCapabilities?.available === false),
      button("List jobs", () => agentRequest({ type: "jobs.list" }), !canAgent || busy),
      button(setupBusy ? "Setting up\u2026" : "Set up media helpers", setupHelpers, !canAgent || setupBusy),
      button("Clear local data", () => setClearConfirm(true), !canAgent || busy),
      drmUnavailable ? h("p", { className: "qubit-tv-drm", role: "status", children: DRM_UNAVAILABLE }) : null,
      contextCurrent ? h("p", { children: `Context expires ${new Date(context.expiresAt).toLocaleTimeString()}. Refresh after navigation.` }) : context ? h("p", { children: "Context expired. Refresh before requesting media actions." }) : null,
      jobs.length ? s("ul", { className: "qubit-tv-jobs" }, ...jobs.map((job) => s("li", { key: job.id }, `${job.kind} \xB7 ${job.state}${job.progress === null ? "" : ` \xB7 ${Math.round(job.progress * 100)}%`}`, job.error ? ` \xB7 ${job.error.message}` : "", job.transcript ? h("details", {}, h("summary", { children: "View transcript" }), ...job.transcript.segments.map((segment, i) => h("p", { key: i, children: `${segment.startSeconds.toFixed(1)}s \xB7 ${segment.text.text}` }))) : null, job.state === "completed" && job.artifact ? button("Reveal file", () => reveal(job.id), !canAgent || busy) : null, ["queued", "running"].includes(job.state) ? button("Cancel", () => agentRequest({ type: "job.cancel", jobId: job.id }), !canAgent || busy) : null))) : null,
      message ? h("p", { role: "status", "aria-live": "polite", children: message }) : null
    ),
    clearConfirm ? s("div", { className: "qubit-tv-dialog", role: "alertdialog", "aria-modal": true, "aria-labelledby": "qubit-tv-clear-title" }, s("div", {}, h("h3", { id: "qubit-tv-clear-title", children: "Clear QubitTV local data?" }), h("p", { children: "This removes local player data saved by QubitTV." }), button("Cancel", () => setClearConfirm(false)), button("Clear local data", clearData, busy))) : null
  );
}
function PaneEntry({ ctx }) {
  const media = ctx.media;
  if (!media || typeof media.usePlayer !== "function" || !media.Surface) return h(UnsupportedPane, {});
  return h(MediaPane, { media, ctx });
}
var plugin_default = {
  id: "qubit-tv",
  name: "QubitTV",
  description: "A streaming dashboard connected to the desktop media host.",
  defaultEnabled: false,
  capabilities: ["media.player"],
  register(ctx) {
    let closeWorkspace = null;
    const openPlayer = () => {
      if (typeof host.openWorkspace !== "function") {
        host.notify({ kind: "info", message: "This QubitTV desktop does not support plugin workspaces." });
        return;
      }
      closeWorkspace = host.openWorkspace("qubit-tv:dashboard", {
        title: "QubitTV",
        minWidth: "28rem",
        dock: { pane: "workspace", pos: "right" },
        render: () => h(PaneEntry, { ctx }),
        onClose: () => {
          closeWorkspace = null;
        }
      });
    };
    ctx.register({
      id: "open",
      area: PALETTE_AREA,
      data: {
        id: "qubit-tv.open",
        label: "Open QubitTV",
        keywords: ["qubittv", "qubit-tv", "streaming", "player", "workspace"],
        run: openPlayer
      }
    });
    ctx.register({
      id: "reopen",
      area: TITLEBAR_AREAS.right,
      render: () => h("button", {
        type: "button",
        title: "Open QubitTV",
        "aria-label": "Open QubitTV",
        onClick: openPlayer,
        children: "QubitTV"
      })
    });
    ctx.onDispose(() => {
      const close = closeWorkspace;
      closeWorkspace = null;
      close?.();
    });
  }
};
export {
  plugin_default as default
};
