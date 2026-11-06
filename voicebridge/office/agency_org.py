"""The agency org chart: 18 divisions, their personas, and the building they fill.

This module is **data, plus a pure projection of that data onto the office's own
org model**. It reads the ported skills' frontmatter (the single source of truth:
``qubit_runtime/skills/agency/<division>/<persona>/SKILL.md``), which carries the
division label/icon/colour from upstream ``divisions.json`` and each persona's
own colour and emoji. It does not write to the store, start an agent, or touch a
renderer.

Two consumers are foreseen and both are served by one projection:

* the **Office** wants floors, departments, rooms and seats — what
  :func:`plan_building` returns, in exactly the vocabulary
  ``voicebridge.office.room_layout`` and ``voicebridge.office.org`` already speak;
* a **sheet / catalog** surface wants the divisions and personas as rows —
  :func:`divisions` and :func:`personas`.

Why the plan is shaped the way it is (every number below is derived, never
chosen):

* **One division = one department = one floor.** A division is the unit a
  director owns, and ``org.department_create`` gives a department one floor.
* **One persona = one seat.** 283 personas become 283 non-CEO seats, one
  director per division and a manager per unit of at most ``MAX_SPAN`` personas.
  The director is a ``director_portfolio`` — the only seat type
  ``org.department_create`` accepts as a department's owner (D35).
* **The span limit splits a division, it does not grow a room.** ``_check_span``
  refuses an 8th direct report (D34), so a 66-persona division is one director,
  ``ceil(66 / 7) = 10`` managers, and ten work rooms — not one enormous room.
  That is the same arithmetic ``room_layout.staffing`` performs for headcount.
* **The boardroom holds the executive chain.** The owner is not an agent, so the
  one ``ceo`` seat in the model is the **chief of staff** persona — the seat that
  reports to the owner and supervises every division director. The other 17
  directors join it in the boardroom. This is a choice of *who stands in the
  seat*, documented here rather than buried in a caller.

Integration point (deliberately not wired into ``tower.py``)
------------------------------------------------------------
:func:`plan_building` returns ``{"floors": ..., "programme": ..., "seats": ...}``
where ``programme`` is a ``room_layout.programme``-shaped list, so a caller can

1. create one floor per division (``org.floor_create``, ``role="operations"``);
2. create one department per division on its floor, ``division_kind`` from
   :func:`division_kind_for` (all six values are already legal in
   ``store.DIVISION_KINDS``);
3. create the rooms in ``programme`` per department
   (``org.room_create(department_id, slug_hint, kind=kind)``);
4. ``org.hire`` each seat in ``seats`` with the ``seat_type``, ``supervisor``
   slug and room this module gives.

Every plan it emits is checked with ``room_layout.check`` before it is
returned, so a caller cannot be handed a building the office would refuse.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import room_layout, store

SCHEMA = "qubit-office-agency-org/1"

#: Where the ported skills live, relative to the repo root (``qubit_runtime/``).
SKILLS_SUBDIR = Path("skills") / "agency"

#: The persona that stands in the office's single CEO seat.
#:
#: The office model allows exactly one ``ceo`` seat and it reports to the owner,
#: not to an agent (``org.py`` D2/C30). Upstream ships a chief-of-staff persona
#: whose own mission is "run the executive office for the owner", which is
#: precisely that seat — so it is the one that takes it, and every division
#: director reports to it. Named explicitly instead of derived so a future
#: change to the roster fails loudly in ``check`` rather than silently
#: restaffing the building.
CEO_PERSONA = "specialized-chief-of-staff"

#: The seats that supervise the division directors.
#:
#: The CEO may carry at most ``store.MAX_SPAN`` (7) direct reports (D34), and
#: there are 18 divisions — so the CEO cannot supervise them all, and neither can
#: a single director. The office's own vocabulary has the tier for this: a
#: ``director_functional`` reporting to the CEO, with the ``director_portfolio``
#: seats reporting to it. Three such seats carry 18/3 = 6 divisions each, inside
#: the span, and the CEO carries three reports.
#:
#: The persona is the roster's line-management role (the orchestrator). Only one
#: persona exists for that job, so it takes the first seat and the projector gives
#: the others the same persona's name with a `-2` / `-3` suffix — the store needs
#: distinguishable slugs, and inventing imaginary people would be worse.
FUNCTIONAL_DIRECTOR_PERSONA = "agents-orchestrator"

#: Division -> ``store.DIVISION_KINDS`` value. Every key here is a legal kind;
#: ``check`` refuses a division left unclassified rather than defaulting it,
#: because a default would silently file a legal department under "product".
DIVISION_KIND = {
    "academic": "intelligence",
    "design": "content",
    "engineering": "product",
    "finance": "trading",
    "game-development": "product",
    "gis": "intelligence",
    "healthcare": "service",
    "marketing": "content",
    "paid-media": "content",
    "product": "product",
    "project-management": "meta",
    "research": "intelligence",
    "sales": "trading",
    "security": "service",
    "spatial-computing": "product",
    "specialized": "meta",
    "support": "service",
    "testing": "meta",
}

#: Floor role per division. ``store.FLOOR_ROLES`` is
#: ``operations, intelligence, director, executive, rnd``; the executive floor is
#: the boardroom's, so the 18 division floors are assigned among the rest.
DIVISION_FLOOR_ROLE = {
    "academic": "intelligence",
    "design": "operations",
    "engineering": "rnd",
    "finance": "operations",
    "game-development": "rnd",
    "gis": "intelligence",
    "healthcare": "operations",
    "marketing": "operations",
    "paid-media": "operations",
    "product": "rnd",
    "project-management": "operations",
    "research": "intelligence",
    "sales": "operations",
    "security": "operations",
    "spatial-computing": "rnd",
    "specialized": "operations",
    "support": "operations",
    "testing": "rnd",
}


def division_kind_for(division: str) -> str:
    """The ``division_kind`` a division's department is created with."""
    return DIVISION_KIND.get(division, "")


def floor_role_for(division: str) -> str:
    """The ``role`` a division's floor is created with."""
    return DIVISION_FLOOR_ROLE.get(division, "")


# ------------------------------------------------------------------ loading

_REPO_ROOT = Path(__file__).resolve().parents[2]


def skills_root(root: Optional[Path] = None) -> Path:
    """The directory holding the ported division skills."""
    base = Path(root) if root is not None else _REPO_ROOT / "qubit_runtime"
    return base / SKILLS_SUBDIR


def _frontmatter(text: str) -> Dict[str, Any]:
    """Parse the frontmatter of a SKILL.md without importing the skill system.

    The loader's parser is ``qubit_runtime.agent.skill_utils.parse_frontmatter``,
    which is the authority; this is a deliberately small reader for the flat
    ``key: value`` / ``metadata.qubit`` block these skills use, so this module
    works with nothing on ``sys.path`` beyond the office package. ``check``
    cross-checks the result against the real loader when it is importable.
    """
    text = text.removeprefix("\ufeff")
    if not text.startswith("---"):
        return {}
    end = re.search(r"\n---\s*\n", text[3:])
    if not end:
        return {}
    block = text[3:end.start() + 3]
    values: Dict[str, Any] = {}
    section: Optional[str] = None
    for line in block.split("\n"):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        key, sep, raw = line.strip().partition(":")
        if not sep:
            continue
        if indent == 0:
            section = key if not raw.strip() else None
            if raw.strip():
                values[key] = _scalar(raw)
            continue
        values[f"{section}.{key}" if section else key] = _scalar(raw)
    return values


def _scalar(raw: str) -> Any:
    raw = raw.strip()
    if not raw:
        return ""
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [_scalar(part) for part in re.findall(r'"(?:[^"\\]|\\.)*"|[^,]+', inner)]
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        return json.loads(raw)
    if len(raw) >= 2 and raw[0] == raw[-1] == "'":
        return raw[1:-1]
    return raw


def load(root: Optional[Path] = None) -> Dict[str, Any]:
    """Read every ported persona into ``{divisions: {...}, personas: [...]}``.

    Returns ``{"schema", "source", "divisions", "personas"}`` where a division is
    ``{slug, label, icon, color, kind, floor_role, personas: [slug, ...]}`` and a
    persona is the record :func:`personas` documents. An empty result (no skills
    on disk) is reported as ``divisions == {}`` — never a demo chart.
    """
    base = skills_root(root)
    personas: List[Dict[str, Any]] = []
    for md in sorted(base.rglob("SKILL.md")) if base.is_dir() else []:
        fm = _frontmatter(md.read_text(encoding="utf-8"))
        division = md.parent.parent.name
        personas.append({
            "slug": fm.get("name") or md.parent.name,
            "name": fm.get("metadata.persona_name") or md.parent.name,
            "emoji": fm.get("metadata.persona_emoji") or "",
            "color": fm.get("metadata.persona_color") or "",
            "vibe": fm.get("metadata.persona_vibe") or "",
            "description": fm.get("metadata.upstream_description") or "",
            "division": division,
            "division_label": fm.get("metadata.division_label") or division,
            "division_icon": fm.get("metadata.division_icon") or "",
            "division_color": fm.get("metadata.division_color") or "",
            "category": fm.get("metadata.category") or "",
            "path": md.parent.relative_to(base).as_posix(),
        })

    divisions: Dict[str, Dict[str, Any]] = {}
    for persona in personas:
        slug = persona["division"]
        entry = divisions.setdefault(slug, {
            "slug": slug,
            "label": persona["division_label"],
            "icon": persona["division_icon"],
            "color": persona["division_color"],
            "kind": division_kind_for(slug),
            "floor_role": floor_role_for(slug),
            "personas": [],
        })
        entry["personas"].append(persona["slug"])

    return {"schema": SCHEMA, "source": str(base), "divisions": divisions,
            "personas": personas}


# ------------------------------------------------------------- public views


def divisions(root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """The 18 divisions, sorted by slug, each with label/icon/colour and members."""
    found = load(root)["divisions"]
    return [found[slug] for slug in sorted(found)]


def personas(division: Optional[str] = None,
             root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Persona records, optionally filtered to one division.

    Each is ``{slug, name, emoji, color, vibe, description, division,
    division_label, division_icon, division_color, category, path}`` — the
    catalogue surface's row.
    """
    rows = load(root)["personas"]
    return [p for p in rows if division is None or p["division"] == division]


def division_colors(root: Optional[Path] = None) -> Dict[str, str]:
    """``{division_slug: hex}`` — the upstream brand colour per division."""
    return {d["slug"]: d["color"] for d in divisions(root)}


# ---------------------------------------------------------------- building


def _units(n: int, capacity: int) -> List[int]:
    """Split ``n`` personas into ``k`` units of at most ``capacity``, none of size 1.

    A unit of 1 is a manager with no team, which ``room_layout.check`` refuses,
    so the count is distributed rather than chunked: ``k`` is the least number of
    units with ``k * capacity >= n``, and the remainder is spread one per unit
    (larger units first). Every persona lands in exactly one unit — the sizes
    always sum to ``n``.
    """
    if n <= 0:
        return []
    if n == 1:
        return [1]
    k = -(-n // capacity)
    base, extra = divmod(n, k)
    sizes = [base + 1] * extra + [base] * (k - extra)
    assert sum(sizes) == n and all(1 < s <= capacity for s in sizes), (n, capacity, sizes)
    return sizes


def _chunks(items: List[Dict[str, Any]], size: int) -> List[List[Dict[str, Any]]]:
    """Yield ``items`` in contiguous groups whose sizes come from :func:`_units`."""
    sizes = _units(len(items), size)
    out, start = [], 0
    for count in sizes:
        out.append(items[start:start + count])
        start += count
    return out



def _span() -> int:
    """The office's hard limit on direct reports (D34), read from the store."""
    return int(store.MAX_SPAN)


def _seat(persona, seat_type, supervisor, division, room, **extra):
    """One seat record. Every non-CEO seat names its supervisor (C30)."""
    seat = {"slug": persona["slug"], "name": persona["name"],
            "seat_type": seat_type, "supervisor": supervisor,
            "division": division, "room": room,
            "persona_emoji": persona.get("emoji", ""),
            "persona_color": persona.get("color", "")}
    seat.update(extra)
    return seat


def plan_building(root: Optional[Path] = None) -> Dict[str, Any]:
    """The building this roster fills, as office vocabulary. Pure — no store I/O.

    Returns::

        {
          "schema", "headcount", "floors_needed", "rooms_needed",
          "floors": [ {number, slug, name, role, division} ],
          "programme": [ room_layout.programme-shaped rooms, in floor order ],
          "seats": [ {slug, name, seat_type, supervisor, division, room,
                      persona_emoji, persona_color} ],
          "boardroom": {...}, "check": room_layout.check(...) verdict,
          "ok": bool,
        }

    **Every number is derived from two inputs only**: the roster on disk and the
    office's own limits. One persona is one seat — the plan never adds seats for
    people who do not exist and never drops a persona:

    * **headcount** = number of personas;
    * **managers** = ``ceil(workers / (span + 1))`` — a work room is one manager
      plus at most ``span`` leaves (``room_layout.MAX_SPAN``);
    * **portfolio directors** = ``ceil(managers / span)`` — a director carries at
      most ``span`` managers, so a big division splits into more than one
      directorate rather than one over-worked seat. Those extra director seats
      are filled by promoting people the roster already has;
    * **functional directors** = ``ceil(portfolios / span)`` and
      **the CEO** = 1, with ``ceil(functionals / span)`` if the org ever grew
      that large.

    A persona holds exactly one seat. The executive seats are filled first from
    the roster (a director must be somebody the division actually ships), then
    the remaining personas fill manager and worker seats. If the roster cannot
    staff its own executive chain the plan refuses — it never invents a person.
    """
    roster = load(root)
    divs = roster["divisions"]
    empty = {"schema": SCHEMA, "headcount": 0, "floors_needed": 0,
             "rooms_needed": 0, "floors": [], "programme": [], "seats": [],
             "boardroom": {}, "ok": False}
    if not divs:
        return {**empty, "check": {"ok": False, "reason": "no ported personas on disk"}}

    span = _span()
    by_division: Dict[str, List[Dict[str, Any]]] = {}
    for persona in roster["personas"]:
        by_division.setdefault(persona["division"], []).append(persona)

    ceo_pool = [p for p in roster["personas"] if p["slug"] == CEO_PERSONA]
    if not ceo_pool:
        return {**empty, "check": {
            "ok": False,
            "reason": (f"CEO persona {CEO_PERSONA!r} is not in the ported "
                       f"roster; refusing to staff a building with no "
                       f"executive chain")}}

    seats: List[Dict[str, Any]] = []
    floors: List[Dict[str, Any]] = []
    programme: List[Dict[str, Any]] = []
    boardroom: List[Dict[str, Any]] = []
    used: Dict[str, str] = {}          # slug -> seat_type, to guarantee one seat
    ceo = ceo_pool[0]
    used[ceo["slug"]] = "ceo"
    seats.append(_seat(ceo, "ceo", None, ceo["division"], "boardroom"))
    boardroom.append({"role": "ceo", "is_supervisor": True, "slug": ceo["slug"]})

    # --- shape the divisions from the roster, before placing anyone ----------
    def _take(pool, wanted, predicate):
        """Pop up to ``wanted`` personas matching ``predicate`` (roster order)."""
        picked = []
        for persona in pool:
            if len(picked) >= wanted:
                break
            if persona["slug"] not in used and predicate(persona):
                picked.append(persona)
        return picked

    # --- shape every division from its roster, seating nobody yet ----------
    # The counts come from the office's own limit, not from taste: a manager
    # carries at most `span` leaves, a portfolio director at most `span`
    # managers. Seating happens afterwards in one fixed order (CEO, functional
    # directors, portfolio directors, then teams) so no persona can be seated
    # twice.
    shape: List[Dict[str, Any]] = []
    for slug in sorted(divs):
        people = [p for p in by_division.get(slug, []) if p["slug"] not in used]
        n = len(people)
        if n == 0:
            shape.append({"slug": slug, "people": [], "managers": 0, "portfolios": 0})
            continue
        # people = directors + managers + leaves. Solve for managers first: every
        # `span + 1` people beyond the first director form one manager's unit.
        managers = -(-max(0, n - 1) // (span + 1))
        managers = max(1, managers) if n > 1 else 0
        portfolios = max(1, -(-managers // span))
        # Directors are seats too: a division needs `portfolios` people to fill
        # them, so a tiny division is a single directorate of one.
        portfolios = min(portfolios, n)
        shape.append({"slug": slug, "people": people, "managers": managers,
                      "portfolios": portfolios})

    plan: List[Dict[str, Any]] = []
    total_portfolios = sum(s["portfolios"] for s in shape)

    # --- the functional tier above the portfolio directors -------------------
    # One functional director per `span` portfolio directors, so no functional
    # seat is over-span. 0 when the portfolios already fit under the CEO.
    # `plan` is still empty here — the directorates are chosen below — so the
    # count comes from the shape, which is already final.
    functional_count = -(-total_portfolios // span) if total_portfolios > span else 0
    functional: List[Dict[str, Any]] = []
    if functional_count:
        pools = [p for slug in sorted(divs) for p in by_division.get(slug, [])
                 if p["slug"] not in used]
        # Prefer the roster's line-management persona; a second functional seat
        # cannot reuse it (one persona, one seat), so it takes the next free
        # persona and carries the same name with a numeric suffix in its slug.
        preferred_first = [p for p in pools if p["slug"] == FUNCTIONAL_DIRECTOR_PERSONA]
        candidates = preferred_first + [p for p in pools if p["slug"] != FUNCTIONAL_DIRECTOR_PERSONA]
        for i in range(functional_count):
            persona = next((p for p in candidates if p["slug"] not in used), None)
            if persona is None:
                break                     # roster exhausted: `check` will report it
            used[persona["slug"]] = "director_functional"
            slug = persona["slug"] if i == 0 else f"{persona['slug']}-{i + 1}"
            seat = _seat(persona, "director_functional", ceo["slug"], "", "boardroom")
            seat["slug"] = slug
            seats.append(seat)
            functional.append(seat)
            boardroom.append({"role": f"director_functional-{i + 1}",
                              "is_supervisor": True, "slug": slug})
    functional_supers = [f["slug"] for f in functional] or [ceo["slug"]]

    # --- choose and seat each division's directorate ------------------------
    # Runs AFTER the functional tier has taken its people, so a persona already
    # holding a functional seat is not handed a directorate too. The division's
    # own lead persona is preferred; anyone else in the division is promoted
    # when the directorate needs more than one seat.
    floor_of: Dict[str, int] = {}
    director_index = 0
    room_seq = 0
    for s in shape:
        slug = s["slug"]
        people = [p for p in s["people"] if p["slug"] not in used]
        preferred = _DIRECTOR_PREFERENCE.get(slug, ())
        chosen: List[Dict[str, Any]] = []
        for want in preferred:
            if len(chosen) >= s["portfolios"]:
                break
            match = next((p for p in people if p["slug"] == want), None)
            if match is not None:
                chosen.append(match)
        for persona in people:
            if len(chosen) >= s["portfolios"]:
                break
            if persona not in chosen:
                chosen.append(persona)
        for director in chosen:
            used[director["slug"]] = "director_portfolio"
        s["directors"] = chosen
        s["pool"] = [p for p in people if p["slug"] not in used]

    for number, entry in enumerate(shape, start=1):
        slug = entry["slug"]
        div = divs[slug]
        floor_of[slug] = number
        floors.append({"number": number, "slug": slug, "name": div["label"],
                       "role": div["floor_role"] or "operations", "division": slug})
        for director in entry.get("directors", []):
            # Round-robin keeps each functional seat at or below `span` reports
            # (functional_count guarantees enough seats); any other scheme piles
            # them onto the first seat.
            supervisor = functional_supers[director_index % len(functional_supers)]
            director_index += 1
            seats.append(_seat(director, "director_portfolio", supervisor, slug,
                               "boardroom", portfolio=div["label"]))
            boardroom.append({"role": f"director_portfolio-{slug}",
                              "is_supervisor": True, "slug": director["slug"]})
    plan = shape

    # --- work rooms: a manager plus up to `span` leaves ----------------------
    for entry in plan:
        slug = entry["slug"]
        div = divs[slug]
        number = floor_of[slug]
        pool = entry["pool"]
        if not pool:
            continue
        # With one person there is no team; that person sits in the division's
        # room as a worker supervised by its director. A work room may not be a
        # lone manager (C31), so no manager is created for a team of zero.
        units = _units(len(pool), span + 1)
        cursor = 0
        for index, size in enumerate(units):
            unit = pool[cursor:cursor + size]
            cursor += size
            room_slug = f"{slug}-desk-{chr(ord('a') + index) if index < 26 else 'n' + str(index + 1)}"
            room_seq += 1
            if size == 1:
                # A single-person unit: nobody to manage. The office forbids a
                # lone-manager room, so this persona is a desk-less floater in
                # its division — recorded, but with no room of its own.
                persona = unit[0]
                seats.append(_seat(persona, "worker",
                                   (entry["directors"][0]["slug"]
                                    if entry["directors"] else ceo["slug"]),
                                   slug, ""))
                continue
            manager, team = unit[0], unit[1:]
            # Spread the division's managers over its own directorates so no
            # portfolio director exceeds the span (D34).
            directors = entry["directors"] or []
            director_slug = (directors[index % len(directors)]["slug"]
                             if directors else ceo["slug"])
            seats.append(_seat(manager, "manager", director_slug, slug, room_slug))
            detail = [{"role": "manager", "is_supervisor": True, "slug": manager["slug"]}]
            for persona in team:
                seats.append(_seat(persona, "worker", manager["slug"], slug, room_slug))
                detail.append({"role": f"leaf-{len(detail)}", "is_supervisor": False,
                               "slug": persona["slug"]})
            programme.append({
                "kind": "work", "slug_hint": room_slug, "division": slug,
                "floor": number, "seats": len(detail), "seats_detail": detail,
                "why": (f"{len(team)} persona(s) of {div['label']} plus the "
                        f"manager's own desk; a unit past {span} leaves would be "
                        f"an illegal span (D34), so the division splits into "
                        f"units of at most {span}")})

    exec_floor = len(plan) + 1
    programme.insert(0, {
        "kind": "boardroom", "slug_hint": "boardroom", "division": "",
        "floor": exec_floor, "seats": len(boardroom),
        "seats_detail": boardroom + [{"role": "owner", "is_supervisor": False,
                                      "slug": "owner"}],
        "why": ("the executive chain: the CEO, the functional directors and one "
                "portfolio director per directorate (D2/D35). The owner sits "
                "here and is not an agent, so it holds no seat")})
    programme.append({
        "kind": "review", "slug_hint": "review", "division": "",
        "floor": exec_floor, "seats": 0, "seats_detail": [],
        "why": ("a manager ruling on its own team's proposals; a meeting "
                "destination with no permanent seats by design")})

    headcount = len(seats)
    verdict = room_layout.check(programme, headcount)
    return {
        "schema": SCHEMA,
        "headcount": headcount,
        "floors_needed": -(-len(programme) // room_layout.ROOMS_PER_FLOOR),
        "rooms_needed": len(programme),
        "floors": floors,
        "programme": programme,
        "seats": seats,
        "boardroom": programme[0],
        "check": verdict,
        "ok": bool(verdict.get("ok")) and _chain_ok(seats) and _spans_ok(seats, span),
    }


def _spans_ok(seats: List[Dict[str, Any]], span: int) -> bool:
    """No seat carries more than ``span`` direct reports (D34).

    ``org._check_span`` enforces this at hire time; checking it on the plan means
    a caller learns of an illegal org before 283 hires fail at seat 200.
    """
    counts: Dict[str, int] = {}
    for seat in seats:
        sup = seat.get("supervisor")
        if sup:
            counts[sup] = counts.get(sup, 0) + 1
    return all(n <= span for n in counts.values())


_DIRECTOR_PREFERENCE = {
    "engineering": ("engineering-software-architect", "engineering-senior-developer"),
    "design": ("design-ux-architect", "design-ui-designer"),
    "product": ("product-manager",),
    "project-management": ("project-manager-senior", "project-management-project-shepherd"),
    "security": ("security-architect",),
    "testing": ("testing-test-automation-engineer",),
    "sales": ("sales-deal-strategist",),
    "finance": ("chief-financial-officer", "finance-financial-analyst"),
    "support": ("support-support-responder",),
    "research": ("research-synthesist",),
    "academic": ("academic-historian",),
    "marketing": ("marketing-social-media-strategist",),
    "paid-media": ("paid-media-ppc-strategist",),
    "gis": ("gis-solution-engineer",),
    "healthcare": ("healthcare-innovation-strategist",),
    "spatial-computing": ("xr-interface-architect",),
    "game-development": ("game-designer",),
    "specialized": ("specialized-chief-of-staff",),
}


def _chain_ok(seats: List[Dict[str, Any]]) -> bool:
    """Every seat but the CEO has a supervisor that exists, and the chain reaches the CEO.

    The same invariant ``org._check_chain_terminates`` enforces, checked here so a
    caller learns about a broken plan before it hires 283 seats into the store.
    """
    by_slug = {s["slug"]: s for s in seats}
    for seat in seats:
        if seat["seat_type"] == "ceo":
            if seat["supervisor"] is not None:
                return False
            continue
        seen = set()
        cur = seat
        while cur is not None and cur["seat_type"] != "ceo":
            if cur["slug"] in seen or len(seen) > store.CHAIN_MAX_HOPS:
                return False
            seen.add(cur["slug"])
            sup = by_slug.get(cur["supervisor"] or "")
            if sup is None:
                return False
            cur = sup
        if cur is None:
            return False
    return True


def summary(root: Optional[Path] = None) -> Dict[str, Any]:
    """Counts only — for a CLI line, a HUD badge, or a test's assertion."""
    plan = plan_building(root)
    per_division = {}
    for seat in plan["seats"]:
        per_division[seat["division"]] = per_division.get(seat["division"], 0) + 1
    return {
        "schema": SCHEMA,
        "divisions": len(plan["floors"]),
        "personas": len(plan["seats"]),
        "rooms": plan["rooms_needed"],
        "floors_needed": plan["floors_needed"],
        "seat_types": {t: sum(1 for s in plan["seats"] if s["seat_type"] == t)
                       for t in sorted({s["seat_type"] for s in plan["seats"]})},
        "per_division": dict(sorted(per_division.items())),
        "ok": plan["ok"],
    }


if __name__ == "__main__":  # pragma: no cover - convenience probe
    print(json.dumps(summary(), indent=2))
