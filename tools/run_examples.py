"""Run the example scripts end to end, small enough for CI.

``tests/test_examples_api.py`` only checks that each example compiles and
that what it imports exists. That misses everything that happens at run
time: a renamed config field, a changed result key, a shape that no longer
lines up, a plot call on an array that became None. This runs them.

``--smoke`` (what CI runs) caps every ``SimConfig.n_symbols`` an example
asks for at ``--cap`` symbols, in the child process only: the library stays
as it is, the examples stay as the documentation shows them, and the numbers
they print are meaningless -- the run is checking that the code paths work,
not what they compute. Without ``--smoke`` the examples run at their own
sizes (slow; the numbers the docs quote).

Each example runs in its own interpreter with a non-interactive matplotlib
backend and writes its figures where it always does (``examples/output``,
ignored by git). An example that needs an optional backend that is not
installed (``pyibisami`` and the like) is reported as skipped, not failed.

    python tools/run_examples.py --smoke                  # all of them
    python tools/run_examples.py --smoke 05 22            # by number prefix
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EXAMPLES = REPO / "examples"

#: missing modules that make an example a skip rather than a failure: the
#: optional backends (not in any extra CI installs, or the [pll] / [fec] ones)
OPTIONAL = ("pyibisami", "pllsim", "galois")

# The child: cap n_symbols at every SimConfig construction (dataclasses.replace
# and load_config build it through __init__ as well), then run the example as
# __main__ from its own directory, as a user would.
_CHILD = r"""
import runpy, sys
cap = int(sys.argv[2])
if cap > 0:
    from halo_serdes.config import schema
    _init = schema.SimConfig.__init__
    def _capped(self, *args, **kw):
        if args:
            args = (min(int(args[0]), cap),) + args[1:]
        elif "n_symbols" in kw:
            kw["n_symbols"] = min(int(kw["n_symbols"]), cap)
        else:
            kw["n_symbols"] = min(schema.SimConfig.__dataclass_fields__["n_symbols"].default, cap)
        _init(self, *args, **kw)
    schema.SimConfig.__init__ = _capped
sys.argv = [sys.argv[1]]
runpy.run_path(sys.argv[0], run_name="__main__")
"""


def _missing_optional(stderr: str) -> str | None:
    for line in stderr.splitlines()[::-1]:
        if "ModuleNotFoundError" in line or "ImportError" in line:
            for mod in OPTIONAL:
                if f"'{mod}'" in line or f"{mod} " in line:
                    return mod
    return None


def run(paths: list[Path], cap: int, timeout: float) -> int:
    env = dict(os.environ, MPLBACKEND="Agg",
               PYTHONPATH=os.pathsep.join(filter(None, [str(REPO / "src"),
                                                        os.environ.get("PYTHONPATH")])))
    failed = []
    for p in paths:
        t0 = time.time()
        try:
            r = subprocess.run([sys.executable, "-c", _CHILD, str(p), str(cap)],
                               cwd=EXAMPLES, env=env, capture_output=True, text=True,
                               timeout=timeout)
            code, err = r.returncode, r.stderr
        except subprocess.TimeoutExpired as exc:
            code, err = -1, f"timed out after {timeout:.0f} s\n{exc.stderr or ''}"
        dt = time.time() - t0
        if code == 0:
            print(f"ok    {p.name:<32} {dt:6.1f} s", flush=True)
            continue
        opt = _missing_optional(err)
        if opt is not None:
            print(f"skip  {p.name:<32} {dt:6.1f} s  (optional backend '{opt}' not installed)",
                  flush=True)
            continue
        failed.append(p.name)
        print(f"FAIL  {p.name:<32} {dt:6.1f} s", flush=True)
        print("\n".join("      " + ln for ln in err.strip().splitlines()[-25:]), flush=True)
    print(f"\n{len(paths) - len(failed)} of {len(paths)} examples ran"
          + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("only", nargs="*", help="number prefixes to run (default: all)")
    ap.add_argument("--smoke", action="store_true", help="cap n_symbols (see --cap)")
    ap.add_argument("--cap", type=int, default=20_000, help="n_symbols cap with --smoke")
    ap.add_argument("--timeout", type=float, default=900.0, help="seconds per example")
    a = ap.parse_args(argv)
    paths = sorted(EXAMPLES.glob("[0-9][0-9]_*.py"))
    if a.only:
        paths = [p for p in paths if any(p.name.startswith(o) for o in a.only)]
    return run(paths, a.cap if a.smoke else 0, a.timeout)


if __name__ == "__main__":
    sys.exit(main())
