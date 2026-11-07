"""Partial and full clones updated N-1 -> HEAD -> next release through the real ``qubit update``.

Users' checkouts come in several shapes: HEAD's installer makes a ``--filter=tree:0`` clone, its
throttled fallback a ``--filter=blob:none`` clone, and plenty of users cloned the repository
themselves (a full clone). Each shape is seeded as that clone of N-1 over smart HTTP, adopted by
N-1's own ``scripts/install.sh``, then updated twice: to HEAD (N-1's updater hands off to HEAD's),
and to one more upstream release (HEAD's updater end to end). The three installs run concurrently.

Property: both updates land and the install runs, and the clone stays in its class: a partial clone
stays partial and a full clone stays full. Converting a full clone to ``tree:0`` re-arms the
``pack-objects ... should_include_obj`` fetch crash for every later update (#124272, #124323).
The one documented intra-partial conversion is ``tree:0`` -> ``blob:none``: a treeless checkout
cannot lazy-fetch blobs, so ``qubit_cli/gitlock.py::convert_treeless_checkout`` upgrades it to
``blob:none`` (still partial) on the first update.
"""

from __future__ import annotations

import pytest

from tests.e2e.core.upgrade import _install_helpers as I
from tests.e2e.core.upgrade.git import _git_world as G

pytestmark = G.PYTESTMARK

SHAPES = {
    "tree0": ["--filter=tree:0"],
    "blobless": ["--filter=blob:none"],
    "full": [],
}


def _scenario(root, shape: str) -> dict:
    # The preclone is the user's N-1 clone shape; the install runs HEAD's own scripts/install.sh
    # because Qubit's vendored runtime bootstrap in an older release predates the monorepo layout
    # (it looks for pm/ at the checkout root), so it cannot install this tree at all.
    cm, w = G.start_world(root / shape, base=G.n1_base(), preclone=SHAPES[shape])
    run = {"cm": cm, "w": w, "shape0": w.install_shape}
    w.set_main(I.head_sha())
    run["cp1"], run["head1"], run["shape1"] = w.update(), w.head(), w.shape()
    run["target2"] = w.publish(f"release: e2e partial {shape}", {f"e2e-partial-{shape}.txt": "release\n"})
    run["cp2"], run["head2"], run["shape2"] = w.update(), w.head(), w.shape()
    run["version"] = w.version()
    return run


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    root = tmp_path_factory.mktemp("git-partial")
    results = G.in_parallel({shape: (lambda s=shape: _scenario(root, s)) for shape in SHAPES})
    yield results
    for res in results.values():
        if isinstance(res, dict):
            res["cm"].__exit__(None, None, None)


def _same_class(shape: str, before: dict, after: dict) -> bool:
    """A clone may stay as it was, or a treeless clone may become blobless (documented).

    Full <-> partial is always a failure: it is the shape change that re-arms the fetch crash.
    """
    if before == after:
        return True
    return (shape == "tree0" and before["filter"] == "tree:0" and after["filter"] == "blob:none"
            and before["shallow"] == after["shallow"] == "false"
            and before["promisor"] == after["promisor"] == "true")


@pytest.mark.parametrize("shape", list(SHAPES))
def test_updates_land_and_the_clone_keeps_its_shape(runs, shape):
    run = runs[shape]
    if isinstance(run, BaseException):
        raise run
    w: G.World = run["w"]
    cp1, cp2 = run["cp1"], run["cp2"]
    assert run["shape0"]["shallow"] == "false" and (shape == "full") == (run["shape0"]["filter"] == "-"), \
        f"seeded the wrong clone shape for {shape}: {run['shape0']}"
    assert cp1.returncode == 0 and run["head1"] == I.head_sha(), f"N-1 -> HEAD update failed:\n{w.diag(cp1)}"
    assert G.TRACEBACK not in G.output(cp1), f"N-1 -> HEAD update printed a traceback:\n{w.diag(cp1)}"
    assert _same_class(shape, run["shape0"], run["shape1"]), (
        f"{'the full clone was converted to a partial clone' if shape == 'full' else 'the clone changed shape'} "
        f"by the N-1 -> HEAD update: {run['shape0']} -> {run['shape1']}\n{w.diag(cp1)}")
    assert cp2.returncode == 0 and run["head2"] == run["target2"], f"HEAD -> next update failed:\n{w.diag(cp2)}"
    assert _same_class(shape, run["shape0"], run["shape2"]), (
        f"the clone changed shape: {run['shape0']} -> {run['shape2']}\n{w.diag(cp2)}")
    version = run["version"]
    assert version.returncode == 0 and G.TRACEBACK not in G.output(version), w.diag(version)
