#!/usr/bin/env python3
"""The engines against an outside reference: serdespy 1.0 on the same links.

The two engines are checked against each other (invariant 3), and two engines
that share a channel import, a pulse builder and a slicer convention can be
wrong together. This runs the same link through serdespy (PyPI ``serdespy``
1.0, Richard Barrie, University of Toronto, MIT): its own Touchstone
reduction (``four_port_to_diff``), its own impulse / pulse construction
(``zero_pad`` + ``freq2impulse``, a ZOH convolution here) and its own
receiver (``Receiver.noise`` / ``slice_signal`` / ``nrz_DFE_BR`` /
``pam4_DFE_BR``) -- and compares, link by link:

1. the channel: serdespy's differential H(f) against ``ChannelModel``;
2. the error rate at the same sampling phase (the pulse peak) and the same
   noise: serdespy's Monte Carlo, our static engine's Monte Carlo, and our
   statistical engine in its static-engine mode (a no-op two-tap FFE,
   ``[1, 0]``: with an FFE the mixed-signal statistical engine has no loop
   and samples at the pulse peak, as the static engine does); a textbook
   ISI enumeration x Q() at that phase, without DFE error propagation, is
   printed beside them for scale.

What is shared and so not checked: the CTLE's *definition* (one zero, two
poles, ``afe.Ctle``) -- the serdespy side evaluates it with
``scipy.signal.freqs`` from the same corner frequencies, because serdespy has
no CTLE; the noise convention (AWGN of ``noise_rms`` at the slicer input,
per sample); the Tx levels (``swing`` peak-to-peak). The CDR is not checked:
serdespy has none, so both sides sample at the pulse peak.

serdespy is not a dependency. Unpack its wheel somewhere and pass the
directory (the one holding ``serdespy/``); its ``Transmitter`` needs the
``samplerate`` package, which is stubbed, since it is not used::

    pip download serdespy==1.0 --no-deps -d /tmp/sp && unzip -q -d /tmp/sp/src /tmp/sp/*.whl
    python -I tools/external_check.py /tmp/sp/src            # ~2 min on 4 cores
    python -I tools/external_check.py /tmp/sp/src --quick    # ~1 min, 1/4 of the symbols

Results and what they mean: ``cairn/external-validation.md``.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
import time
import types
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
CHANNELS = REPO / "data" / "channels"


def _import(sp_dir: str):
    sys.modules.setdefault("samplerate", types.ModuleType("samplerate"))
    sys.path.insert(0, str(Path(sp_dir).resolve()))
    sys.path.insert(0, str(REPO / "src"))
    import serdespy

    return serdespy


@dataclasses.dataclass(frozen=True)
class Case:
    name: str
    file: str
    modulation: str
    rate: float              # symbol rate [Bd]
    noise: float             # AWGN at the slicer [V rms]
    n_dfe: int
    ctle_db: float | None = None
    osr: int = 32


CASES = (
    Case("NRZ 10G Whisper, 4-tap DFE", "TEC_Whisper42p8in_Meg6_THRU_C8C9.s4p",
         "nrz", 10e9, 0.03, 4),
    Case("NRZ 16G Whisper, CTLE 7 dB + 4-tap DFE", "TEC_Whisper42p8in_Meg6_THRU_C8C9.s4p",
         "nrz", 16e9, 0.025, 4, ctle_db=7.0),
    Case("PAM4 16 GBd Whisper, CTLE 7 dB + 4-tap DFE", "TEC_Whisper42p8in_Meg6_THRU_C8C9.s4p",
         "pam4", 16e9, 0.004, 4, ctle_db=7.0),
)


def _q(x):
    from scipy.special import erfc

    return 0.5 * erfc(x / np.sqrt(2.0))


def compare_channels(sdp, files) -> list[tuple[str, float, float, float]]:
    """Per file, the largest |dB| difference of |H| down to -40 dB:

    - on the file's own grid (ours: ``import_diff_network`` +
      ``terminate_renormalize``, no interpolation) -- the mixed-mode
      reduction and the termination;
    - the model as the engines take it (``ChannelModel.from_touchstone``,
      interpolated to a DC-inclusive grid), from twice the file's first
      point (a file that starts at 50 MHz has its DC extrapolated, which
      serdespy does not do);
    - the file grid again, serdespy given (100, 100): what its arguments
      mean (they are against the file's single-ended z0, so (50, 50) is
      the matched differential load)."""
    import skrf as rf

    from halo_serdes.channel import ChannelModel
    from halo_serdes.channel.touchstone import import_diff_network, terminate_renormalize

    def worst(H_sp, H_o, sel):
        sel = sel & (np.abs(H_o) > 0.01)
        return float(np.max(np.abs(20 * np.log10(np.abs(H_sp[sel]) / np.abs(H_o[sel])))))

    out = []
    for name in files:
        path = str(CHANNELS / name)
        net = rf.Network(path)
        H50, f, _, _ = sdp.four_port_to_diff(net, None, 50.0, 50.0)
        H100, _, _, _ = sdp.four_port_to_diff(net, None, 100.0, 100.0)
        on_grid = terminate_renormalize(import_diff_network(path), 100.0, 100.0)
        model = ChannelModel.from_touchstone(path, f_max=f[-1], n_freq=4 * f.size)
        H_m = np.interp(f, model.f, model.H.real) + 1j * np.interp(f, model.f, model.H.imag)
        nz = f > 0
        out.append((name, worst(H50, on_grid, nz), worst(H50, H_m, f >= 2 * f[nz][0]),
                    worst(H100, on_grid, nz)))
    return out


def _config(case: Case, n_symbols: int):
    from halo_serdes.config.schema import (
        ChannelConfig, CtleConfig, DfeConfig, FfeConfig, LinkConfig, RxConfig,
        SimConfig, TxConfig,
    )

    ctle = (CtleConfig(enable=True, peak_db=case.ctle_db) if case.ctle_db is not None
            else CtleConfig(enable=False))
    return LinkConfig(
        modulation=case.modulation, symbol_rate=case.rate, osr=case.osr,
        channel=ChannelConfig(kind="touchstone", file=str(CHANNELS / case.file),
                              zs_diff=100.0, zl_diff=100.0, n_freq=4096),
        tx=TxConfig(swing=1.0),
        rx=RxConfig(ctle=ctle, ffe=FfeConfig(n_pre=0, n_post=0),
                    dfe=DfeConfig(n_taps=case.n_dfe), noise_rms=case.noise),
        sim=SimConfig(n_symbols=n_symbols, seed=3,
                      pattern="prbs31"),
    )


def serdespy_link(sdp, case: Case, cfg, n_symbols: int, seed: int = 11) -> dict:
    """The link through serdespy: its impulse at our dt, the CTLE's transfer
    from ``scipy.signal.freqs``, a ZOH pulse, its Receiver at the pulse peak
    with ideal DFE weights (the pulse's postcursors)."""
    import scipy.signal as sps
    import skrf as rf

    net = rf.Network(str(CHANNELS / case.file))
    osr = case.osr
    # serdespy terminates against the file's single-ended z0: its matched
    # differential load is (50, 50) in its arguments (see compare_channels)
    H, f, h, _ = sdp.four_port_to_diff(net, None, 50.0, 50.0, t_d=cfg.dt)
    if case.ctle_db is not None:
        fp1 = case.rate / 2.0
        fp2, fz = 2.0 * fp1, fp1 / 10.0 ** (case.ctle_db / 20.0)
        wz, wp1, wp2 = 2 * np.pi * fz, 2 * np.pi * fp1, 2 * np.pi * fp2
        _, Hc = sps.freqs([1.0 / wz, 1.0], np.polymul([1.0 / wp1, 1.0], [1.0 / wp2, 1.0]),
                          worN=2 * np.pi * f)
        h, _ = sdp.freq2impulse(H * Hc, f)
    pulse = np.convolve(h, np.ones(osr))[: 400 * osr]
    pk = int(np.argmax(np.abs(pulse)))
    cur = pulse[pk % osr::osr]
    k0 = pk // osr
    main = cur[k0]

    levels = np.array([-0.5, 0.5]) if case.modulation == "nrz" else np.array([-0.5, -1 / 6, 1 / 6, 0.5])
    rng = np.random.default_rng(seed)
    np.random.seed(seed + 1)                   # Receiver.noise draws from the global state
    sym = rng.integers(0, levels.size, n_symbols)
    y = sps.fftconvolve(np.repeat(levels[sym], osr), h)[: n_symbols * osr]
    rx = sdp.Receiver(y[pk % osr:], osr, case.rate / 2.0, levels, shift=False, main_cursor=main)
    rx.noise(case.noise)
    rx.slice_signal()
    taps = cur[k0 + 1: k0 + 1 + case.n_dfe]
    if case.modulation == "nrz":
        rx.nrz_DFE_BR(taps)
        dec = (rx.signal_BR > 0).astype(int)
    else:
        rx.pam4_DFE_BR(taps)
        dec = rx.symbols_out.astype(int)
    skip = 200
    got = dec[k0 + skip: n_symbols + k0 - 50]   # baud sample i holds symbol i - k0
    ref = sym[skip: skip + got.size]
    n_err = int(np.sum(got != ref))

    # textbook: residual ISI (all but the DFE's taps) enumerated, x Q()
    res = np.concatenate([cur[:k0], cur[k0 + 1 + case.n_dfe:]])
    res = res[np.abs(res) > 1e-4 * abs(main)]
    isi, p = np.array([0.0]), np.array([1.0])
    for c in res:
        isi = (isi[:, None] + c * levels[None, :]).ravel()
        p = (p[:, None] * np.full(levels.size, 1.0 / levels.size)[None, :]).ravel()
        if isi.size > 8192:
            hist, e = np.histogram(isi, bins=4096, weights=p)
            isi, p = 0.5 * (e[1:] + e[:-1]), hist
    half = main * (levels[1] - levels[0]) / 2.0              # half an eye
    n_inner = 2 * (levels.size - 1)                          # error directions
    ser_th = float(np.sum(p * _q((half + isi) / case.noise)) * n_inner / levels.size)
    return {"errors": n_err, "n": int(got.size), "ser": n_err / got.size,
            "ser_textbook": ser_th, "main": float(main), "pre1": float(cur[k0 - 1]),
            "post1": float(cur[k0 + 1])}


def _untrimmed(cfg):
    """The config's channel with its impulse response kept whole (2048 UI on
    Whisper at 16 GBd). Until 2026-10-30 the engines trimmed it to 99.99 %
    of its first-difference energy, 14 UI there, and dropped a slow ISI tail
    serdespy keeps (its 400 UI); now the trim runs until the postcursors
    left out are under 1e-3 of the main cursor (RMS, 505 UI), and the two
    rows should agree (ROADMAP P1 #42)."""
    from halo_serdes.channel import ChannelModel

    class Untrimmed(ChannelModel):
        def response_set(self, dt, trim=True, keep_energy=0.9999, ui=None):
            return super().response_set(dt, trim=False)

    ch = ChannelModel.from_config(cfg)
    return Untrimmed(ch.H, ch.f, name=ch.name, ref_gain=ch.ref_gain, ui=ch.ui)


def ours(case: Case, n_symbols: int, channel=None) -> dict:
    from halo_serdes.engine import run_static_link
    from halo_serdes.engine.statistical import run_statistical

    cfg = _config(case, n_symbols)
    mc = run_static_link(cfg, channel=channel, collect_eye=False)
    # the no-op FFE puts the statistical engine where the static one samples
    st = run_statistical(cfg, channel=channel, ffe_taps=np.array([1.0, 0.0]), ffe_pre=0)
    return {"static_ser": mc.ser, "static_ber": mc.ber.ber, "static_errors": mc.ber.n_errors,
            "static_n": mc.ber.n_checked, "stat_ser": st.ser, "stat_ber": st.ber,
            "ep": st.extras.get("dfe_ep_factor"), "cfg": cfg}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("serdespy", help="directory holding an unpacked serdespy 1.0 (serdespy/)")
    ap.add_argument("--quick", action="store_true", help="1/4 of the symbols")
    args = ap.parse_args(argv)
    sdp = _import(args.serdespy)
    n_sym = 150_000 if args.quick else 600_000

    print("== channel: serdespy four_port_to_diff vs ChannelModel.from_touchstone ==")
    print("  max |dB| of |H| down to -40 dB: file grid / our model (interpolated) / "
          "file grid with serdespy given 100/100")
    for name, grid, model, d100 in compare_channels(
            sdp, sorted(p.name for p in CHANNELS.glob("*.s4p"))):
        print(f"  {name:42s} {grid:.3f} / {model:.3f} / {d100:.3f}")

    print("\n== SER at the pulse peak, same noise (Monte Carlo counts in brackets) ==")
    for case in CASES:
        t0 = time.time()
        ref = ours(case, n_sym)
        sp = serdespy_link(sdp, case, ref["cfg"], n_sym)
        print(f"  {case.name}  (noise {case.noise * 1e3:g} mV, main cursor {sp['main']:.4f},"
              f" h-1 {sp['pre1']:.4f}, h+1 {sp['post1']:.4f})")
        print(f"    serdespy MC                {sp['ser']:.3e}  [{sp['errors']} / {sp['n']}]")
        whole = ours(case, n_sym, channel=_untrimmed(ref["cfg"]))
        for label, r in (("", ref), (", whole h", whole)):
            print(f"    ours static MC{label:9s} {r['static_ser']:.3e}"
                  f"  [{r['static_errors']} bit errors / {r['static_n']} bits]"
                  f"   ratio {r['static_ser'] / max(sp['ser'], 1e-300):.2f}")
            print(f"    ours StatEye{label:11s} {r['stat_ser']:.3e}"
                  f"   ratio {r['stat_ser'] / max(sp['ser'], 1e-300):.2f}"
                  f"   (DFE error propagation x{r['ep']:.2f})")
        print(f"    textbook, no EP            {sp['ser_textbook']:.3e}"
              f"   ratio {sp['ser_textbook'] / max(sp['ser'], 1e-300):.2f}"
              f"   [{time.time() - t0:.0f} s]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
