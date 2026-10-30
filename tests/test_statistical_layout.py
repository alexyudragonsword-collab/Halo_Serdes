"""``engine.statistical`` after the 2026-10-30 split into parts.

The engine's helpers moved to ``stat_pdf`` / ``stat_noise`` / ``stat_pr`` /
``stat_loop`` / ``stat_pattern_phase``; callers and tests import them from
``engine.statistical`` as before. A re-export is the same object, so a
module-level mutable (the pattern-phase cache) is shared -- but rebinding a
name on ``statistical`` does not reach the part that reads it, which is why
the probe hook is set on its own module.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

from halo_serdes.engine import statistical

PARTS = ("stat_pdf", "stat_noise", "stat_pr", "stat_loop", "stat_pattern_phase")


def _top_level(part):
    """(module, the names its own source defines at top level)."""
    mod = importlib.import_module(f"halo_serdes.engine.{part}")
    src = Path(mod.__file__).parent / f"{part}.py"
    if not src.exists():
        pytest.skip("compiled build: no source to read the names from")
    tree = ast.parse(src.read_text(encoding="utf-8"))
    names = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            names += [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign):
            names.append(node.target.id)
    return mod, tree, names


@pytest.mark.parametrize("part", PARTS)
def test_every_name_of_a_part_is_the_same_object_from_statistical(part):
    mod, _, names = _top_level(part)
    assert names, part
    for name in names:
        assert getattr(statistical, name) is getattr(mod, name), (part, name)


def test_the_parts_do_not_import_the_engine_module():
    """The parts sit below ``statistical``: none imports it (a cycle would
    make the import order matter)."""
    for part in PARTS:
        _, tree, _ = _top_level(part)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                assert node.module != "statistical", part


def test_the_pattern_phase_cache_is_shared():
    from halo_serdes.engine import stat_pattern_phase

    assert statistical._PATTERN_PHASE_CACHE is stat_pattern_phase._PATTERN_PHASE_CACHE
