"""Examples must stay in sync with the library API.

The numbered scripts in ``examples/`` are the primary user-facing
documentation, but running them all is far too slow for CI (several use 10^6
symbols). They therefore rot silently whenever a symbol is renamed or removed —
which has happened in practice.

These tests are the cheap guard that catches that class of breakage: every
example must byte-compile, and every name it imports from ``halo_serdes`` /
``halo_serdes_gui`` must actually exist. Numerical behaviour is covered by the
unit tests. Running them is the CI ``examples`` job (``tools/run_examples.py
--smoke``, every example with ``n_symbols`` capped); its runner is tested at
the end of this file.
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


# ------------------------------------------------- tools/run_examples.py
# The runner CI uses to execute the examples (the `examples` job). What it
# has to get right: the cap reaches every SimConfig, a broken example fails
# the run, and a missing optional backend is a skip, not a failure.

def _runner():
    import importlib.util

    spec = importlib.util.spec_from_file_location("run_examples", REPO / "tools" / "run_examples.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_runner_caps_every_simconfig_and_reports_failures(tmp_path, capsys):
    ex = _runner()
    capped = tmp_path / "90_capped.py"
    capped.write_text(
        "import dataclasses\n"
        "from halo_serdes.config.schema import SimConfig\n"
        "assert SimConfig(n_symbols=10**6).n_symbols == 5000\n"
        "assert SimConfig().n_symbols == 5000\n"
        "assert SimConfig(300).n_symbols == 300\n"
        "s = dataclasses.replace(SimConfig(), n_symbols=10**7)\n"
        "assert s.n_symbols == 5000\n"
        "assert __name__ == '__main__'\n")
    broken = tmp_path / "91_broken.py"
    broken.write_text("raise KeyError('a result key that is gone')\n")
    optional = tmp_path / "92_optional.py"
    optional.write_text("raise ImportError(\"needs the optional 'pyibisami' backend\")\n")
    assert ex.run([capped, optional], 5000, 120) == 0
    assert ex.run([capped, broken], 5000, 120) == 1
    out = capsys.readouterr().out
    assert "skip  92_optional.py" in out and "FAIL  91_broken.py" in out
    assert "KeyError" in out
    # without the cap the example's own size stands
    assert ex.run([capped], 0, 120) == 1


def test_runner_saves_each_examples_output_in_order_with_jobs(tmp_path, capsys):
    """--save keeps what each example printed (what the docs quote and the
    next full-size run is diffed against); --jobs runs them at once but
    reports them in the examples' order."""
    ex = _runner()
    slow = tmp_path / "93_slow.py"
    slow.write_text("import time\ntime.sleep(1.0)\nprint('slow: 1.25 dB')\n")
    fast = tmp_path / "94_fast.py"
    fast.write_text("print('fast: 3.5e-04')\n")
    out_dir = tmp_path / "out"
    assert ex.run([slow, fast], 0, 120, jobs=2, save=out_dir) == 0
    assert (out_dir / "93_slow.txt").read_text() == "slow: 1.25 dB\n"
    assert (out_dir / "94_fast.txt").read_text() == "fast: 3.5e-04\n"
    out = capsys.readouterr().out
    assert out.index("93_slow.py") < out.index("94_fast.py")


def test_example_paths_in_the_docs_exist():
    """The README's quick start once named two scripts that had been renamed."""
    import re

    docs = [REPO / "README.md", REPO / "ROADMAP.md", REPO / "CHANGELOG.md",
            *(REPO / "docs").glob("*.md"), *(REPO / "cairn").glob("*.md")]
    missing = sorted({(d.name, m) for d in docs if d.exists()
                      for m in re.findall(r"examples/\d\d_[a-z0-9_]+\.py", d.read_text())
                      if not (REPO / m).exists()})
    assert not missing, missing
