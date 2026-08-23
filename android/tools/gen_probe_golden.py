"""Generate the golden values the on-device probe compares itself against.

Run on the CI *host* (the same commit, the same pure-Python kernels) and write
``probe_golden.json`` next to ``halo_probe.py`` so it ships inside the APK. The
device then recomputes the same quantities and diffs.

A committed constant would rot the moment the engine legitimately changed; a
value produced from the same commit in the same run cannot. The comparison is
therefore about *platform* divergence — libm, FMA contraction, OpenBLAS — which
is exactly risk R8 in the plan, and this makes it fail at the build that
introduces it rather than months later.

Usage (from the repo root)::

    python android/tools/gen_probe_golden.py
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

os.environ.setdefault("HALO_NO_JIT", "1")

_HERE = pathlib.Path(__file__).resolve().parent
_PROBE_DIR = _HERE.parent / "app" / "src" / "main" / "python"
sys.path.insert(0, str(_PROBE_DIR))
sys.path.insert(0, str(_HERE.parents[2] / "src"))

import halo_probe  # noqa: E402  (needs the sys.path above)


def main() -> int:
    compute = halo_probe._compute_core()
    # elapsed_ms is wall-clock — it must never be part of a golden comparison
    compute.pop("elapsed_ms", None)
    out = {
        "generated_on": {
            "python": sys.version.split()[0],
            "platform": halo_probe.platform.platform(),
            "machine": halo_probe.platform.machine(),
        },
        "preset": halo_probe.PROBE_PRESET,
        "overrides": halo_probe.PROBE_OVERRIDES,
        "rtol": halo_probe.RTOL,
        "compute": compute,
    }
    dest = _PROBE_DIR / "probe_golden.json"
    dest.write_text(json.dumps(out, indent=2) + "\n")
    print(f"wrote {dest}")
    print(json.dumps(compute, indent=2))
    if compute.get("ber", 0.0) == 0.0:
        print("WARNING: golden BER is exactly 0 — a platform comparison against "
              "zero proves nothing; adjust PROBE_OVERRIDES.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
