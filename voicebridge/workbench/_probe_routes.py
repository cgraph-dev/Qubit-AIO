import json
import sys

from voicebridge.workbench import workbench_routes as WR

rows = WR.workbench_routes()
handlers = WR.workbench_handlers()
print("routes:", len(rows))
for m, p, h in rows:
    state = "OK" if callable(handlers.get(h)) else "MISSING_HANDLER"
    print(f"  {m:5s} {p:52s} -> {h}  {state}")
missing = [h for m, p, h in rows if not callable(handlers.get(h))]
print("missing handlers:", missing)

# Each handler must degrade to a typed refusal with NO cfg / a broken adapter,
# never raise and never return an empty success.
bad = 0
for name, fn in handlers.items():
    try:
        status, payload = fn(None, {}, {}, {"id": "", "path": ""})
    except Exception as exc:
        print(f"  FAIL {name} raised {type(exc).__name__}: {exc}")
        bad += 1
        continue
    ok = isinstance(payload, dict)
    code = payload.get("code") if ok else None
    print(f"  {name}: status={status} code={code} "
          f"state={payload.get('state') if ok else '?'}")
    if not ok:
        print(f"  FAIL {name} returned non-dict")
        bad += 1
    elif payload.get("ok") is True and not payload.get("files"):
        # an EMPTY project answer is allowed only with a named code
        if payload.get("code") is None and not payload.get("entries") \
                and not payload.get("sessions"):
            print(f"  FAIL {name} returned a bare empty success")
            bad += 1
print("\nROUTE GATE:", "PASS" if not bad else f"FAIL ({bad})")
sys.exit(1 if bad else 0)
