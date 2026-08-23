"""M0 feasibility probe — the one thing the Android spike exists to answer.

Runs entirely inside the app's embedded CPython and reports, as JSON, whether
the pieces the real app depends on actually work on this device:

1. Are numpy and scipy importable at all? Chaquopy ships prebuilt wheels only
   for some Python versions (scipy has historically lagged), and a missing
   wheel is a *build*-time failure — but a wheel that loads on x86_64 and not
   on a 16 KB-page arm64 device fails only here, at runtime.
2. Do the three scipy entry points the framework actually uses work?
   ``special.erfc`` / ``special.erfcinv`` / ``stats.binom`` / ``optimize`` —
   nothing else is imported anywhere in ``src/halo_serdes``.
3. Does the real compute core produce the *same numbers* as the desktop?
   ``EXPECTED`` below is generated on the host by ``tools/gen_probe_golden.py``
   in the same CI run, so a mismatch means a genuine platform divergence
   (libm, FMA contraction, OpenBLAS) rather than a stale constant.

Deliberately importable and runnable on a desktop too, so the assertions can be
developed and tested without an emulator: ``python android/app/src/main/python/
halo_probe.py``.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import time
import traceback

# numba has no Android wheels; architecture invariant #4 guarantees the
# pure-Python kernels give identical results, so make that explicit and
# fail loudly if numba ever sneaks in.
os.environ.setdefault("HALO_NO_JIT", "1")

#: Config used for the end-to-end check. Analytic channel on purpose: it needs
#: no bundled file, so this probe works before asset extraction is wired up.
PROBE_PRESET = "NRZ 28G analytic (COM/xtalk)"

#: The preset's own noise level yields BER == 0, which would compare equal on
#: any platform and prove nothing. This raises it until the BER lands around
#: 1e-6 — a value produced by the full PDF-convolution path and spanning
#: several decades, so a real numerical divergence cannot hide in it.
PROBE_OVERRIDES = {"rx.noise_rms": "0.020"}

#: Relative tolerance for cross-platform agreement. Tight enough that a real
#: divergence (different libm, FMA contraction) shows up; loose enough not to
#: trip on last-bit noise.
RTOL = 1e-9


def _versions() -> dict:
    out = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "HALO_NO_JIT": os.environ.get("HALO_NO_JIT"),
    }
    for mod in ("numpy", "scipy", "skrf", "yaml"):
        try:
            m = __import__(mod)
            out[mod] = getattr(m, "__version__", "?")
        except Exception as exc:
            out[mod] = f"MISSING ({type(exc).__name__})"
    out["numba_absent"] = "numba" not in sys.modules
    return out


def _scipy_entry_points() -> dict:
    """Exercise exactly the scipy surface src/halo_serdes uses — nothing more."""
    from scipy.optimize import curve_fit
    from scipy.special import erfc, erfcinv
    from scipy.stats import binom
    import numpy as np

    x = np.linspace(0.0, 2.0, 5)
    fit, _ = curve_fit(lambda t, a, b: a * t + b, x, 2.0 * x + 1.0)
    return {
        "erfc(1.0)": float(erfc(1.0)),
        "erfcinv(0.5)": float(erfcinv(0.5)),
        "binom.sf(15,544,2.4e-4)": float(binom.sf(15, 544, 2.4e-4)),
        "curve_fit_slope": float(fit[0]),
    }


def _compute_core() -> dict:
    """Run the real thing: statistical engine + 802.3 COM + FEC projection."""
    from halo_serdes_app import api

    t0 = time.perf_counter()
    preset = json.loads(api.call("preset", json.dumps({"name": PROBE_PRESET})))
    if not preset["ok"]:
        raise RuntimeError(f"preset failed: {preset['error']}")
    values = {**preset["data"]["values"], **PROBE_OVERRIDES}

    stat = json.loads(api.call("run_stat", json.dumps({"values": values})))
    if not stat["ok"]:
        raise RuntimeError(f"run_stat failed: {stat['error']}")
    com = json.loads(api.call("run_com", json.dumps({"values": values})))
    if not com["ok"]:
        raise RuntimeError(f"run_com failed: {com['error']}")

    from halo_serdes.fec import pre_to_post_fec_ber

    return {
        "ber": stat["data"]["ber"],
        "ser": stat["data"]["ser"],
        "best_phi": stat["data"]["best_phi"],
        "bathtub_points": len(stat["data"]["bathtub"]["x"]),
        "com_db": com["data"]["com_db"],
        "post_fec_kp4": pre_to_post_fec_ber(2.4e-4, "kp4"),
        "elapsed_ms": round((time.perf_counter() - t0) * 1e3, 1),
    }


def _compare(actual: dict, expected: dict | None) -> dict:
    """Diff against host-computed golden values, if any were supplied."""
    if not expected:
        return {"checked": False, "reason": "no golden values bundled"}
    import numpy as np

    bad = {}
    for key, want in expected.items():
        got = actual.get(key)
        if got is None:
            bad[key] = "missing"
        elif isinstance(want, (int, float)) and isinstance(got, (int, float)):
            if not np.isclose(got, want, rtol=RTOL, atol=1e-300):
                bad[key] = {"want": want, "got": got}
        elif got != want:
            bad[key] = {"want": want, "got": got}
    return {"checked": True, "rtol": RTOL, "mismatches": bad, "match": not bad}


def _load_golden() -> dict | None:
    """Golden values written next to this file by the host at build time."""
    import pathlib

    p = pathlib.Path(__file__).with_name("probe_golden.json")
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text())["compute"]
    except Exception:
        return None


def run() -> str:
    """Entry point called from Kotlin. Never raises — returns JSON either way."""
    report: dict = {"ok": False}
    try:
        report["versions"] = _versions()
        report["scipy"] = _scipy_entry_points()
        compute = _compute_core()
        report["compute"] = compute
        report["golden"] = _compare(compute, _load_golden())
        report["ok"] = report["golden"].get("match", True)
    except Exception as exc:
        report["error"] = {"type": type(exc).__name__, "message": str(exc),
                           "traceback": traceback.format_exc()}
    return json.dumps(report, indent=2, allow_nan=False)


def summary() -> str:
    """A few lines fit for a phone screen."""
    r = json.loads(run())
    if "error" in r:
        return f"FAILED\n{r['error']['type']}: {r['error']['message']}"
    v, c = r["versions"], r["compute"]
    g = r.get("golden", {})
    verdict = ("golden MATCH" if g.get("match")
               else "golden MISMATCH" if g.get("checked")
               else "no golden bundled")
    return (f"python {v['python']} on {v['machine']}\n"
            f"numpy {v['numpy']}  scipy {v['scipy']}\n"
            f"skrf {v['skrf']}  numba absent: {v['numba_absent']}\n"
            f"erfc(1.0) = {r['scipy']['erfc(1.0)']:.6f}\n"
            f"BER = {c['ber']:.4e}   COM = {c['com_db']:.2f} dB\n"
            f"took {c['elapsed_ms']:.0f} ms   [{verdict}]")


if __name__ == "__main__":
    print(run())
