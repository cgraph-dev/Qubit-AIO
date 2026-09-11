/* Qubit Boards — overlay plugin bundle (OpenClaw addition, dependency-free IIFE).
 * Uses only window.__HERMES_PLUGIN_SDK__ (React, hooks, fetchJSON).
 * Full board mutations on the canonical store (same voicebridge/kanban.py
 * module as `qubit kanban` CLI and voice): create board, add card, move
 * card across boards/columns, complete card. Board tabs and canvas widgets
 * are driven through the Tier-1 ops broker (POST /api/ops/run with
 * {id, args}) — the same dashboard_ops registry as CLI and voice. Every
 * mutation runs the shared confirmation journal: staged answers 409 (never
 * 2xx), and the tab offers inline Confirm/Cancel settling
 * POST /api/ops/confirm|cancel — the same records CLI approvals, voice
 * yes/no, and the shell-wide pending banner settle. No frontend-only
 * success: errors render typed backend states.
 *
 * SECURITY: note/text/link render as text; html/svg are only ever placed
 * inside a sandboxed iframe (sandbox="" — no allow-same-origin, no
 * allow-scripts) and never as live DOM. Link widgets render as anchors and
 * never auto-fetch. No innerHTML/dangerouslySetInnerHTML is used. */
(function () {
  var SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK || !window.__HERMES_PLUGINS__) return;
  var React = SDK.React, hooks = SDK.hooks, fetchJSON = SDK.fetchJSON;
  var C = SDK.components || {};
  var e = React.createElement;

  var STATUSES = ["open", "claimed", "blocked", "done", "archived"];
  var WIDGET_KINDS = ["note", "text", "link", "tasks", "stats", "links",
                      "html", "svg"];
  var MARKUP_KINDS = {"html": true, "svg": true};

  function parseEnvelope(ex) {
    /* fetchJSON throws Error("<status>: <body>") on non-2xx. A staged
     * confirmation is 409 with a JSON envelope carrying confirmation_id;
     * other non-2xx bodies carry a typed code/detail. */
    var msg = String((ex && ex.message) || ex || "");
    var at = msg.indexOf("{");
    if (at < 0) return null;
    try {
      var body = JSON.parse(msg.slice(at));
      if (!body || typeof body !== "object") return null;
      if (body.state === "NEEDS_CONFIRMATION" && body.confirmation_id) {
        return {staged: true, id: body.confirmation_id,
                detail: body.detail || ""};
      }
      return {staged: false, code: body.code || body.state || "",
              detail: body.detail || body.speech || ""};
    } catch (parseEx) { /* not a JSON envelope */ }
    return null;
  }

  function describeError(ex) {
    var info = parseEnvelope(ex);
    if (info && !info.staged) {
      return "[" + (info.code || "ERROR") + "] " +
        (info.detail || "request failed");
    }
    return String((ex && ex.message) || ex || "request failed");
  }

  /* kanban_tab_list returns {"tabs": <tab_list result>} and the result
   * itself carries {"tabs": [...]}; accept both shapes. */
  function tabsOf(result) {
    if (!result) return [];
    var t = result.tabs;
    if (Array.isArray(t)) return t;
    if (t && Array.isArray(t.tabs)) return t.tabs;
    return [];
  }

  function widgetsOf(result) {
    if (!result) return [];
    return Array.isArray(result.widgets) ? result.widgets : [];
  }

  function Boards() {
    var bst = hooks.useState(null);
    var boards = bst[0], setBoards = bst[1];
    var tst = hooks.useState(null);
    var tasks = tst[0], setTasks = tst[1];
    var sst = hooks.useState("");
    var selBoard = sst[0], setSelBoard = sst[1];
    var est = hooks.useState("");
    var err = est[0], setErr = est[1];
    var pst = hooks.useState(null);
    var pending = pst[0], setPending = pst[1];
    var wst = hooks.useState(false);
    var busy = wst[0], setBusy = wst[1];
    var nbs = hooks.useState("");
    var newSlug = nbs[0], setNewSlug = nbs[1];
    var nbn = hooks.useState("");
    var newName = nbn[0], setNewName = nbn[1];
    var nct = hooks.useState("");
    var newTitle = nct[0], setNewTitle = nct[1];
    var tb = hooks.useState(null);
    var tabs = tb[0], setTabs = tb[1];
    var wg = hooks.useState(null);
    var widgets = wg[0], setWidgets = wg[1];
    var stb = hooks.useState("main");
    var selTab = stb[0], setSelTab = stb[1];
    var nts = hooks.useState("");
    var newTabSlug = nts[0], setNewTabSlug = nts[1];
    var ntn = hooks.useState("");
    var newTabName = ntn[0], setNewTabName = ntn[1];
    var rn = hooks.useState("");
    var renameName = rn[0], setRenameName = rn[1];
    var mp = hooks.useState("0");
    var movePos = mp[0], setMovePos = mp[1];
    var wk = hooks.useState("note");
    var wkKind = wk[0], setWkKind = wk[1];
    var wt = hooks.useState("");
    var wkTitle = wt[0], setWkTitle = wt[1];
    var wc = hooks.useState("");
    var wkContent = wc[0], setWkContent = wc[1];
    var wtab = hooks.useState("");
    var wkTab = wtab[0], setWkTab = wtab[1];
    var pv = hooks.useState(null);
    var previewId = pv[0], setPreviewId = pv[1];

    function runRead(opId, args) {
      return fetchJSON("/api/ops/run", {method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({id: opId, args: args || {}})})
        .then(function (res) { return (res && res.result) || {}; });
    }

    function load(board) {
      var b = (board === undefined || board === null) ? selBoard : board;
      fetchJSON("/api/ops/boards").then(function (res) {
        var list = ((res && res.result && res.result.boards) || []);
        setBoards(list);
        if (!b && list.length) setSelBoard(list[0].board);
      }).catch(function (ex) { setErr(describeError(ex)); });
      var url = "/api/ops/kanban" +
        (b ? "?board=" + encodeURIComponent(b) : "");
      fetchJSON(url).then(function (res) {
        var inner = (res && res.result) || {};
        setTasks(inner.tasks || inner.receipt || []);
      }).catch(function (ex) { setErr(describeError(ex)); });
      if (!b) { setTabs([]); setWidgets([]); return; }
      runRead("kanban_tab_list", {board: b}).then(function (res) {
        var list = tabsOf(res);
        setTabs(list);
        setSelTab(function (prev) {
          var found = false;
          list.forEach(function (t) { if (t.slug === prev) found = true; });
          return found ? prev : ((list[0] && list[0].slug) || "main");
        });
      }).catch(function (ex) { setErr(describeError(ex)); });
      runRead("kanban_widget_list", {board: b}).then(function (res) {
        setWidgets(widgetsOf(res));
      }).catch(function (ex) { setErr(describeError(ex)); });
    }

    hooks.useEffect(function () { load(""); }, []);
    hooks.useEffect(function () { load(selBoard); }, [selBoard]);

    /* Sync the rename/position editor to the selected tab whenever the tab
     * set or selection changes (never on every keystroke). */
    hooks.useEffect(function () {
      var found = null;
      (tabs || []).forEach(function (t) { if (t.slug === selTab) found = t; });
      setRenameName(found ? (found.name || found.slug) : "");
      setMovePos(found ? String(found.position || 0) : "0");
    }, [selTab, tabs]);

    function stageRun(url, body, what) {
      if (busy) return;
      setBusy(true); setErr(""); setPending(null);
      fetchJSON(url, {method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(body || {})}).then(function () {
        setBusy(false);
        load(selBoard);
      }).catch(function (ex) {
        setBusy(false);
        var info = parseEnvelope(ex);
        if (info && info.staged) {
          setPending({id: info.id, what: what, detail: info.detail});
        } else {
          setErr(describeError(ex));
        }
      });
    }

    function runMutation(url, body, what) {
      stageRun(url, body, what);
    }

    function runOp(opId, args, what) {
      stageRun("/api/ops/run", {id: opId, args: args || {}}, what);
    }

    function settlePending(confirm) {
      if (!pending) return;
      var id = pending.id;
      setBusy(true); setErr("");
      fetchJSON("/api/ops/" + (confirm ? "confirm" : "cancel"), {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({confirmation_id: id}),
      }).then(function (res) {
        setBusy(false);
        setPending(null);
        if (res && res.ok) {
          load(selBoard);
        } else {
          setErr("[" + ((res && (res.code || res.state)) || "ERROR") + "] " +
            ((res && (res.detail || res.speech)) || "settle failed"));
        }
      }).catch(function (ex) {
        setBusy(false);
        setErr(describeError(ex));
      });
    }

    function createBoard() {
      var slug = (newSlug || "").trim();
      if (!slug) { setErr("[MALFORMED] board slug required"); return; }
      setNewSlug(""); setNewName("");
      runMutation("/api/ops/boards-create",
        {board: slug, name: (newName || "").trim()},
        "create board " + slug);
    }

    function addCard() {
      var t = (newTitle || "").trim();
      if (!t) { setErr("[MALFORMED] card title required"); return; }
      setNewTitle("");
      runMutation("/api/ops/cards-create",
        {title: t, board: selBoard || "main"}, "add card");
    }

    function createTab() {
      var slug = (newTabSlug || "").trim();
      if (!slug) { setErr("[MALFORMED] tab slug required"); return; }
      var name = (newTabName || "").trim() || slug;
      setNewTabSlug(""); setNewTabName("");
      runOp("kanban_tab_create", {board: selBoard || "main",
        slug: slug, name: name}, "create tab " + slug);
    }

    function renameTab() {
      var name = (renameName || "").trim();
      if (!name) { setErr("[MALFORMED] tab name required"); return; }
      runOp("kanban_tab_rename", {board: selBoard || "main",
        tab: selTab, name: name}, "rename tab " + selTab);
    }

    function deleteTab() {
      runOp("kanban_tab_delete", {board: selBoard || "main", tab: selTab},
        "delete tab " + selTab);
    }

    function moveTab() {
      var n = parseInt(movePos, 10);
      if (isNaN(n)) { setErr("[MALFORMED] tab position must be an integer"); return; }
      runOp("kanban_tab_move", {board: selBoard || "main", tab: selTab,
        position: n}, "move tab " + selTab);
    }

    function addWidget() {
      var title = (wkTitle || "").trim();
      if (!title) { setErr("[MALFORMED] widget title required"); return; }
      var kind = wkKind || "note";
      if (kind === "link" && !(wkContent || "").trim()) {
        setErr("[MALFORMED] link widget needs a URL"); return;
      }
      setWkTitle(""); setWkContent("");
      runOp("kanban_widget_add", {board: selBoard || "main", kind: kind,
        title: title, content: (wkContent || ""),
        tab: wkTab || selTab || "main"}, "add " + kind + " widget");
    }

    /* Effective tab set: backend tabs plus any tab referenced by a widget
     * (a board can keep widgets on the virtual default after it gains
     * explicit tabs, so those stay reachable). */
    var tabList = (tabs || []).slice();
    var seen = {};
    tabList.forEach(function (t) { seen[t.slug] = true; });
    (widgets || []).forEach(function (w) {
      var s = w.tab || "main";
      if (!seen[s]) {
        seen[s] = true;
        tabList.push({slug: s, name: s, virtual: true, id: null,
          default: s === "main", widget_count: 0});
      }
    });
    var selTabObj = null;
    tabList.forEach(function (t) { if (t.slug === selTab) selTabObj = t; });
    var activeTab = selTab || "main";
    var visible = (widgets || []).filter(function (w) {
      return (w.tab || "main") === activeTab;
    });
    /* Never hold a stale tab selection after a tab is deleted/renamed. */
    var wkTabValue = activeTab;
    if (wkTab) {
      var wkTabValid = false;
      tabList.forEach(function (t) { if (t.slug === wkTab) wkTabValid = true; });
      if (wkTabValid) wkTabValue = wkTab;
    }

    function renderWidgetBody(w) {
      var kind = String(w.kind || "").toLowerCase();
      if (MARKUP_KINDS[kind]) {
        var isPreview = previewId === w.id;
        return e("div", null,
          e("div", {style: {marginTop: 6, display: "flex", gap: 8,
            alignItems: "center", flexWrap: "wrap"}},
            e("span", {style: {fontSize: 12, opacity: 0.7}},
              "trust: " + (w.trust || "inert") + " · inert · " +
              "not executable"),
            e(C.Button || "button", {
              "aria-label": (isPreview ? "Hide" : "Preview") +
                " inert " + kind + " widget " + (w.id || ""),
              disabled: busy,
              onClick: function () {
                setPreviewId(isPreview ? null : w.id);
              }}, isPreview ? "Hide inert preview"
                            : "Preview inert (sandboxed)")),
          isPreview
            ? e("iframe", {
                sandbox: "",
                srcDoc: w.content || "",
                title: "Inert " + kind + " widget " + (w.id || ""),
                style: {width: "100%", height: 200, border: "1px solid #2a3443",
                  borderRadius: 6, background: "#fff", marginTop: 6}})
            : e("pre", {style: {whiteSpace: "pre-wrap", opacity: 0.8,
                marginTop: 6, fontSize: 12, maxHeight: 160,
                overflow: "auto"}}, w.content || ""));
      }
      if (kind === "link") {
        var href = w.url || w.content || "";
        return e("div", {style: {marginTop: 6}},
          e("a", {href: href, target: "_blank",
            rel: "noopener noreferrer nofollow",
            style: {wordBreak: "break-all"}}, href),
          e("span", {style: {opacity: 0.6, fontSize: 12}},
            " (reference only — opens only when clicked)"));
      }
      return e("pre", {style: {whiteSpace: "pre-wrap", margin: "6px 0 0",
        fontFamily: "inherit", fontSize: 13}}, w.content || "");
    }

    var list = tasks || [];
    return e("div", {style: {padding: 16, maxWidth: 960}},
      e("h2", null, "Boards"),
      e("p", {style: {opacity: 0.75}},
        "Kanban boards on the canonical store — same backend as `qubit kanban` CLI and voice. ",
        "Tables, tabs, and widgets run on the Tier-1 ops broker; mutations confirm ",
        "through the shared journal (inline below, or the shell banner)."),
      err ? e("div", {role: "alert", style: {color: "#e5484d"}}, err) : null,
      pending ? e("div", {role: "region", "aria-label": "Pending board confirmation",
        style: {marginTop: 8, padding: "8px 12px", border: "1px solid #b7791f",
          borderRadius: 8, fontSize: 13}},
        e("strong", null, "Confirm " + pending.what + ": "),
        e("span", null, String(pending.detail || "").slice(0, 160)),
        e("div", {style: {marginTop: 6, display: "flex", gap: 8}},
          e(C.Button || "button", {onClick: function () { settlePending(true); },
            disabled: busy}, busy ? "Working…" : "Confirm"),
          e(C.Button || "button", {onClick: function () { settlePending(false); },
            disabled: busy}, "Cancel"))) : null,
      e("div", {style: {marginTop: 12, display: "flex", gap: 8, flexWrap: "wrap",
        alignItems: "end"}},
        e("label", null, "Board ",
          e(C.Input || "input", {value: selBoard,
            "aria-label": "Active board",
            onChange: function (ev) { setSelBoard(ev.target.value); },
            placeholder: "board slug", style: {maxWidth: 180}})),
        e("label", null, "New board slug ",
          e(C.Input || "input", {value: newSlug,
            "aria-label": "New board slug",
            onChange: function (ev) { setNewSlug(ev.target.value); },
            placeholder: "slug", style: {maxWidth: 160}})),
        e("label", null, "Name ",
          e(C.Input || "input", {value: newName,
            "aria-label": "New board name",
            onChange: function (ev) { setNewName(ev.target.value); },
            placeholder: "name", style: {maxWidth: 200}})),
        e(C.Button || "button", {"aria-label": "Create board", disabled: busy,
          onClick: createBoard}, "Create board")),

      e("h3", {style: {marginTop: 18}},
        "Tabs" + (selBoard ? " on " + selBoard : "") + " (" + tabList.length + ")"),
      !tabList.length
        ? e("p", {style: {opacity: 0.6}}, "No tabs (create a board first).")
        : e("div", {role: "tablist", "aria-label": "Board tabs",
            style: {display: "flex", gap: 6, flexWrap: "wrap"}},
            tabList.map(function (t) {
              var active = t.slug === activeTab;
              return e("button", {key: t.slug, role: "tab",
                "aria-selected": active,
                "aria-label": "Tab " + (t.name || t.slug),
                disabled: busy,
                onClick: function () { setSelTab(t.slug); },
                style: {padding: "5px 10px", cursor: "pointer",
                  borderRadius: 6, color: "inherit",
                  background: active ? "#1b2634" : "transparent",
                  border: "1px solid " + (active ? "#33445a" : "#222c39")}},
                (t.name || t.slug) + " (" + (t.widget_count || 0) + ")" +
                (t.default ? " ★" : ""));
            })),
      e("div", {style: {marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap",
        alignItems: "end"}},
        e("label", null, "New tab slug ",
          e(C.Input || "input", {value: newTabSlug,
            "aria-label": "New tab slug",
            onChange: function (ev) { setNewTabSlug(ev.target.value); },
            placeholder: "slug", style: {maxWidth: 150}})),
        e("label", null, "New tab name ",
          e(C.Input || "input", {value: newTabName,
            "aria-label": "New tab name",
            onChange: function (ev) { setNewTabName(ev.target.value); },
            placeholder: "name", style: {maxWidth: 180}})),
        e(C.Button || "button", {"aria-label": "Create tab", disabled: busy,
          onClick: createTab}, "Create tab")),
      selTabObj ? e("div", {style: {marginTop: 8, display: "flex", gap: 8,
        flexWrap: "wrap", alignItems: "end"}},
        e("span", {style: {fontSize: 13, opacity: 0.8}},
          "Selected: " + (selTabObj.name || selTabObj.slug) +
          (selTabObj.id === null ? " (virtual default)" : "")),
        e("label", null, "Rename to ",
          e(C.Input || "input", {value: renameName,
            "aria-label": "Rename tab to",
            onChange: function (ev) { setRenameName(ev.target.value); },
            style: {maxWidth: 180}})),
        e(C.Button || "button", {"aria-label": "Rename tab", disabled: busy,
          onClick: renameTab}, "Rename tab"),
        e("label", null, "Position ",
          e(C.Input || "input", {value: movePos, type: "number",
            "aria-label": "Tab position",
            onChange: function (ev) { setMovePos(ev.target.value); },
            style: {maxWidth: 90}})),
        e(C.Button || "button", {"aria-label": "Move tab", disabled: busy,
          onClick: moveTab}, "Move tab"),
        e(C.Button || "button", {"aria-label": "Delete tab", disabled: busy,
          onClick: deleteTab}, "Delete tab")) : null,

      e("h3", {style: {marginTop: 18}},
        "Cards" + (selBoard ? " on " + selBoard : "") + " (" + list.length + ")"),
      e("div", {style: {marginTop: 8, display: "flex", gap: 8}},
        e(C.Input || "input", {value: newTitle,
          "aria-label": "New card title",
          onChange: function (ev) { setNewTitle(ev.target.value); },
          onKeyDown: function (ev) {
            if (ev.key === "Enter") addCard();
          },
          placeholder: "New card title… (Enter to add)", style: {flex: 1}}),
        e(C.Button || "button", {"aria-label": "Add card", disabled: busy,
          onClick: addCard}, "Add card")),
      !list.length
        ? e("p", {style: {opacity: 0.6}}, "No cards here yet.")
        : e("ul", null, list.map(function (c) {
            var id = c.id;
            return e("li", {key: id, style: {marginBottom: 8}},
              e("strong", null, "#" + id + " "), e("span", null, c.title || ""),
              e("span", {style: {opacity: 0.6}},
                " [" + (c.status || "?") + "@" + (c.board || "?") + "] "),
              e("label", null, "Move ",
                e("select", {
                  value: c.status || "open",
                  "aria-label": "Move card " + id + " to status",
                  disabled: busy,
                  onChange: function (ev) {
                    runMutation("/api/ops/cards-move",
                      {task_id: id, status: ev.target.value},
                      "move card #" + id);
                  }}, STATUSES.map(function (s) {
                    return e("option", {key: s, value: s}, s);
                  }))),
              e("button", {"aria-label": "Complete card " + id, disabled: busy,
                style: {marginLeft: 6},
                onClick: function () {
                  runMutation("/api/ops/cards-complete", {task_id: id},
                    "complete card #" + id);
                }}, "Complete"));
          })),

      e("h3", {style: {marginTop: 18}},
        "Widgets" + (selBoard ? " on " + selBoard : "") +
        " / tab " + activeTab + " (" + visible.length + ")"),
      e("div", {style: {marginTop: 8, display: "flex", gap: 8, flexWrap: "wrap",
        alignItems: "end"}},
        e("label", null, "Kind ",
          e("select", {value: wkKind, "aria-label": "Widget kind",
            disabled: busy,
            onChange: function (ev) { setWkKind(ev.target.value); }},
            WIDGET_KINDS.map(function (k) {
              return e("option", {key: k, value: k}, k);
            }))),
        e("label", null, "Title ",
          e(C.Input || "input", {value: wkTitle,
            "aria-label": "Widget title",
            onChange: function (ev) { setWkTitle(ev.target.value); },
            placeholder: "widget title", style: {maxWidth: 220}})),
        e("label", null, "Tab ",
          e("select", {value: wkTabValue, "aria-label": "Widget tab",
            disabled: busy,
            onChange: function (ev) { setWkTab(ev.target.value); }},
            tabList.map(function (t) {
              return e("option", {key: t.slug, value: t.slug},
                t.name || t.slug);
            }))),
        e(C.Button || "button", {"aria-label": "Add widget", disabled: busy,
          onClick: addWidget}, "Add widget")),
      e("div", {style: {marginTop: 6}},
        e("textarea", {value: wkContent,
          "aria-label": "Widget content",
          onChange: function (ev) { setWkContent(ev.target.value); },
          placeholder: "content (text, URL, or inert html/svg)…",
          rows: 3,
          style: {width: "100%", boxSizing: "border-box", padding: "7px 9px",
            background: "#0c1118", color: "inherit", border: "1px solid #2a3443",
            borderRadius: 6, fontFamily: "inherit"}})),
      !visible.length
        ? e("p", {style: {opacity: 0.6}},
            "No widgets on this tab yet.")
        : e("ul", {style: {listStyle: "none", padding: 0}},
            visible.map(function (w) {
              return e("li", {key: w.id,
                style: {marginTop: 10, padding: "8px 10px",
                  border: "1px solid #2a3443", borderRadius: 8}},
                e("div", null,
                  e("strong", null, w.title || ("widget #" + w.id)), " ",
                  e("span", {style: {fontSize: 11, padding: "1px 6px",
                    borderRadius: 999, border: "1px solid #33445a",
                    marginLeft: 4}}, w.kind || "?"),
                  e("span", {style: {fontSize: 11, padding: "1px 6px",
                    borderRadius: 999, border: "1px solid #33445a",
                    marginLeft: 6}}, w.inert ? "inert" : (w.trust || "trust")),
                  e("span", {style: {opacity: 0.55, fontSize: 12}},
                    "  tab " + (w.tab || "main"))),
                renderWidgetBody(w));
            })));
  }
  window.__HERMES_PLUGINS__.register("qubit-boards", Boards);
})();
