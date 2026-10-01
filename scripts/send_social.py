#!/usr/bin/env python3
"""Send a confirmed message through the logged-in Chromium session (CDP).

Usage:
  send_social.py --channel {gmail,messenger,instagram} --to NAME
                 --href LOCATOR --body TEXT [--dry-run | --send]

--dry-run fills the composer, verifies the text, clears it, and reports
without sending. --send delivers exactly once, then verifies the composer
emptied. Any ambiguity (thread gone/changed, composer missing, text
mismatch) refuses with typed JSON and touches nothing.

Output: JSON {"ok", "code", "detail"} on stdout; exit 0 iff sent/dry-ok.
"""
import argparse
import json
import re
import sys
import time
import urllib.request

try:
    import websocket
except Exception:
    print(json.dumps({"ok": False, "code": "SEND_NO_WS",
                      "detail": "websocket-client missing"}))
    sys.exit(1)

CDP = "http://127.0.0.1:9222"
TAB_FRAG = {"gmail": "mail.google.com", "messenger": "messenger.com",
            "instagram": "instagram.com/direct"}


def fail(code, detail):
    print(json.dumps({"ok": False, "code": code, "detail": detail[:200]}))
    sys.exit(1)


def ok(detail):
    print(json.dumps({"ok": True, "code": "SENT",
                      "detail": detail[:200]}))
    sys.exit(0)


class Ctx:
    def __init__(self, tab_url):
        tabs = json.load(urllib.request.urlopen(CDP + "/json", timeout=10))
        target = None
        for t in tabs:
            if t.get("type") == "page" and tab_url in t.get("url", ""):
                target = t["webSocketDebuggerUrl"]
                break
        if not target:
            fail("SEND_NO_TAB", f"no open {tab_url} tab")
        self.ws = websocket.create_connection(target, timeout=30)
        self._id = 0

    def cmd(self, method, params=None):
        self._id += 1
        self.ws.send(json.dumps({"id": self._id, "method": method,
                                 "params": params or {}}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == self._id:
                return m.get("result", {})

    def js(self, expr):
        r = self.cmd("Runtime.evaluate",
                     {"expression": expr, "returnByValue": True})
        return r.get("result", {}).get("value")

    def wait_js(self, expr, timeout_s=15):
        end = time.time() + timeout_s
        while time.time() < end:
            v = self.js(expr)
            if v:
                return v
            time.sleep(0.7)
        return None

    def click_at(self, x, y):
        self.cmd("Input.dispatchMouseEvent",
                 {"type": "mouseMoved", "x": x, "y": y})
        self.cmd("Input.dispatchMouseEvent",
                 {"type": "mousePressed", "x": x, "y": y, "button": "left",
                  "buttons": 1, "clickCount": 1})
        self.cmd("Input.dispatchMouseEvent",
                 {"type": "mouseReleased", "x": x, "y": y, "button": "left",
                  "buttons": 0, "clickCount": 1})

    def press_enter(self, ctrl=False):
        params = {"key": "Enter", "code": "Enter",
                  "windowsVirtualKeyCode": 13}
        if ctrl:
            params["modifiers"] = 2
        # NOTE: Instagram's Draft.js composer treats a bare Enter as a
        # newline — it sends only on Ctrl+Enter. Messenger sends on a
        # bare Enter. Callers pass the channel-appropriate flag.
        self.cmd("Input.dispatchKeyEvent",
                 dict(params, type="rawKeyDown"))
        self.cmd("Input.dispatchKeyEvent", dict(params, type="keyDown"))
        self.cmd("Input.dispatchKeyEvent", dict(params, type="keyup"))

    def fill_composer(self, composer_js, body):
        """Focus + trusted text insert; returns composer text after."""
        self.js(composer_js + ".focus()")
        time.sleep(0.4)
        self.cmd("Input.insertText", {"text": body})
        time.sleep(0.6)
        return self.js(composer_js +
                       ".innerText || " + composer_js + ".value || ''")

    def clear_composer(self, composer_js):
        self.js(composer_js + ".focus()")
        time.sleep(0.3)
        self.cmd("Input.dispatchKeyEvent",
                 {"type": "keyDown", "key": "a", "code": "KeyA",
                  "windowsVirtualKeyCode": 65, "modifiers": 2})
        self.cmd("Input.dispatchKeyEvent",
                 {"type": "keyup", "key": "a", "code": "KeyA",
                  "windowsVirtualKeyCode": 65, "modifiers": 2})
        self.cmd("Input.dispatchKeyEvent",
                 {"type": "keyDown", "key": "Backspace", "code": "Backspace",
                  "windowsVirtualKeyCode": 8})
        self.cmd("Input.dispatchKeyEvent",
                 {"type": "keyup", "key": "Backspace", "code": "Backspace",
                  "windowsVirtualKeyCode": 8})
        time.sleep(0.5)
        return self.js(composer_js +
                       ".innerText || " + composer_js + ".value || ''")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def send_messenger(ctx, to, href, body, dry_run):
    if href and href.rstrip("/") not in (ctx.js("location.href") or ""):
        ctx.cmd("Page.navigate", {"url": href})
        time.sleep(5)
    label = ctx.wait_js(
        "(() => { const els=[...document.querySelectorAll("
        "'[contenteditable=\"true\"],[role=\"textbox\"]')]; const vis="
        "els.filter(e=>{const r=e.getBoundingClientRect(); return "
        "r.width>0&&r.height>0;}); const c=vis.length?vis[vis.length-1]"
        ":null; return c?(c.getAttribute('aria-label')||''):''; })()", 15)
    if to.lower() not in (label or "").lower():
        fail("SEND_RECIPIENT_MISMATCH",
             f"composer is for {label!r}, expected {to!r}")
    after = ctx.fill_composer(
        "(() => { const els=[...document.querySelectorAll("
        "'[contenteditable=\"true\"],[role=\"textbox\"]')]; const vis=els"
        ".filter(e=>{const r=e.getBoundingClientRect(); return r.width>0&&"
        "r.height>0;}); return vis.length?vis[vis.length-1]:null; })()",
        body)
    if body.strip()[:40] not in (after or ""):
        ctx.clear_composer(
            "document.querySelector('[contenteditable=\"true\"]')")
        fail("SEND_FILL_FAILED", "composer text mismatch after fill")
    if dry_run:
        ctx.clear_composer(
            "document.querySelector('[contenteditable=\"true\"]')")
        ok(f"composer for {to!r} verified and cleared, nothing sent")
    ctx.press_enter()
    time.sleep(3)
    rest = ctx.js(
        "(() => { const c=document.querySelector("
        "'[contenteditable=\"true\"]'); return c?(c.innerText||''):'gone';"
        "})()")
    if (rest or "").strip():
        fail("SEND_UNCERTAIN",
             "enter pressed but composer still holds text; check the thread")
    ok(f"sent to {to} on messenger")


def send_instagram(ctx, to, _href, body, dry_run):
    box = ctx.js(
        "(() => { const rows=[...document.querySelectorAll("
        "'div[role=\"button\"]')]; const r=rows.find(d=>{const b="
        "d.getBoundingClientRect(); if(b.y<100) return false; const t="
        "(d.innerText||'').split('\\n')[0].trim(); return t.toLowerCase()==="
        "__TO__;}); if(!r) return null; const b="
        "r.getBoundingClientRect(); return {x:b.x+b.width/2, y:b.y+"
        "b.height/2}; })()".replace("__TO__", json.dumps(to.lower())))
    if not box:
        fail("SEND_RECIPIENT_GONE",
             f"no instagram thread row exactly named {to!r}")
    ctx.click_at(box["x"], box["y"])
    opened = ctx.wait_js(
        "location.href.includes('/direct/t/') ? location.href : ''", 12)
    if not opened:
        fail("SEND_THREAD_UNCHANGED", "thread did not open on click")
    comp = ctx.wait_js(
        "(() => { const els=[...document.querySelectorAll("
        "'[contenteditable=\"true\"],[role=\"textbox\"]')]; const vis=els"
        ".filter(e=>{const r=e.getBoundingClientRect(); return r.width>0&&"
        "r.height>0;}); return vis.length ? 'yes' : ''; })()", 12)
    if not comp:
        fail("SEND_NO_COMPOSER", "thread opened but no composer found")
    after = ctx.fill_composer(
        "(() => { const els=[...document.querySelectorAll("
        "'[contenteditable=\"true\"],[role=\"textbox\"]')]; const vis=els"
        ".filter(e=>{const r=e.getBoundingClientRect(); return r.width>0&&"
        "r.height>0;}); return vis.length?vis[vis.length-1]:null; })()",
        body)
    if body.strip()[:40] not in (after or ""):
        ctx.clear_composer(
            "document.querySelector('[contenteditable=\"true\"]')")
        fail("SEND_FILL_FAILED", "composer text mismatch after fill")
    if dry_run:
        ctx.clear_composer(
            "document.querySelector('[contenteditable=\"true\"]')")
        ok(f"composer for {to!r} verified and cleared, nothing sent")
    ctx.press_enter(ctrl=True)
    time.sleep(3)
    rest = ctx.js(
        "(() => { const c=document.querySelector("
        "'[contenteditable=\"true\"]'); return c?(c.innerText||''):'gone';"
        "})()")
    if (rest or "").strip():
        fail("SEND_UNCERTAIN",
             "enter pressed but composer still holds text; check the thread")
    ok(f"sent to {to} on instagram")


def _compose_box_js():
    return ("(() => { const els=[...document.querySelectorAll("
            "'div[role=\"textbox\"]')]; const vis=els.filter(e=>{const r=e."
            "getBoundingClientRect(); return r.width>60&&r.height>60;}); "
            "return vis.length?vis[vis.length-1]:null; })()")


def send_gmail_compose(ctx, address, body, dry_run):
    """New message via the compose URL (no thread needed)."""
    import urllib.parse as _up
    ctx.cmd("Page.navigate",
            {"url": "https://mail.google.com/mail/u/0/?view=cm&fs=1&to="
             + _up.quote(address)})
    comp = ctx.wait_js(
        "(() => { const els=[...document.querySelectorAll("
        "'div[role=\"textbox\"]')]; const vis=els.filter(e=>{const r=e."
        "getBoundingClientRect(); return r.width>60&&r.height>60;}); return "
        "vis.length?'yes':''; })()", 15)
    if not comp:
        fail("SEND_NO_COMPOSER", "compose window did not open")
    to_now = ctx.js(
        "(() => { const d=document.querySelector('div[aria-label=\"To\"]');"
        " const v=(d&&(d.value||d.textContent)||''); const i=document."
        "querySelector('input[aria-label=\"To recipients\"]'); return "
        "(v+' '+(i&&i.value||'')).replace(/Loading\\.\\.\\./g,''); })()")
    if address.lower() not in (to_now or "").lower():
        fail("SEND_RECIPIENT_MISMATCH",
             f"compose To field shows {to_now!r}, expected {address!r}")
    after = ctx.fill_composer(_compose_box_js(), body)
    if body.strip()[:40] not in (after or ""):
        ctx.clear_composer(_compose_box_js())
        fail("SEND_FILL_FAILED", "compose text mismatch after fill")
    if dry_run:
        ctx.clear_composer(_compose_box_js())
        ok(f"gmail compose to {address!r} verified and cleared, nothing sent")
    send_btn = ctx.js(
        "(() => { const bs=[...document.querySelectorAll("
        "'[role=\"button\"]')]; const b=bs.find(x=>{const l=("
        "x.getAttribute('aria-label')||'').toLowerCase(); return l.startsWith"
        "('send');}); if(!b) return null; const r=b.getBoundingClientRect();"
        " return {x:r.x+r.width/2, y:r.y+r.height/2}; })()")
    if send_btn:
        ctx.click_at(send_btn["x"], send_btn["y"])
    else:
        ctx.js(_compose_box_js() + ".focus()")
        import time as _t
        _t.sleep(0.3)
        ctx.press_enter(ctrl=True)
    import time as _t2
    _t2.sleep(4)
    gone = ctx.js(
        "(() => { const els=[...document.querySelectorAll("
        "'div[role=\"textbox\"]')]; const vis=els.filter(e=>{const r=e."
        "getBoundingClientRect(); return r.width>60&&r.height>60;}); return "
        "vis.length?'open':'closed'; })()")
    if gone != "closed":
        fail("SEND_UNCERTAIN",
             "send pressed but compose still open; check Sent/Drafts")
    ok(f"gmail sent to {address}")


def _gmail_find_row(ctx, to):
    """First inbox row from `to` with its thread id, or None."""
    return ctx.js(
        "(() => { const rows=[...document.querySelectorAll('tr.zA')]; const "
        "needle=__TO__; for (const r of rows) { "
        "const who=(r.querySelector('.yW')?.textContent||'').toLowerCase(); "
        "if (who.includes(needle)) { const log=r.getAttribute('jslog')||''; "
        "let m=/thread-f:(\\d+)/.exec(log); if(!m){ try{ const b=/1:([A-Za-z0-9"
        "+/=\\-_]+)/.exec(log); if(b){ const dec=atob(b[1]); const m2=/#thread-f:"
        "(\\d+)/.exec(dec); if(m2) m=m2; } }catch(e){} } return m ? "
        "{tid:m[1], who:r.querySelector('.yW')?.textContent||''} : null; } } "
        "return null; })()".replace("__TO__", json.dumps(to.lower())))


def send_gmail(ctx, to, _href, body, dry_run):
    if "@" in (to or ""):
        send_gmail_compose(ctx, to.strip(), body, dry_run)
        return
    row = _gmail_find_row(ctx, to)
    if row is None:
        # Maybe sitting inside a thread: go back to the list and retry once.
        ctx.cmd("Page.navigate",
                {"url": "https://mail.google.com/mail/u/0/#inbox"})
        import time as _t
        _t.sleep(6)
        row = _gmail_find_row(ctx, to)
    if not row or not row.get("tid"):
        fail("SEND_RECIPIENT_GONE",
             f"no gmail row from {to!r} with a thread id")
    # Direct thread-URL navigation falls back to the inbox (numeric
    # thread-f ids are not URL ids) — open with a row click instead.
    opened = ctx.js(
        "(() => { const rows=[...document.querySelectorAll('tr.zA')]; const "
        "needle=__TO__; for (const r of rows) { "
        "const who=(r.querySelector('.yW')?.textContent||'').toLowerCase(); "
        "if (who.includes(needle)) { const s=r.querySelector('.bog'); "
        "(s||r).click(); return 'clicked'; } } "
        "return ''; })()".replace("__TO__", json.dumps(to.lower())))
    if not opened:
        fail("SEND_THREAD_UNCHANGED", "could not click the gmail row")
    import time as _t2
    _t2.sleep(5)
    reply = ctx.wait_js(
        "(() => { const bs=[...document.querySelectorAll("
        "'[role=\"button\"],[role=\"link\"]')]; const b=bs.find(x=>{const l=("
        "x.getAttribute('aria-label')||x.textContent||'').trim().toLowerCase();"
        " return l==='reply'||l.startsWith('reply ');}); return b?'yes':''; })()",
        15)
    if not reply:
        fail("SEND_NO_COMPOSER", "thread opened but no Reply button found")
    ctx.js(
        "(() => { const bs=[...document.querySelectorAll("
        "'[role=\"button\"],[role=\"link\"]')]; const b=bs.find(x=>{const l=("
        "x.getAttribute('aria-label')||x.textContent||'').trim().toLowerCase();"
        " return l==='reply'||l.startsWith('reply ');}); if(b) b.click(); })()")
    comp = ctx.wait_js(
        "(() => { const els=[...document.querySelectorAll("
        "'div[role=\"textbox\"]')]; const vis=els.filter(e=>{const r=e."
        "getBoundingClientRect(); return r.width>60&&r.height>60;}); return "
        "vis.length?'yes':''; })()", 12)
    if not comp:
        fail("SEND_NO_COMPOSER", "reply clicked but no compose box opened")
    box = ("(() => { const els=[...document.querySelectorAll("
           "'div[role=\"textbox\"]')]; const vis=els.filter(e=>{const r=e."
           "getBoundingClientRect(); return r.width>60&&r.height>60;}); "
           "return vis.length?vis[vis.length-1]:null; })()")
    after = ctx.fill_composer(box, "\n\n" + body)
    if body.strip()[:40] not in (after or ""):
        ctx.clear_composer(box)
        fail("SEND_FILL_FAILED", "compose text mismatch after fill")
    if dry_run:
        ctx.clear_composer(box)
        ctx.cmd("Page.navigate",
                {"url": "https://mail.google.com/mail/u/0/#inbox"})
        ok(f"gmail reply to {row['who']!r} verified and cleared, nothing sent")
    send_btn = ctx.js(
        "(() => { const bs=[...document.querySelectorAll("
        "'[role=\"button\"]')]; const b=bs.find(x=>{const l=("
        "x.getAttribute('aria-label')||'').toLowerCase(); return l.startsWith"
        "('send');}); if(!b) return null; const r=b.getBoundingClientRect();"
        " return {x:r.x+r.width/2, y:r.y+r.height/2}; })()")
    if send_btn:
        ctx.click_at(send_btn["x"], send_btn["y"])
    else:
        ctx.js(box + ".focus()")
        time.sleep(0.3)
        ctx.press_enter(ctrl=True)
    time.sleep(4)
    gone = ctx.js(
        "(() => { const els=[...document.querySelectorAll("
        "'div[role=\"textbox\"]')]; const vis=els.filter(e=>{const r=e."
        "getBoundingClientRect(); return r.width>60&&r.height>60;}); return "
        "vis.length?'open':'closed'; })()")
    if gone != "closed":
        fail("SEND_UNCERTAIN",
             "send pressed but compose still open; check the thread")
    ok(f"gmail reply sent re {row['who']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True,
                    choices=["gmail", "messenger", "instagram"])
    ap.add_argument("--to", required=True)
    ap.add_argument("--href", default="")
    ap.add_argument("--body", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--send", action="store_true")
    args = ap.parse_args()
    if not args.body.strip():
        fail("SEND_MALFORMED", "empty body")
    try:
        ctx = Ctx(TAB_FRAG[args.channel])
    except Exception as exc:
        fail("SEND_NO_CDP", f"browser CDP unreachable: {exc}"[:160])
    try:
        if args.channel == "messenger":
            send_messenger(ctx, args.to, args.href, args.body, args.dry_run)
        elif args.channel == "instagram":
            send_instagram(ctx, args.to, args.href, args.body, args.dry_run)
        else:
            send_gmail(ctx, args.to, args.href, args.body, args.dry_run)
    except SystemExit:
        raise
    except Exception as exc:
        fail("SEND_FAILED", str(exc))
    finally:
        ctx.close()


if __name__ == "__main__":
    main()
