/**
 * Board administration — tabs, widgets, roles, and the board log.
 *
 * Qubit-owned page (route `/qubit-board-admin`). Tab create/rename/move/delete,
 * widget add/list/remove, and role grant + roster run through the board family
 * client (`@/lib/ocFamilyBoardApi`); every mutation settles the shared
 * staged-confirmation round-trip via an in-app confirm dialog, and failures
 * render the typed backend reason (never a faked success).
 *
 * `board.event`, `board.prompt.authorize`, and `board.action` need a board
 * widget sandbox / signed view-ticket runtime Qubit does not run, so they are
 * rendered disabled-with-reason: code + prerequisite + counterpart are quoted
 * verbatim from the gateway handler returns and the buttons never fire.
 *
 * Look mirrors the upstream workboard UI 1:1 (Qubit voice, rebranded):
 * `ui/src/ui/views/workboard.ts` `renderWorkboard` —
 * `section.workboard > .workboard-main > .workboard-toolbar` (filters +
 * actions) `> .workboard-health` strip `> .callout` error `> .workboard-board`
 * of `section.workboard-column` (header with `h2` + count, then cards) `>`
 * `.workboard-empty-state` — while every backend call, confirm-gating step,
 * and verbatim refusal reason below is unchanged (look changes, behavior
 * doesn't).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Info } from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";
import { Input } from "@nous-research/ui/ui/components/input";
import { Label } from "@nous-research/ui/ui/components/label";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Toast } from "@nous-research/ui/ui/components/toast";
import { ConfirmDialog } from "@nous-research/ui/ui/components/confirm-dialog";
import { useToast } from "@nous-research/ui/hooks/use-toast";
import {
  BOARD_ACTION_REFUSAL,
  BOARD_EVENT_REFUSAL,
  BOARD_PROMPT_AUTHORIZE_REFUSAL,
  addBoardWidget,
  createBoardTab,
  deleteBoardTab,
  getBoardLog,
  getBoardRoster,
  grantBoardRole,
  listBoardTabs,
  listBoardWidgets,
  moveBoardTab,
  normalizeEvents,
  normalizeRoles,
  normalizeTabs,
  normalizeWidgets,
  removeBoardWidget,
  renameBoardTab,
  type BoardEvent,
  type BoardRole,
  type BoardTab,
  type BoardWidget,
  type GatewayRefusal,
} from "@/lib/ocFamilyBoardApi";
import { refusalOrError } from "@/lib/policy-refusal";

type BoardViewPreset = "all" | "tabs" | "widgets" | "roles" | "log";

function RefusalRow({ refusal }: { refusal: GatewayRefusal }) {
  return (
    <div className="rounded-md border border-dashed p-3 space-y-1" data-testid={`refusal-${refusal.code}`}>
      <div className="flex items-center gap-2 flex-wrap">
        <code className="text-xs font-mono">{refusal.method}</code>
        <Badge tone="secondary">{refusal.code}</Badge>
      </div>
      <p className="text-sm text-muted-foreground">{refusal.detail}</p>
      <p className="text-sm">
        <span className="font-medium">Needs: </span>
        {refusal.prerequisite}
      </p>
      <p className="text-sm">
        <span className="font-medium">Use instead: </span>
        <code className="text-xs font-mono">{refusal.counterpart}</code>
      </p>
      <Button disabled title={refusal.detail}>
        Run {refusal.method}
      </Button>
    </div>
  );
}

export default function BoardAdminPage() {
  const [board, setBoard] = useState("main");
  const [tabs, setTabs] = useState<BoardTab[]>([]);
  const [widgets, setWidgets] = useState<BoardWidget[]>([]);
  const [roles, setRoles] = useState<BoardRole[]>([]);
  const [events, setEvents] = useState<BoardEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const { toast, showToast } = useToast();

  // Toolbar-local view state only (mirrors the upstream workboard toolbar:
  // search, view preset, hide-empty, layout, refresh status). Never sent to
  // the backend; every backend call below is unchanged.
  const [query, setQuery] = useState("");
  const [viewPreset, setViewPreset] = useState<BoardViewPreset>("all");
  const [hideEmpty, setHideEmpty] = useState(false);
  const [layout, setLayout] = useState<"compact" | "comfortable">("comfortable");
  const [lastRefreshAt, setLastRefreshAt] = useState<number | null>(null);

  const [newSlug, setNewSlug] = useState("");
  const [newTitle, setNewTitle] = useState("");
  const [renameSlug, setRenameSlug] = useState("");
  const [renameTitle, setRenameTitle] = useState("");
  const [moveSlug, setMoveSlug] = useState("");
  const [movePos, setMovePos] = useState("0");
  const [widgetKind, setWidgetKind] = useState("note");
  const [widgetTitle, setWidgetTitle] = useState("");
  const [widgetContent, setWidgetContent] = useState("");
  const [grantSubject, setGrantSubject] = useState("");
  const [grantRole, setGrantRole] = useState("viewer");

  const [brokerConfirm, setBrokerConfirm] = useState<{
    title: string;
    description: string;
  } | null>(null);
  const confirmResolverRef = useRef<((ok: boolean) => void) | null>(null);

  const askBrokerConfirm = useCallback((title: string, description: string) => {
    return new Promise<boolean>((resolve) => {
      confirmResolverRef.current = resolve;
      setBrokerConfirm({ title, description });
    });
  }, []);

  const settleBrokerConfirm = useCallback((ok: boolean) => {
    const resolve = confirmResolverRef.current;
    confirmResolverRef.current = null;
    setBrokerConfirm(null);
    resolve?.(ok);
  }, []);

  const load = useCallback(
    async (slug: string) => {
      setLoading(true);
      setError("");
      try {
        const [t, w, r, l] = await Promise.all([
          listBoardTabs(slug),
          listBoardWidgets(slug),
          getBoardRoster(slug),
          getBoardLog(slug),
        ]);
        setTabs(normalizeTabs(t));
        setWidgets(normalizeWidgets(w));
        setRoles(normalizeRoles(r));
        setEvents(normalizeEvents(l));
        setLastRefreshAt(Date.now());
      } catch (e) {
        const message = refusalOrError(e, "Failed to load board state.");
        setError(message);
        showToast(message, "error");
      } finally {
        setLoading(false);
      }
    },
    [showToast],
  );

  useEffect(() => {
    load(board);
  }, [board, load]);

  const mutate = useCallback(
    async (title: string, call: () => Promise<unknown>) => {
      if (busy) return;
      setBusy(true);
      setError("");
      try {
        await call();
        showToast(title, "success");
        await load(board);
      } catch (e) {
        const message = refusalOrError(e, `${title} failed.`);
        setError(message);
        showToast(message, "error");
      } finally {
        setBusy(false);
      }
    },
    [board, busy, load, showToast],
  );

  const confirm = useCallback(
    (title: string) => (detail: string) => askBrokerConfirm(title, detail),
    [askBrokerConfirm],
  );

  const q = query.trim().toLowerCase();
  const matches = (...parts: Array<string | undefined>) =>
    q === "" || parts.filter(Boolean).join(" ").toLowerCase().includes(q);
  const visibleTabs = tabs.filter((t) => matches(t.slug, t.name));
  const visibleWidgets = widgets.filter((w) => matches(w.id, w.kind, w.title));
  const visibleRoles = roles.filter((r) => matches(r.subject, r.role, r.scope));
  const visibleEvents = events.filter((e) => matches(e.kind, e.detail));

  const allColumns = [
    { key: "tabs" as const, title: "Tabs", count: visibleTabs.length },
    { key: "widgets" as const, title: "Widgets", count: visibleWidgets.length },
    { key: "roles" as const, title: "Roles", count: visibleRoles.length },
    { key: "log" as const, title: "Board log", count: visibleEvents.length },
  ];
  const visibleColumns =
    viewPreset === "all"
      ? hideEmpty
        ? allColumns.filter((c) => c.count > 0)
        : allColumns
      : allColumns.filter((c) => c.key === viewPreset);
  const totalVisibleRows =
    visibleTabs.length + visibleWidgets.length + visibleRoles.length + visibleEvents.length;
  const showFilteredEmpty =
    q !== "" && (viewPreset === "all" ? totalVisibleRows === 0 : visibleColumns.every((c) => c.count === 0));

  const healthItems: Array<{ key: BoardViewPreset; label: string; count: number }> = [
    { key: "tabs", label: "Tabs", count: tabs.length },
    { key: "widgets", label: "Widgets", count: widgets.length },
    { key: "roles", label: "Grants", count: roles.length },
    { key: "log", label: "Events", count: events.length },
  ];

  const showColumn = (key: BoardViewPreset) => visibleColumns.some((c) => c.key === key);

  return (
    <section className="workboard">
      <div className="workboard-main space-y-6 p-4">
        <div>
          <h1 className="text-xl font-semibold">Board administration</h1>
          <p className="text-sm text-muted-foreground">
            Tabs, widgets, roles, and the board log for one Qubit board. Sandbox-gated
            widget verbs stay disabled with their reason.
          </p>
        </div>

        <div className="workboard-toolbar">
          <div className="workboard-toolbar__filters">
            <div className="space-y-1">
              <Label htmlFor="board-admin-board">Board</Label>
              <Input
                id="board-admin-board"
                value={board}
                onChange={(e) => setBoard(e.target.value.trim() || "main")}
                placeholder="main"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="board-admin-search">Search</Label>
              <Input
                id="board-admin-search"
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search tabs, widgets, roles, events…"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="board-admin-view">View</Label>
              <select
                id="board-admin-view"
                className="input"
                value={viewPreset}
                onChange={(e) => setViewPreset(e.target.value as BoardViewPreset)}
              >
                <option value="all">All sections</option>
                <option value="tabs">Tabs</option>
                <option value="widgets">Widgets</option>
                <option value="roles">Roles</option>
                <option value="log">Board log</option>
              </select>
            </div>
            <label className="workboard-toggle" htmlFor="board-admin-hide-empty">
              <input
                id="board-admin-hide-empty"
                type="checkbox"
                checked={hideEmpty}
                onChange={(e) => setHideEmpty(e.target.checked)}
              />
              <span>Hide empty sections</span>
            </label>
            <div className="workboard-layout-controls">
              <div className="workboard-layout-toggle" role="group" aria-label="Layout">
                <button
                  className={`btn btn--icon ${layout === "compact" ? "active" : ""}`}
                  type="button"
                  aria-label="Compact layout"
                  aria-pressed={layout === "compact"}
                  onClick={() => setLayout("compact")}
                >
                  Compact
                </button>
                <button
                  className={`btn btn--icon ${layout === "comfortable" ? "active" : ""}`}
                  type="button"
                  aria-label="Comfortable layout"
                  aria-pressed={layout === "comfortable"}
                  onClick={() => setLayout("comfortable")}
                >
                  Comfortable
                </button>
              </div>
              {lastRefreshAt ? (
                <span className="workboard-refresh-status">
                  Last refreshed{" "}
                  {new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(
                    new Date(lastRefreshAt),
                  )}
                </span>
              ) : null}
            </div>
          </div>
          <div className="workboard-toolbar__actions">
            <Button outlined onClick={() => load(board)} disabled={loading}>
              Reload
            </Button>
          </div>
        </div>

        <div className="workboard-health" aria-label="Board health">
          {healthItems.map((item) => (
            <button
              key={item.key}
              className={`workboard-health__item workboard-health__item--${item.key} ${
                viewPreset === item.key ? "workboard-health__item--active" : ""
              } ${item.count === 0 ? "workboard-health__item--empty" : ""}`}
              type="button"
              aria-pressed={viewPreset === item.key}
              aria-label={`${item.count} ${item.label}`}
              onClick={() => setViewPreset((prev) => (prev === item.key ? "all" : item.key))}
            >
              <strong>{item.count}</strong>
              {item.label}
            </button>
          ))}
        </div>

        {loading ? (
          <div className="flex items-center gap-2">
            <Spinner /> <span className="text-sm">Loading board state…</span>
          </div>
        ) : null}
        {error ? (
          <div className="callout danger" role="alert">
            {error}
          </div>
        ) : null}

        {showFilteredEmpty ? (
          <div className="workboard-empty-state" role="status">
            <strong>No sections match this filter</strong>
            <span>Clear the search or show all sections.</span>
          </div>
        ) : null}

        <div className={`workboard-board workboard-board--${layout}`}>
          {showColumn("tabs") ? (
            <section className="workboard-column workboard-column--tabs">
              <div className="workboard-column__header">
                <h2>Tabs</h2>
                <span>{visibleTabs.length}</span>
              </div>
              <div className="workboard-column__cards">
                <Card>
                  <CardContent className="pt-4 space-y-3">
                    {visibleTabs.length === 0 ? (
                      <p className="text-sm text-muted-foreground">No tabs yet.</p>
                    ) : (
                      <ul className="space-y-1">
                        {visibleTabs.map((t) => (
                          <li key={t.slug} className="flex items-center gap-2 text-sm">
                            <code className="text-xs font-mono">{t.slug}</code>
                            <span>{t.name}</span>
                            {typeof t.position === "number" ? (
                              <Badge tone="outline">pos {t.position}</Badge>
                            ) : null}
                            <Button
                              outlined
                              size="sm"
                              disabled={busy}
                              onClick={() =>
                                mutate("Delete tab", () =>
                                  deleteBoardTab(board, t.slug, confirm(`Delete tab ${t.slug}`)),
                                )
                              }
                            >
                              Delete
                            </Button>
                          </li>
                        ))}
                      </ul>
                    )}
                    <div className="flex items-end gap-2 flex-wrap">
                      <div className="space-y-1">
                        <Label htmlFor="board-admin-new-slug">New tab id</Label>
                        <Input
                          id="board-admin-new-slug"
                          value={newSlug}
                          onChange={(e) => setNewSlug(e.target.value)}
                          placeholder="ideas"
                        />
                      </div>
                      <div className="space-y-1">
                        <Label htmlFor="board-admin-new-title">Title</Label>
                        <Input
                          id="board-admin-new-title"
                          value={newTitle}
                          onChange={(e) => setNewTitle(e.target.value)}
                          placeholder="Ideas"
                        />
                      </div>
                      <Button
                        disabled={busy || !newSlug.trim() || !newTitle.trim()}
                        onClick={() => {
                          const slug = newSlug.trim();
                          const title = newTitle.trim();
                          setNewSlug("");
                          setNewTitle("");
                          void mutate("Create tab", () =>
                            createBoardTab(board, slug, title, confirm(`Create tab ${slug}`)),
                          );
                        }}
                      >
                        Create tab
                      </Button>
                    </div>
                    <div className="flex items-end gap-2 flex-wrap">
                      <div className="space-y-1">
                        <Label htmlFor="board-admin-rename-slug">Tab id</Label>
                        <Input
                          id="board-admin-rename-slug"
                          value={renameSlug}
                          onChange={(e) => setRenameSlug(e.target.value)}
                          placeholder="ideas"
                        />
                      </div>
                      <div className="space-y-1">
                        <Label htmlFor="board-admin-rename-title">New title</Label>
                        <Input
                          id="board-admin-rename-title"
                          value={renameTitle}
                          onChange={(e) => setRenameTitle(e.target.value)}
                          placeholder="Ideas v2"
                        />
                      </div>
                      <Button
                        outlined
                        disabled={busy || !renameSlug.trim() || !renameTitle.trim()}
                        onClick={() => {
                          const slug = renameSlug.trim();
                          const title = renameTitle.trim();
                          setRenameSlug("");
                          setRenameTitle("");
                          void mutate("Rename tab", () =>
                            renameBoardTab(board, slug, title, confirm(`Rename tab ${slug}`)),
                          );
                        }}
                      >
                        Rename
                      </Button>
                      <div className="space-y-1">
                        <Label htmlFor="board-admin-move-slug">Move tab id</Label>
                        <Input
                          id="board-admin-move-slug"
                          value={moveSlug}
                          onChange={(e) => setMoveSlug(e.target.value)}
                          placeholder="ideas"
                        />
                      </div>
                      <div className="space-y-1">
                        <Label htmlFor="board-admin-move-pos">Position</Label>
                        <Input
                          id="board-admin-move-pos"
                          value={movePos}
                          onChange={(e) => setMovePos(e.target.value)}
                          placeholder="0"
                        />
                      </div>
                      <Button
                        outlined
                        disabled={busy || !moveSlug.trim()}
                        onClick={() => {
                          const slug = moveSlug.trim();
                          const pos = Number.parseInt(movePos, 10);
                          setMoveSlug("");
                          void mutate("Move tab", () =>
                            moveBoardTab(board, slug, Number.isFinite(pos) ? pos : 0, confirm(`Move tab ${slug}`)),
                          );
                        }}
                      >
                        Move
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              </div>
            </section>
          ) : null}

          {showColumn("widgets") ? (
            <section className="workboard-column workboard-column--widgets">
              <div className="workboard-column__header">
                <h2>Widgets</h2>
                <span>{visibleWidgets.length}</span>
              </div>
              <div className="workboard-column__cards">
                <Card>
                  <CardContent className="pt-4 space-y-3">
                    {visibleWidgets.length === 0 ? (
                      <p className="text-sm text-muted-foreground">No widgets yet.</p>
                    ) : (
                      <ul className="space-y-1">
                        {visibleWidgets.map((w) => (
                          <li key={w.id} className="flex items-center gap-2 text-sm">
                            <code className="text-xs font-mono">{w.id}</code>
                            {w.kind ? <Badge tone="outline">{w.kind}</Badge> : null}
                            <span>{w.title}</span>
                            <Button
                              outlined
                              size="sm"
                              disabled={busy}
                              onClick={() =>
                                mutate("Remove widget", () =>
                                  removeBoardWidget(board, w.id, confirm(`Remove widget ${w.id}`)),
                                )
                              }
                            >
                              Remove
                            </Button>
                          </li>
                        ))}
                      </ul>
                    )}
                    <div className="flex items-end gap-2 flex-wrap">
                      <div className="space-y-1">
                        <Label htmlFor="board-admin-widget-kind">Kind</Label>
                        <Input
                          id="board-admin-widget-kind"
                          value={widgetKind}
                          onChange={(e) => setWidgetKind(e.target.value)}
                          placeholder="note"
                        />
                      </div>
                      <div className="space-y-1">
                        <Label htmlFor="board-admin-widget-title">Title</Label>
                        <Input
                          id="board-admin-widget-title"
                          value={widgetTitle}
                          onChange={(e) => setWidgetTitle(e.target.value)}
                          placeholder="Shopping list"
                        />
                      </div>
                      <div className="space-y-1">
                        <Label htmlFor="board-admin-widget-content">Content</Label>
                        <Input
                          id="board-admin-widget-content"
                          value={widgetContent}
                          onChange={(e) => setWidgetContent(e.target.value)}
                          placeholder="milk, eggs"
                        />
                      </div>
                      <Button
                        disabled={busy || !widgetTitle.trim()}
                        onClick={() => {
                          const title = widgetTitle.trim();
                          const kind = widgetKind.trim() || "note";
                          const content = widgetContent;
                          setWidgetTitle("");
                          setWidgetContent("");
                          void mutate("Add widget", () =>
                            addBoardWidget(board, kind, title, content, confirm(`Add widget ${title}`)),
                          );
                        }}
                      >
                        Add widget
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              </div>
            </section>
          ) : null}

          {showColumn("roles") ? (
            <section className="workboard-column workboard-column--roles">
              <div className="workboard-column__header">
                <h2>Roles</h2>
                <span>{visibleRoles.length}</span>
              </div>
              <div className="workboard-column__cards">
                <Card>
                  <CardContent className="pt-4 space-y-3">
                    {visibleRoles.length === 0 ? (
                      <p className="text-sm text-muted-foreground">No grants yet.</p>
                    ) : (
                      <ul className="space-y-1">
                        {visibleRoles.map((r, i) => (
                          <li key={`${r.subject}-${r.role}-${i}`} className="flex items-center gap-2 text-sm">
                            <code className="text-xs font-mono">{r.subject}</code>
                            <Badge tone="outline">{r.role}</Badge>
                            {r.scope ? <span className="text-muted-foreground">{r.scope}</span> : null}
                          </li>
                        ))}
                      </ul>
                    )}
                    <div className="flex items-end gap-2 flex-wrap">
                      <div className="space-y-1">
                        <Label htmlFor="board-admin-grant-subject">Subject</Label>
                        <Input
                          id="board-admin-grant-subject"
                          value={grantSubject}
                          onChange={(e) => setGrantSubject(e.target.value)}
                          placeholder="teammate"
                        />
                      </div>
                      <div className="space-y-1">
                        <Label htmlFor="board-admin-grant-role">Role</Label>
                        <Input
                          id="board-admin-grant-role"
                          value={grantRole}
                          onChange={(e) => setGrantRole(e.target.value)}
                          placeholder="viewer"
                        />
                      </div>
                      <Button
                        disabled={busy || !grantSubject.trim() || !grantRole.trim()}
                        onClick={() => {
                          const subject = grantSubject.trim();
                          const role = grantRole.trim();
                          setGrantSubject("");
                          void mutate("Grant role", () =>
                            grantBoardRole(board, subject, role, confirm(`Grant ${role} to ${subject}`)),
                          );
                        }}
                      >
                        Grant role
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              </div>
            </section>
          ) : null}

          {showColumn("log") ? (
            <section className="workboard-column workboard-column--log">
              <div className="workboard-column__header">
                <h2>Board log</h2>
                <span>{visibleEvents.length}</span>
              </div>
              <div className="workboard-column__cards">
                <Card>
                  <CardContent className="pt-4 space-y-2">
                    {visibleEvents.length === 0 ? (
                      <p className="text-sm text-muted-foreground">No board events yet.</p>
                    ) : (
                      <ul className="space-y-1">
                        {visibleEvents.map((e, i) => (
                          <li key={e.id || i} className="text-sm">
                            {e.kind ? <Badge tone="outline">{e.kind}</Badge> : null}{" "}
                            <span>{e.detail}</span>{" "}
                            {e.at ? <span className="text-muted-foreground">{e.at}</span> : null}
                          </li>
                        ))}
                      </ul>
                    )}
                  </CardContent>
                </Card>
              </div>
            </section>
          ) : null}
        </div>

        <Card>
          <CardContent className="pt-4 space-y-3">
            <h2 className="font-medium flex items-center gap-2">
              <Info size={16} /> Sandbox-gated verbs (disabled with reason)
            </h2>
            <RefusalRow refusal={BOARD_EVENT_REFUSAL} />
            <RefusalRow refusal={BOARD_PROMPT_AUTHORIZE_REFUSAL} />
            <RefusalRow refusal={BOARD_ACTION_REFUSAL} />
          </CardContent>
        </Card>

        <ConfirmDialog
          open={brokerConfirm !== null}
          onCancel={() => settleBrokerConfirm(false)}
          onConfirm={() => settleBrokerConfirm(true)}
          title={brokerConfirm?.title ?? "Confirm board change"}
          description={brokerConfirm?.description ?? "The board backend staged this change for confirmation."}
        />
        {toast ? <Toast toast={toast} /> : null}
      </div>
    </section>
  );
}
