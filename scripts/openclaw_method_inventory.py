#!/usr/bin/env python3
"""Deterministic per-method inventory of the contractual upstream gateway surface.

Produces one row per method in the contractual-pin ``CORE_GATEWAY_METHOD_SPECS``
table (``src/gateway/methods/core-descriptors.ts``), with the upstream request
schema chain (method -> validator -> schema -> fields) resolved from the pinned
clone wherever the clone proves it, plus the authoritative terminal disposition
read from the regenerated ``docs/archive/2026-09-21/QUBIT_OPENCLAW_UPGRADE_MANIFEST.json``.

The inventory is a work list, not a completion claim: a method whose manifest
disposition is ``OPEN`` stays ``OPEN`` here. Schema entries are labelled
``VERIFIED`` / ``INFERRED`` / ``UNKNOWN`` and never promoted by name-guess alone.

Deterministic: no timestamps, stable sort order; identical upstream revisions
produce a byte-identical file. Run:

    python scripts/openclaw_method_inventory.py --clone /path/to/openclaw-clone
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import defaultdict
from pathlib import Path

GATEWAY_DESCRIPTOR = "src/gateway/methods/core-descriptors.ts"
SCHEMA_PACKAGE = "packages/gateway-protocol/src"
ARRAY_START_RE = re.compile(r"CORE_GATEWAY_METHOD_SPECS\s*=\s*\[")
ARRAY_END = "\n] as const satisfies"
ROW_RE = re.compile(
    r'\s*\[\s*"([^"]+)"\s*,\s*(null|"[^"]+")\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"'
    r'\s*(?:,\s*([A-Za-z_][A-Za-z0-9_]*))?')


def git_show(clone: Path, rev: str, path: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(clone), "show", f"{rev}:{path}"],
        capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise SystemExit(
            f"git show {rev[:12]}:{path} failed: {proc.stderr.strip()[:200]}")
    return proc.stdout


def git_ls(clone: Path, rev: str, prefix: str) -> list:
    """Repo-relative paths of every file under *prefix* at *rev*.

    Reading the tree at the named commit (rather than the clone's working tree)
    makes ``--pin`` fully determine the result: which revision the clone happens
    to be checked out at cannot change the inventory.
    """
    proc = subprocess.run(
        ["git", "-C", str(clone), "ls-tree", "-r", "--name-only", rev,
         "--", prefix],
        capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise SystemExit(
            f"git ls-tree {rev[:12]} -- {prefix} failed: "
            f"{proc.stderr.strip()[:200]}")
    return [ln for ln in proc.stdout.splitlines() if ln.endswith(".ts")]


def tree_texts(clone: Path, rev: str, prefix: str) -> dict:
    """``repo-relative path -> blob text`` for the ``.ts`` files under *prefix*.

    Excludes ``*.test.ts`` (matching the scanner's original intent) so a test
    fixture can never be mistaken for a production schema/handler.
    """
    out = {}
    for rel in git_ls(clone, rev, prefix):
        if rel.endswith(".test.ts"):
            continue
        out[rel] = git_show(clone, rev, rel)
    return out


def balanced(text: str, start: int) -> int:
    depth = 0
    i = start
    seen = False
    while i < len(text):
        c = text[i]
        if c in "([{":
            depth += 1
            seen = True
        elif c in ")]}":
            depth -= 1
            if seen and depth == 0:
                return i + 1
        i += 1
    return len(text)


def parse_rows(blob: str) -> list:
    m = ARRAY_START_RE.search(blob)
    if m is None:
        raise SystemExit("no CORE_GATEWAY_METHOD_SPECS array")
    arr = blob[m.end():]
    end = arr.find(ARRAY_END)
    if end == -1:
        raise SystemExit("unterminated CORE_GATEWAY_METHOD_SPECS array")
    arr = arr[:end]
    base_line = blob[:m.end()].count("\n") + 1
    rows = []
    for mt in ROW_RE.finditer(arr):
        rows.append(dict(
            name=mt.group(1),
            family=(None if mt.group(2) == "null" else mt.group(2).strip('"')),
            scope=mt.group(3), since=mt.group(4), policy=mt.group(5),
            descriptor_line=base_line + arr[:mt.start()].count("\n")))
    names = [r["name"] for r in rows]
    if len(names) != len(set(names)):
        raise SystemExit("duplicate method names in descriptor table")
    return rows


def extract_fields(definition: str) -> list:
    oi = definition.find("{")
    if oi < 0:
        return []
    end = balanced(definition, oi) - 1
    body = definition[oi + 1:end]
    fields, depth, cur = [], 0, ""
    for ch in body:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            fields.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        fields.append(cur)
    out = []
    for f in fields:
        fm = re.match(r'\s*"?([A-Za-z0-9_$]+)"?\s*:\s*(.*)', f, re.S)
        if fm:
            out.append(dict(name=fm.group(1),
                            type_expr=" ".join(fm.group(2).split())))
    return out


def build(clone: Path, pin: str) -> dict:
    blob = git_show(clone, pin, GATEWAY_DESCRIPTOR)
    rows = parse_rows(blob)
    # Every source is read from the named commit's tree, never the clone's
    # working tree, so the clone checkout state cannot alter the inventory.
    proto_texts = tree_texts(clone, pin, SCHEMA_PACKAGE)
    gateway_texts = tree_texts(clone, pin, "src/gateway")

    schema_defs: dict = {}
    for rel, text in proto_texts.items():
        for mt in re.finditer(r'export const (\w+)\s*(?::[^=\n]{0,240}?)?=\s*', text):
            name = mt.group(1)
            if not name.endswith("Schema"):
                continue
            end = balanced(text, mt.end() - 1)
            schema_defs.setdefault(name, dict(
                file=rel,
                line=text[:mt.start()].count("\n") + 1,
                definition=text[mt.start():end]))

    val_to_schema: dict = {}
    val_re = re.compile(
        r'export const (validate\w+)\s*=\s*(?:lazy)?[Cc]ompile'
        r'(?:<[^>]*>)?\(\s*(?:[A-Za-z_]\w*\.)?(\w+)')
    for rel, text in proto_texts.items():
        for mt in val_re.finditer(text):
            val_to_schema.setdefault(mt.group(1), dict(
                schema=mt.group(2), file=rel,
                line=text[:mt.start()].count("\n") + 1))

    scan = dict(gateway_texts)
    scan.update(proto_texts)
    bindings: dict = defaultdict(
        lambda: {"request": set(), "response": set(), "files": set()})
    b_define = re.compile(
        r'defineValidatedGatewayMethod\(\s*"([^"]+)"\s*,\s*([A-Za-z0-9_]+)')
    b_assert = re.compile(
        r'assertValidParams\([^()]*?,\s*([A-Za-z0-9_]+)\s*,\s*"([^"]+)"')
    for rel, t in scan.items():
        for mt in b_define.finditer(t):
            bindings[mt.group(1)]["request"].add(mt.group(2))
            bindings[mt.group(1)]["files"].add(rel)
        for mt in b_assert.finditer(t):
            bindings[mt.group(2)]["request"].add(mt.group(1))
            bindings[mt.group(2)]["files"].add(rel)

    result_validators: dict = {}
    for rel, t in proto_texts.items():
        for mt in re.finditer(
                r'export const (validate\w+Result)\s*=\s*(?:lazy)?[Cc]ompile'
                r'(?:<[^>]*>)?\(\s*(?:[A-Za-z_]\w*\.)?(\w+)', t):
            result_validators.setdefault(mt.group(1), dict(
                schema=mt.group(2), file=rel,
                line=t[:mt.start()].count("\n") + 1))
    result_usage: dict = defaultdict(set)
    for rel, t in scan.items():
        for mt in re.finditer(r'\b(validate\w+Result)\b', t):
            result_usage[rel].add(mt.group(1))

    def resolve(valname: str, kind: str) -> dict:
        vs = val_to_schema.get(valname)
        if not vs:
            return dict(validator=valname, schema=None, status="UNKNOWN",
                        kind=kind)
        out = dict(validator=valname, schema=vs["schema"],
                   schema_file=vs["file"], schema_line=vs["line"],
                   status="VERIFIED", kind=kind)
        sd = schema_defs.get(vs["schema"])
        if sd:
            out["schema_definition_file"] = sd["file"]
            out["schema_definition_line"] = sd["line"]
            out["schema_definition"] = sd["definition"]
            out["fields"] = extract_fields(sd["definition"])
        return out

    inventory = []
    for r in rows:
        b = bindings.get(r["name"], {"request": set(), "response": set(),
                                     "files": set()})
        reqs = [resolve(v, "request") for v in sorted(b["request"])]
        rv = set()
        for rel in b["files"]:
            rv |= result_usage.get(rel, set())
        resps = [resolve(v, "response") for v in sorted(rv)]
        inventory.append(dict(
            name=r["name"], family=r["family"], scope=r["scope"],
            since=r["since"], policy=r["policy"],
            descriptor_file=GATEWAY_DESCRIPTOR,
            descriptor_line=r["descriptor_line"],
            handler_files=sorted(b["files"]),
            request_validators=sorted(b["request"]),
            request_schemas=reqs,
            response_validators=sorted(rv),
            response_schemas=resps))
    return inventory


def load_dispositions(manifest: Path) -> dict:
    data = json.loads(manifest.read_text(encoding="utf-8"))
    methods = data.get("pinned_surface_disposition", {}).get("methods", {})
    out = {}
    if isinstance(methods, dict):
        items = methods.items()
    else:
        items = ((m["method"], m) for m in methods)
    for name, m in items:
        out[name] = {
            "status": m.get("status"),
            "evidence": m.get("evidence"),
            "basis": m.get("basis"),
        }
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--clone", required=True,
                    help="pristine read-only upstream clone")
    ap.add_argument("--pin", default=None,
                    help="contractual-pin commit (default: from manifest)")
    ap.add_argument("--manifest",
                    default="docs/generated/QUBIT_OPENCLAW_UPGRADE_MANIFEST.json")
    ap.add_argument("--out",
                    default="docs/generated/QUBIT_OPENCLAW_METHOD_INVENTORY.json")
    args = ap.parse_args(argv)

    clone = Path(args.clone).resolve()
    if not (clone / ".git").exists():
        raise SystemExit(f"not a git clone: {clone}")
    manifest = Path(args.manifest)
    pin = args.pin
    if pin is None:
        if not manifest.is_file():
            raise SystemExit(
                "no manifest to read the contractual pin from; pass --pin")
        pin = json.loads(manifest.read_text(encoding="utf-8"))[
            "revisions"]["contractual_pin"]["commit"]
    dispositions = load_dispositions(manifest) if manifest.is_file() else {}

    methods = build(clone, pin)
    counts = {"IMPLEMENTED": 0, "SAFE_EQUIVALENT": 0, "OPERATOR_ONLY": 0,
              "OPEN": 0}
    for m in methods:
        d = dispositions.get(m["name"], {})
        m["disposition"] = d.get("status", "OPEN")
        m["disposition_basis"] = d.get("basis")
        m["disposition_evidence"] = d.get("evidence")
        counts[m["disposition"]] = counts.get(m["disposition"], 0) + 1

    schema_verified = sum(
        1 for m in methods
        if any(s.get("status") == "VERIFIED" for s in m["request_schemas"]))
    schema_unknown = sum(1 for m in methods if not m["request_schemas"])

    doc = {
        "schema": "qubit-openclaw-method-inventory/1",
        "generated_by": "scripts/openclaw_method_inventory.py",
        "description": (
            "Per-method inventory of the contractual OpenClaw gateway surface. "
            "Dispositions are authoritative (read from "
            "QUBIT_OPENCLAW_UPGRADE_MANIFEST.json); schema rows are proven "
            "request chains where the upstream clone yields them and UNKNOWN "
            "otherwise. OPEN rows are the work list, not a completion claim."),
        "upstream": {
            "commit": pin,
            "method_table": GATEWAY_DESCRIPTOR,
            "schema_package": SCHEMA_PACKAGE,
        },
        "method_count": len(methods),
        "disposition_counts": counts,
        "schema_coverage": {
            "request_schema_verified": schema_verified,
            "request_schema_absent": schema_unknown,
            "note": ("Response schemas are associated at handler-file "
                     "granularity upstream and are not proven per method; they "
                     "are not counted as coverage."),
        },
        "methods": methods,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n",
                   encoding="utf-8")
    print(f"wrote {out}")
    print(f"methods={len(methods)} dispositions={counts}")
    print(f"request schema verified={schema_verified} "
          f"absent={schema_unknown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
