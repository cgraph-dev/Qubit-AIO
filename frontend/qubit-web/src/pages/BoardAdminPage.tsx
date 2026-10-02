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

  return (
    <div className="space-y-6 p-4">
      <div>
        <h1 className="text-xl font-semibold">Board administration</h1>
        <p className="text-sm text-muted-foreground">
          Tabs, widgets, roles, and the board log for one Qubit board. Sandbox-gated
          widget verbs stay disabled with their reason.
        </p>
      </div>

      <Card>
        <CardContent className="pt-4 flex items-end gap-2 flex-wrap">
          <div className="space-y-1">
            <Label htmlFor="board-admin-board">Board</Label>
            <Input
              id="board-admin-board"
              value={board}
              onChange={(e) => setBoard(e.target.value.trim() || "main")}
              placeholder="main"
            />
          </div>
          <Button outlined onClick={() => load(board)} disabled={loading}>
            Reload
          </Button>
        </CardContent>
      </Card>

      {loading ? (
        <div className="flex items-center gap-2">
          <Spinner /> <span className="text-sm">Loading board state…</span>
        </div>
      ) : null}
      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      <Card>
        <CardContent className="pt-4 space-y-3">
          <h2 className="font-medium">Tabs</h2>
          {tabs.length === 0 ? (
            <p className="text-sm text-muted-foreground">No tabs yet.</p>
          ) : (
            <ul className="space-y-1">
              {tabs.map((t) => (
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

      <Card>
        <CardContent className="pt-4 space-y-3">
          <h2 className="font-medium">Widgets</h2>
          {widgets.length === 0 ? (
            <p className="text-sm text-muted-foreground">No widgets yet.</p>
          ) : (
            <ul className="space-y-1">
              {widgets.map((w) => (
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

      <Card>
        <CardContent className="pt-4 space-y-3">
          <h2 className="font-medium">Roles</h2>
          {roles.length === 0 ? (
            <p className="text-sm text-muted-foreground">No grants yet.</p>
          ) : (
            <ul className="space-y-1">
              {roles.map((r, i) => (
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

      <Card>
        <CardContent className="pt-4 space-y-2">
          <h2 className="font-medium">Board log</h2>
          {events.length === 0 ? (
            <p className="text-sm text-muted-foreground">No board events yet.</p>
          ) : (
            <ul className="space-y-1">
              {events.map((e, i) => (
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
  );
}
