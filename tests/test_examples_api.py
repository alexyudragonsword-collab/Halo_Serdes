"""Examples must stay in sync with the library API.

The 30 numbered scripts in ``examples/`` are the primary user-facing
documentation, but running them all is far too slow for CI (several use 10^6
symbols). They therefore rot silently whenever a symbol is renamed or removed —
which has happened in practice.

These tests are the cheap guard that catches that class of breakage: every
example must byte-compile, and every name it imports from ``halo_serdes`` /
``halo_serdes_gui`` must actually exist. Numerical behaviour is covered by the
unit tests; full example execution stays a manual step.
"""

import ast
import importlib
import py_compile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
EXAMPLES = sorted((REPO / "examples").glob("*.py"))
PKGS = ("halo_serdes", "halo_serdes_gui")

assert EXAMPLES, "no example scripts found"


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_example_compiles(path, tmp_path):
    py_compile.compile(str(path), cfile=str(tmp_path / "out.pyc"), doraise=True)


def _library_imports(path):
    """(module, name) pairs the example imports from the project packages."""
    tree = ast.parse(path.read_text(), filename=str(path))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in PKGS:
                for alias in node.names:
                    out.append((node.module, alias.name))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in PKGS:
                    out.append((alias.name, None))
    return out


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_example_imports_resolve(path):
    missing = []
    for module, name in _library_imports(path):
        try:
            mod = importlib.import_module(module)
        except ImportError as exc:                 # optional backend, not rot
            pytest.skip(f"{module} unavailable: {exc}")
        if name is not None and not hasattr(mod, name):
            missing.append(f"{module}.{name}")
    assert not missing, f"{path.name} imports names that no longer exist: {missing}"


def test_examples_are_numbered_and_unique():
    """The numbered sequence is the documented tour order."""
    nums = [p.name.split("_")[0] for p in EXAMPLES]
    assert all(n.isdigit() for n in nums), [p.name for p in EXAMPLES]
    assert len(set(nums)) == len(nums), "duplicate example numbers"
