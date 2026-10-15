"""C7/L3 (relationship form) — the animation map and the emitted kinds agree.

**Why this file exists separately from ``test_office_event_animation_linkage.py``.**

That file is correct but it encodes the CURRENT membership: it hard-codes
``WIRED = {...}`` and ``already_emitted = {...}`` and asserts
``mapped <= WIRED | already_emitted | DELIBERATELY_UNWIRED``. Adding one honest
event kind therefore turns it red, and the red gets blamed on the new kind. The
skill's rule is explicit: *do not pin a defect in a test — pin the RELATIONSHIP,
so adding a kind does not break it.*

This file pins the relationship instead. Neither side is a literal list of the
kinds that exist today:

* the **mapped** side is read out of ``tower._ANIMATION`` at runtime (the map the
  renderer actually folds over);
* the **emitted** side is derived from the office SOURCE — every production
  ``office_event`` write site, with the kinds it can reach resolved (string
  literals at the call site, module constants, and the variable-kind classifier).

The two rules, both directions of the diff:

1. **Every mapped kind is emittable** — a pose the renderer can reach must have
   something in the office that can write its event. A new ``_ANIMATION`` entry
   with no emitter fails here, which is the whole point: it is the check that
   catches "no event, no pose" the moment the pose is added.
2. **Every emitted kind is either mapped or a declared non-animator** — a kind
   written to ``office_event`` that ``_ANIMATION`` does not carry is work nobody
   can see (the reverse direction). An unknown kind silently folds to ``working``
   via ``_animation_for``'s default, so this must be DECLARED rather than
   discovered.

The one escape hatch on each side is a NAMED, reasoned declaration
(``DECLARED_UNREACHABLE`` / ``DECLARED_NON_ANIMATING``). A kind may be listed
only with a reason, and the test asserts the declaration is *really* true (the
declared-unreachable kind really has no emitter), so the list can never be used
to hide a dead pose or an invisible event.

Hermetic: reads source + the pure ``_ANIMATION`` map only. No store, no clock,
no network, no provider call.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
OFFICE = REPO / "voicebridge" / "office"

#: The one write seam every ``office_event`` row goes through.
#: ``runner._event`` is the office's single event writer; ``agent_memory.emit_event``
#: reuses it (and is the seam the capability call sites use). Both are the SAME
#: column shape, so a kind reaching either has a renderable row behind it.
EMITTER_NAMES = ("_event", "emit_event", "_emit")

#: Kinds that are mapped in ``_ANIMATION`` and DELIBERATELY have no emitter.
#: Each entry needs a reason. Asserted to be genuinely unemitted below, so the
#: list cannot be used to smuggle a dead pose past the forward rule.
DECLARED_UNREACHABLE = {
    # A meeting is a real boardroom event and the boardroom is not wired yet
    # (`57-BOARDROOM.md`); emitting `meeting` now would draw motion for a room
    # that does not exist. It stays mapped AND unemitted on purpose.
    "meeting": "no boardroom yet (57-BOARDROOM.md); emitting would be a pose for a room that does not exist",
}

#: Kinds written to ``office_event`` that are DELIBERATELY not mapped to a pose.
#: Empty today: every emitted kind has a pose. This exists so a future
#: deliberately-invisible kind is DECLARED here rather than discovered by the
#: reverse rule going red. Each entry also needs a reason.
DECLARED_NON_ANIMATING: dict = {}


def _mapped_kinds() -> set:
    """The event kinds ``tower._ANIMATION`` maps — read from the live object."""
    from voicebridge.office import tower as T

    return set(T._ANIMATION)


# ------------------------------------------------------------- the emitted side
def _string_literals(node: ast.AST) -> set:
    """Every string this expression can evaluate to, statically.

    Handles the three shapes the office actually uses to name a kind:

    * a bare literal — ``_event(cfg, "run_started", ...)``
    * a conditional — ``_event(cfg, "task_done" if done else "task_unverified")``
    * a module-level name — ``_emit(cfg, event, ...)`` where
      ``event = NOTE_KIND_EVENT if ... else MEMORY_KIND_EVENT``; the NAME is
      resolved through the module's own constant table (see
      :func:`_module_constants`), because a kind spelled as a constant is still
      a kind that is emitted.
    """
    out: set = set()
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        out.add(node.value)
    elif isinstance(node, ast.IfExp):
        out |= _string_literals(node.body)
        out |= _string_literals(node.orelse)
    elif isinstance(node, ast.BoolOp):
        for v in node.values:
            out |= _string_literals(v)
    elif isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        for e in node.elts:
            out |= _string_literals(e)
    return out


def _module_constants(tree: ast.Module) -> dict:
    """``{CONST_NAME: {string values}}`` for the module's own assignments.

    Only string-valued constants and names that alias another constant are kept
    (``NOTE_KIND_EVENT = "note_written"``; ``_emit = emit_event`` would be
    dropped because its value is not a string). A name is never followed into
    another module — an office kind constant is local to the module that writes
    it, and following imports would make this an inter-module resolver for no
    gain.
    """
    consts: dict = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for tgt in node.targets:
            if not isinstance(tgt, ast.Name):
                continue
            vals = _string_literals(node.value)
            if not vals:
                # a name that aliases another constant, e.g. `X = Y`
                if isinstance(node.value, ast.Name) and node.value.id in consts:
                    vals = set(consts[node.value.id])
            if vals:
                consts[tgt.id] = vals
    return consts


def _resolved_kinds(node: ast.AST, consts: dict, locals_: dict | None = None) -> set:
    """String values of an expression, resolving names through a scope.

    ``consts`` is the module constant table; ``locals_`` (optional) is the
    enclosing function's local-name table, which is what lets a variable-kind
    emit resolve: ``event = MEMORY_KIND_EVENT if ... else NOTE_KIND_EVENT`` then
    ``_emit(cfg, event)``.
    """
    locals_ = locals_ or {}
    out = _string_literals(node)
    if isinstance(node, ast.Name):
        if node.id in locals_:
            out |= set(locals_[node.id])
        elif node.id in consts:
            out |= set(consts[node.id])
    elif isinstance(node, ast.IfExp):
        out |= _resolved_kinds(node.body, consts, locals_)
        out |= _resolved_kinds(node.orelse, consts, locals_)
    return out


def _local_kinds(fn: ast.FunctionDef, consts: dict, classifiers: dict) -> dict:
    """``{local_name: {kinds}}`` for one function body — a one-pass dataflow.

    Resolves the two shapes the office uses to name a kind in a variable:

    * a constant / conditional assignment — ``event = NOTE_KIND_EVENT if ... else
      MEMORY_KIND_EVENT``;
    * a classifier call — ``kind = classify_tool(name)``, where ``classify_tool``
      is a function in the same module whose returns are string literals.

    A name is only ever bound to the kinds the source can actually produce, so
    nothing is invented. Repeats until stable so ``a = b; b = "x"`` resolves.
    """
    local: dict = {}
    # classifier calls assigned to a name
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        callee = node.value.func
        cname = callee.attr if isinstance(callee, ast.Attribute) else (
            callee.id if isinstance(callee, ast.Name) else None)
        if cname not in classifiers:
            continue
        for tgt in node.targets:
            if isinstance(tgt, ast.Name):
                local.setdefault(tgt.id, set()).update(classifiers[cname])

    for _ in range(4):  # small fixpoint; dependency chains here are length 1-2
        changed = False
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign):
                vals = _resolved_kinds(node.value, consts, local)
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name) and vals:
                        if not vals <= local.get(tgt.id, set()):
                            local.setdefault(tgt.id, set()).update(vals)
                            changed = True
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                vals = _resolved_kinds(node.value, consts, local)
                if isinstance(node.target, ast.Name) and vals:
                    if not vals <= local.get(node.target.id, set()):
                        local.setdefault(node.target.id, set()).update(vals)
                        changed = True
        if not changed:
            break
    return local


def _enclosing_function(tree: ast.Module) -> dict:
    """``{id(node): enclosing FunctionDef}`` for every node (one walk)."""
    out: dict = {}
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        for sub in ast.walk(fn):
            out.setdefault(id(sub), fn)
    return out


def _emitted_kinds() -> dict:
    """``{kind: [evidence, ...]}`` for every kind the office source can write.

    Scans each office module's AST for calls to the emitter seam and resolves the
    kind argument through (a) string literals / conditionals at the call site,
    (b) module constants, (c) the enclosing function's local-name table, and (d)
    the return literals of a classifier function the kind came from
    (``agent_memory.classify_tool`` -> ``skill_used`` / ``mcp_call``). Nothing is
    promoted into the set that the source cannot actually produce.
    """
    evidence: dict = {}

    def add(kind: str, where: str) -> None:
        # an empty/whitespace kind is not an emitted kind: `classify_tool`
        # returns "" for a non-skill/non-MCP tool, which means "no event", not
        # "an event named empty". Recording it would be a phantom in the diff.
        if kind and kind.strip():
            evidence.setdefault(kind, []).append(where)

    for py in sorted(OFFICE.glob("*.py")):
        src = py.read_text()
        tree = ast.parse(src)
        consts = _module_constants(tree)
        # a function that RETURNS string literals is a kind-classifier:
        # its returns are kinds a caller can emit.
        classifiers: dict = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                rets: set = set()
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Return) and sub.value is not None:
                        rets |= _resolved_kinds(sub.value, consts)
                if rets:
                    classifiers[node.name] = rets
        enclosing = _enclosing_function(tree)
        local_cache: dict = {}

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else (
                fn.id if isinstance(fn, ast.Name) else None)
            if name not in EMITTER_NAMES or not node.args:
                continue
            scope = enclosing.get(id(node))
            if scope is None:
                continue
            locals_ = local_cache.setdefault(
                id(scope), _local_kinds(scope, consts, classifiers))
            for i, arg in enumerate(node.args):
                if i == 0:
                    continue  # arg0 is `cfg`
                for k in _resolved_kinds(arg, consts, locals_):
                    add(k, f"{py.name}:{node.lineno}")
    return evidence


# ====================================================== 1. the forward rule
def test_every_mapped_pose_has_an_emitter_or_is_a_named_exception():
    """No ``_ANIMATION`` entry is a pose nothing in the office can reach.

    This is the check the skill's rule asks for: after adding a kind to
    ``_ANIMATION``, if no production call site can write it, the office reads as
    dead and this test says which pose is unreachable. A kind with no emitter
    must be moved into ``DECLARED_UNREACHABLE`` WITH a reason.
    """
    mapped = _mapped_kinds()
    emitted = set(_emitted_kinds())
    unreachable = mapped - emitted - set(DECLARED_UNREACHABLE)
    assert not unreachable, (
        "these _ANIMATION poses are unreachable — nothing in voicebridge/office "
        f"can emit them: {sorted(unreachable)}\n"
        "Fix by EMITTING the event at its honest call site, or (if it is "
        "deliberate) add it to DECLARED_UNREACHABLE with a reason."
    )


# ====================================================== 2. the reverse rule
def test_every_emitted_kind_is_mapped_or_a_declared_non_animator():
    """No kind reaches ``office_event`` without a pose or a declaration.

    An emitted-but-unmapped kind is work that happened and cannot be seen. It is
    not a crash (``_animation_for`` defaults an unknown kind to ``working``), so
    nothing goes red on its own — which is exactly why it needs a test. A
    deliberately-invisible kind must be declared.
    """
    mapped = _mapped_kinds()
    emitted = _emitted_kinds()
    invisible = set(emitted) - mapped - set(DECLARED_NON_ANIMATING)
    assert not invisible, (
        "these kinds are written to office_event but have no _ANIMATION pose — "
        f"the work is invisible in the tower: {sorted(invisible)}\n"
        "Evidence: "
        + "; ".join(f"{k} <- {emitted[k]}" for k in sorted(invisible))
        + "\nMap it, or add it to DECLARED_NON_ANIMATING with a reason."
    )


# ====================================================== 3. the escape hatches
def test_the_declared_unreachable_pose_is_really_unemitted():
    """A claim of "deliberately unreachable" is verified, not trusted.

    If someone wires an emitter for a declared-unreachable kind, the declaration
    is now a lie and silently exempts a reachable pose from the forward rule.
    This asserts the declaration is still true, and that each carries a reason.
    """
    emitted = set(_emitted_kinds())
    for kind, reason in DECLARED_UNREACHABLE.items():
        assert reason and reason.strip(), f"{kind!r} declared without a reason"
        assert kind not in emitted, (
            f"{kind!r} is declared unreachable but IS now emitted "
            f"({emitted and _emitted_kinds().get(kind)}) — remove the declaration "
            "so the forward rule covers it again."
        )
        assert kind in _mapped_kinds(), (
            f"{kind!r} is declared unreachable but is not even mapped — the "
            "declaration is stale"
        )


def test_the_declared_non_animating_kind_is_really_emitted():
    """The mirror of the above: a declared-invisible kind must be emitted.

    A non-animating declaration for a kind nobody emits would silently widen
    nothing (harmless) but is a stale note; assert it is still true so the list
    cannot rot into a place kinds are parked.
    """
    emitted = set(_emitted_kinds())
    for kind, reason in DECLARED_NON_ANIMATING.items():
        assert reason and reason.strip(), f"{kind!r} declared without a reason"
        assert kind in emitted, (
            f"{kind!r} is declared non-animating but nothing emits it")


# ====================================================== 4. the map is real
def test_the_map_is_the_one_the_renderer_folds_over():
    """``_animation_for`` reads ``_ANIMATION``; an unknown kind must not crash.

    The forward/reverse rules above are only meaningful if the renderer
    actually consults this map. Pin the seam rather than the contents: an
    unknown kind folds to ``working`` (an event that happened stays visible),
    and a mapped kind folds to its own pose.
    """
    from voicebridge.office import tower as T

    assert T._animation_for("run_started") == T._ANIMATION["run_started"]
    # unknown kind: visible, never a crash, never "idle"
    assert T._animation_for("some_future_kind") != "idle"
    assert T._animation_for("") != "idle"


def test_the_relationship_can_fail(tmp_path):
    """Falsifiability: the forward rule is red on a mapped kind with no emitter.

    Copies ``tower.py`` to a scratch dir, adds a pose to ``_ANIMATION`` that no
    call site emits, and asserts the SAME predicate the forward test uses
    reports it unreachable. The mutated map is read by PARSING the mutated
    source (the same AST walk the collector uses) rather than importing it, so
    the probe does not depend on ``tower.py``'s relative imports resolving
    standalone.
    """
    import shutil

    scratch = tmp_path / "tower_mutated.py"
    shutil.copyfile(OFFICE / "tower.py", scratch)
    txt = scratch.read_text()
    marker = "_ANIMATION = {\n"
    assert marker in txt, "tower.py no longer opens _ANIMATION the expected way"
    scratch.write_text(txt.replace(
        marker, marker + '    "phantom_pose_no_emitter": "working",\n', 1))

    # the ORIGINAL map, parsed from the unmutated source — the reference the
    # mutation is measured against (the live object may itself be mutated by a
    # concurrent probe, so read the pristine file, not the imported module).
    def _map_from(source: str) -> set:
        for node in ast.walk(ast.parse(source)):
            if (isinstance(node, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "_ANIMATION"
                            for t in node.targets)
                    and isinstance(node.value, ast.Dict)):
                return {k.value for k in node.value.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)}
        return set()

    original_mapped = _map_from(txt)
    mutated_mapped = _map_from(scratch.read_text())
    assert "phantom_pose_no_emitter" in mutated_mapped, (
        "the mutation did not take — falsifiability check is vacuous")
    assert mutated_mapped - {"phantom_pose_no_emitter"} == original_mapped, (
        "the AST reader disagrees between the two reads of the same map")
    assert _mapped_kinds() == original_mapped, (
        "the live `_ANIMATION` differs from its own source — the probe is not "
        "measuring the map the renderer folds over")

    emitted = set(_emitted_kinds())
    phantom = "phantom_pose_no_emitter"
    assert phantom not in emitted, "the phantom kind is somehow emitted"
    # the forward rule's exact predicate, applied to the mutated map
    unreachable = mutated_mapped - emitted - set(DECLARED_UNREACHABLE)
    assert unreachable == {phantom}, (
        "the forward rule would NOT have caught an unreachable pose — it "
        f"reported {unreachable} instead of {{{phantom!r}}}")
