"""Numerical fingerprint of the engines: prove a change did not move a number.

Tests assert thresholds and relations (``snr_db > 12``, ``a > b``); a result
that moves a little and still satisfies them passes. So a change meant to
leave behaviour alone has to be checked bit for bit
(cairn/engineering-pitfalls.md, "重构不改变行为要用数值指纹证明"). This
records every bundled preset through the static, time-domain and statistical
engines and COM. It adds a set of synthetic links for the paths no preset
reaches:
- both clock kinds with a Tx FIR and Tx bandwidth;
- the reconstructed front end;
- the Tx edge offsets;
- an optical link (time and statistical engines, Tx FFE on) with its E/O
  power curve;
- a retimed cascade.

Scalars are stored by ``repr`` and arrays by SHA-256, so the comparison is
exact. Bit-exact means the same machine and library versions: record both
sides in one environment.

    python tools/fingerprint.py record before.json     # on the base commit
    python tools/fingerprint.py record after.json      # on the change
    python tools/fingerprint.py compare before.json after.json

``compare`` prints every differing value and exits 1 if there is one. A change
that is meant to move numbers (a fix) should move only the ones it explains;
list them in the PR. Both commands also list the entries that raised instead
of recording values: two sides failing the same way compare equal, which is
how COM went unchecked while this lived outside the repo (its call had the
wrong signature on both sides). About a minute.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import sys
import time
import warnings
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

CLOCK_PROFILE = REPO / "data" / "clock_profiles" / "bench_markulic16_sspll_40m_10p24g.yaml"


def digest(a) -> str:
    return hashlib.sha256(np.ascontiguousarray(np.asarray(a, dtype=np.float64))).hexdigest()[:16]


def result_values(r) -> dict:
    """The scalars and array digests of one engine result (any engine)."""
    out = {}
    for k in ("ser", "slicer_snr_db", "n_symbols", "sample_phase"):
        v = getattr(r, k, None)
        if v is not None:
            out[k] = repr(v)
    b = getattr(r, "ber", None)
    if b is not None:
        if isinstance(b, float):
            out["ber"] = repr(b)
        else:
            for k in ("ber", "n_errors", "n_checked"):
                out["ber." + k] = repr(getattr(b, k, None))
    for k in ("ffe_taps", "dfe_taps", "y_slicer"):
        v = getattr(r, k, None)
        if v is not None and np.asarray(v).size:
            out[k] = digest(v)
            out[k + ".n"] = int(np.asarray(v).size)
    extras = getattr(r, "extras", None) or {}
    for k in sorted(extras):
        v = extras[k]
        if isinstance(v, (bool, int, float, str)):
            out["x." + k] = repr(v)
        elif isinstance(v, np.ndarray) and v.size:
            out["x." + k] = digest(v)
    return out


def _guarded(fn):
    # an engine that raises is part of the fingerprint: a change that makes a
    # preset start (or stop) failing is a behaviour change too
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def _com_values(c) -> dict:
    return {k: repr(getattr(c, k)) for k in ("com_db", "a_signal", "a_noise", "fom_db",
                                             "fom_isi", "fom_xtalk", "fom_noise", "fom_jitter")}


def _recon_values(wave_and_phase) -> list:
    w, phase = wave_and_phase
    return [digest(w.y), repr(float(phase))]


def _cascade_values(r) -> list:
    return [repr(r.ber.ber), [repr(b) for b in r.segment_bers]]


def record_presets(names=None) -> dict:
    from halo_serdes.analysis.com import compute_com
    from halo_serdes.channel import ChannelModel
    from halo_serdes.engine.static_link import run_static_link
    from halo_serdes.engine.statistical import run_statistical
    from halo_serdes.engine.timedomain import run_time_link
    from halo_serdes_app.config_bridge import load_preset, preset_names

    out = {}
    for name in names or preset_names():
        cfg = load_preset(name)
        try:
            ch = ChannelModel.from_config(cfg)
        except Exception as exc:  # noqa: BLE001
            out[name] = {"channel_error": type(exc).__name__}
            continue
        out[name] = {
            "static": _guarded(lambda: result_values(run_static_link(cfg, channel=ch, collect_eye=True))),
            "time": _guarded(lambda: result_values(run_time_link(cfg, channel=ch, collect_eye=True))),
            "stat": _guarded(lambda: result_values(run_statistical(cfg, channel=ch))),
            "com": _guarded(lambda: _com_values(compute_com(ch, cfg))),
        }
        print(f"  preset {name}", file=sys.stderr, flush=True)
    return out


def record_synthetic() -> dict:
    """The paths the presets do not reach."""
    from halo_serdes.analysis.cdr_tracking import tx_edge_offsets_s
    from halo_serdes.analysis.reconstruct import front_end_waveform
    from halo_serdes.config import LinkConfig
    from halo_serdes.config.schema import (
        ChannelConfig, ClockConfig, OpticalConfig, RxConfig, SimConfig, TopologyConfig, TxConfig,
    )
    from halo_serdes.engine.cascade import run_cascade
    from halo_serdes.engine.optical_stage import transmitter_power
    from halo_serdes.engine.static_link import run_static_link
    from halo_serdes.engine.statistical import run_statistical
    from halo_serdes.engine.timedomain import run_time_link

    out = {}
    for arch in ("mixed_signal", "adc_dsp"):
        nrz = arch == "mixed_signal"
        baud = 16e9 if nrz else 26.5625e9
        for clk in (ClockConfig(rj_ui=0.01, sj_ui=0.02, sj_freq=5e6, dcd_ui=0.01),
                    ClockConfig(kind="profile", file=str(CLOCK_PROFILE), f0_hz=baud)):
            cfg = LinkConfig(
                modulation="nrz" if nrz else "pam4", symbol_rate=baud, osr=16,
                channel=ChannelConfig(kind="analytic", length_m=0.2),
                tx=TxConfig(swing=1.0, fir_taps=(-0.08, 0.72, -0.2), fir_n_pre=1, bw=40e9, clock=clk),
                rx=RxConfig(arch=arch, noise_rms=2e-3),
                sim=SimConfig(n_symbols=6000, seed=11, pattern="prbs13" if nrz else "prbs13q"))
            key = f"{arch}/{clk.kind}"
            out[key + "/time"] = _guarded(lambda: result_values(run_time_link(cfg, collect_jitter=True)))
            out[key + "/static"] = _guarded(lambda: result_values(run_static_link(cfg)))
            out[key + "/stat"] = _guarded(lambda: result_values(run_statistical(cfg)))
            out[key + "/edges"] = _guarded(lambda: digest(tx_edge_offsets_s(cfg)))
            out[key + "/recon"] = _guarded(lambda: _recon_values(front_end_waveform(cfg, with_noise=True)))
            if arch == "adc_dsp" and clk.kind == "white":
                top = TopologyConfig(
                    seg_a=ChannelConfig(kind="analytic", length_m=0.05),
                    optical=OpticalConfig(kind="vcsel_mmf", f_r_hz=22e9, damping_hz=30e9, er_db=4.0,
                                          oma_dbm=1.0, length_m=50.0, modal_bw_mhz_km=4700.0),
                    seg_b=ChannelConfig(kind="analytic", length_m=0.05))
                oc = dataclasses.replace(cfg, topology=top,
                                         sim=dataclasses.replace(cfg.sim, n_symbols=4000))
                out["optical/time"] = _guarded(lambda: result_values(run_time_link(oc)))
                # the stat engine's optical noise kernels under a Tx FFE (no
                # preset has both; they missed the FFE until 2026-10-09)
                out["optical/stat"] = _guarded(lambda: result_values(run_statistical(oc)))
                for c in (0.0, 0.3):
                    oc2 = dataclasses.replace(oc, topology=dataclasses.replace(
                        top, optical=dataclasses.replace(top.optical, li_compression=c)))
                    out[f"optical/tx_power/{c}"] = _guarded(
                        lambda: [digest(x) for x in transmitter_power(oc2, through_fibre=True)])
                oc3 = dataclasses.replace(oc, topology=dataclasses.replace(top, retimer="both"))
                out["cascade"] = _guarded(lambda: _cascade_values(run_cascade(oc3)))
    print("  synthetic links", file=sys.stderr, flush=True)
    return out


def compare(a: dict, b: dict) -> tuple[int, list[str]]:
    """(leaf values compared, the differing ones as 'path: old -> new')."""
    diffs, n = [], 0

    def walk(path, x, y):
        nonlocal n
        if isinstance(x, dict) or isinstance(y, dict):
            x = x if isinstance(x, dict) else {}
            y = y if isinstance(y, dict) else {}
            for k in sorted(set(x) | set(y)):
                walk(path + [k], x.get(k), y.get(k))
        else:
            n += 1
            if x != y:
                diffs.append(" / ".join(path) + f": {x!r} -> {y!r}")

    walk([], a, b)
    return n, diffs


def raised(fp: dict) -> list[str]:
    """The entries that recorded an exception instead of values. They compare
    equal when both sides raise the same way, so a broken call in this tool or
    an engine that fails on both commits reads as "bit-identical" -- list them
    so somebody looks."""
    out = []

    def walk(path, x):
        if isinstance(x, dict):
            for k in sorted(x):
                if k in ("error", "channel_error"):
                    out.append(" / ".join(path) + f": {x[k]}")
                else:
                    walk(path + [k], x[k])

    walk([], fp)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    rec = sub.add_parser("record", help="run the engines and write the fingerprint")
    rec.add_argument("out", type=Path)
    rec.add_argument("--presets", nargs="*", help="only these presets (default: all)")
    rec.add_argument("--no-synthetic", action="store_true", help="presets only")
    cmp_ = sub.add_parser("compare", help="diff two fingerprints; exit 1 on any difference")
    cmp_.add_argument("a", type=Path)
    cmp_.add_argument("b", type=Path)
    args = ap.parse_args(argv)

    if args.cmd == "record":
        warnings.filterwarnings("ignore")
        t0 = time.time()
        fp = {"presets": record_presets(args.presets)}
        if not args.no_synthetic:
            fp["synthetic"] = record_synthetic()
        args.out.write_text(json.dumps(fp, indent=1, sort_keys=True))
        n, _ = compare(fp, {})
        print(f"wrote {args.out}: {n} values [{time.time() - t0:.0f}s]")
        _report_raised(fp)
        return 0
    a, b = json.loads(args.a.read_text()), json.loads(args.b.read_text())
    n, diffs = compare(a, b)
    print(f"compared {n} values")
    _report_raised(b)
    if diffs:
        print(f"{len(diffs)} differ:")
        for d in diffs:
            print("  " + d)
        return 1
    print("bit-identical")
    return 0


def _report_raised(fp: dict) -> None:
    errs = raised(fp)
    if errs:
        print(f"{len(errs)} entries raised instead of recording values (expected ones: "
              f"'Library defaults' has no channel file):")
        for e in errs:
            print("  " + e)


if __name__ == "__main__":
    sys.exit(main())
