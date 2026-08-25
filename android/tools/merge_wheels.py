#!/usr/bin/env python3
"""Merge the per-package wheels of one distribution into a single wheel.

``android_wheel.py`` builds one wheel per *importable package*, which is the
right shape when a distribution has one.  This project's does not: the
``halo-serdes`` distribution contains three packages --- ``halo_serdes``
(physics and engines), ``halo_serdes_app`` (presentation-independent
application layer) and ``halo_serdes_gui`` (the Dash desktop UI) --- and the
APK needs the first two, installed together.

Two things therefore have to happen here, and both are load-bearing:

**One wheel, not two.**  Two invocations produce two wheels with the *same*
filename and the same ``dist-info``, because the filename comes from the
distribution and not from the package.  Installing the second uninstalls the
first, and pip says nothing that would make you suspect it.

**``RECORD`` has to be recomputed.**  Each input wheel carried its own, the
later extraction overwrote the earlier one, and the survivor lists one
package's files only.  pip verifies installed files against ``RECORD``, so
shipping the truncated one is not a cosmetic problem.

Dropping ``Requires-Dist``
--------------------------
The merged wheel declares no dependencies, deliberately.  ``pyproject.toml``
asks for ``scipy>=1.11``; Chaquopy's package index carries **scipy 1.8.1** and
nothing newer for Python 3.10, so a pip resolve that honours the floor cannot
succeed --- the build would fail on a version constraint that has never
applied to this port.  (The divergence is not new; the ``wheel-versions`` CI
job exists because of it and runs the compute path against 1.8.1 on a host.
What is new is that it became *load-bearing*: the interpreted build installs
the Python through Chaquopy's ``srcDirs``, which performs no dependency
resolution at all, so nothing ever read these floors before.)

The Gradle ``pip`` block installs numpy, scipy, PyYAML and scikit-rf by name
regardless, so nothing goes missing.  This wheel is a build artifact consumed
by exactly one pip invocation, not a distribution anyone resolves against.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import shutil
import sys
import zipfile
from pathlib import Path


def _record_row(path: Path, tree: Path) -> tuple[str, str, str]:
    data = path.read_bytes()
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
    rel = path.relative_to(tree).as_posix()
    return rel, f"sha256={digest.decode()}", str(len(data))


def merge(out: Path, parts: list[Path], keep_deps: bool = False) -> None:
    tree = out.with_suffix(out.suffix + ".tree")
    shutil.rmtree(tree, ignore_errors=True)
    tree.mkdir(parents=True)
    for part in parts:
        with zipfile.ZipFile(part) as z:
            z.extractall(tree)

    dist_info = next(p for p in tree.iterdir() if p.name.endswith(".dist-info"))

    if not keep_deps:
        meta = dist_info / "METADATA"
        kept = [ln for ln in meta.read_text().splitlines()
                if not ln.startswith("Requires-Dist:")]
        meta.write_text("\n".join(kept) + "\n")

    record = dist_info / "RECORD"
    rows = [_record_row(p, tree)
            for p in sorted(tree.rglob("*")) if p.is_file() and p != record]
    rows.append((record.relative_to(tree).as_posix(), "", ""))
    with record.open("w", newline="") as fh:
        csv.writer(fh).writerows(sorted(rows))

    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(tree.rglob("*")):
            if path.is_file():
                z.write(path, path.relative_to(tree).as_posix())
    shutil.rmtree(tree, ignore_errors=True)

    with zipfile.ZipFile(out) as z:
        names = z.namelist()
    print(f"{out.name}  ({out.stat().st_size // 1024} KiB, "
          f"{sum(n.endswith('.so') for n in names)} .so, "
          f"{sum(n.endswith('.py') for n in names)} .py)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, type=Path,
                    help="merged wheel to write")
    ap.add_argument("parts", nargs="+", type=Path,
                    help="per-package wheels to merge, in install order")
    ap.add_argument("--keep-deps", action="store_true",
                    help="leave Requires-Dist alone (see the module docstring "
                         "for why the default strips it)")
    args = ap.parse_args()

    for part in args.parts:
        if not part.is_file():
            raise SystemExit(f"not a file: {part}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    merge(args.out, args.parts, keep_deps=args.keep_deps)
    return 0


if __name__ == "__main__":
    sys.exit(main())
