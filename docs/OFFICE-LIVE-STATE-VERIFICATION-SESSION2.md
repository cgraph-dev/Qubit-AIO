# Office live-state verification (this session)

HEAD at start: `5d9448286` (worktree `/tmp/wt-office-live2`, branch `fix/office-live-state-2`).
Live server: `http://127.0.0.1:8890`. Everything below was RUN; outputs are verbatim.

## 1. The SSE stream delivers REAL frames (raw curl capture)

```
$ TOK=$(curl -s http://127.0.0.1:8890/chat | grep -o 'window.__QUBIT_SESSION_TOKEN__="[^"]*"' | cut -d'"' -f2)
$ timeout 90 curl -sN -H "X-Qubit-Session-Token: $TOK" "http://127.0.0.1:8890/api/stream?token=$TOK"
```

Captured 56s -> 224 lines / ~250 KB. Event counts: `28 x event: snapshot`, `28 x event: feed`.
`rev` advances monotonically 1..N (no gaps, no repeats). First frame:

```
retry: 2000

event: snapshot
data: {"rev": 1, "serverTime": 1792177649664, "rooms": [{"id": "f1r1", "name": "boardroom", ...},
      {"id": "f2r2","name":"desk-a"}, {"id":"f2r3","name":"desk-b"}, {"id":"f3r4","name":"review"}],
      "agents":[{"id":"1","roomId":"f1r1","name":"ceo","role":"ceo","status":"idle",
      "lastEventAgeS":170.1,"hasHistory":true,"subagents":2,...}, ...]}
```

Feed rows: 28 frames x 40 rows, **40 DISTINCT ids** (the same recent window re-sent each poll).
The client's own `applyFeed` dedups by id:
`applyFeed(e){...let t=new Set(this.feed.map(e=>e.id)),n=e.filter(e=>!t.has(e.id));...}`
-> re-send is safe; no duplicate rows.

## 2. Activity reaches the UI (headless Chrome CDP, DOM read back)

Drove `/qubit-office` on CDP :9502 and read the iframe DOM. Time-sampled (5 x 3s):

```
{"agents": 10, "working": 1, "idle": 11, "waiting": 1, "pills": ["ui-pill is-open"],
 "head": "0 Activity ceo: ceo wrote a memory 22:04:39 ceo Q boardroom ceo wrote a memory 22:04:39 lead-quality ..."}
```

So: 10 agents render, real activity rows (`ceo wrote a memory`, `lead-quality`, `lead-content`,
room `boardroom`), and the connection pill reads `is-open`.

## 3. The pill badge IS live-driven (falsification: block the stream)

CSS semantics (`app-BW61bUik.css`):
`.ui-pill{--pill:var(--st-idle)} .ui-pill.is-open{--pill:var(--st-working)}
 .ui-pill.is-closed{--pill:var(--lv-crit)} .ui-pill.is-connecting{--pill:var(--st-waiting)}`

Two clean runs, `Network.setBlockedURLs(['*api/stream*'])` before navigate vs. not:

| case | stream | pill class | agents | body |
|---|---|---|---|---|
| BLOCKED | 3 loadingFailed, 0 responses | `ui-pill is-closed` | 0 | "The Habblaud server isn't responding" |
| OK | RES 200 text/event-stream | `ui-pill is-open` | 10 | real activity rows |

The badge transitions `is-connecting -> is-closed` when the stream is down and
`is-open` when it is live. **The badge updates; it reflects the connection.**

## 4. Cursor / resync

The office SSE emits NO `id:` field and the server does not implement `Last-Event-ID`
(grep confirms: no producer). The client does not use `rev` as a cursor either — it
re-receives a FULL snapshot every 2s (`SNAPSHOT_INTERVAL_SECONDS = 2.0`) and replaces
state wholesale. Client guard:
`applySnapshot(e){let t=this.replay?this.live?.snap:this.snapshot;
  t&&e.rev<t.rev&&e.meta.startedAt===t.meta.startedAt||(... this.setSnapshot(e))}`
i.e. skip only if `rev` went BACKWARDS **and** `meta.startedAt` is unchanged.
In-connection `rev` is monotonic and `meta.startedAt` is fixed per connection
(`start_time`), so every frame applies -> **no frames dropped**. On reconnect the
server's `rev` resets to 1 but `meta.startedAt` changes, so the guard applies it.
Verified: no dropped frames observed across 28 in-connection frames.

## Findings

- NO frame-drop / cursor bug: full-snapshot re-send + client-side feed dedup by id.
- NO dead badge: pill transitions is-connecting/is-closed/is-open correctly.
- "idle after ACTIVE_WINDOW_S=120" is BY DESIGN (not re-reported).
