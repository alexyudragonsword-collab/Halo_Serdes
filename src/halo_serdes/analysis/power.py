"""Power estimate per block, from public figures of merit.

Every coefficient in ``PowerCoefficients`` is an estimate taken from public
sources (table, ranges and derivations: ``cairn/功耗建模.md``). The model is
for comparing configurations against each other -- the two receiver
architectures, partial-response target lengths, ADC resolution, LPO / CPO /
retimed -- not for an absolute budget: its totals land within about 2x of
the published transceivers it was checked against, and ratios are what it
is meant to give.

What it counts, per link direction:

- transmitter: a per-bit lump (serialiser, driver, its analog FIR), plus the
  digital FIR when the transmitter has a DAC (``tx.dac_bits``);
- receiver, both architectures: a per-bit lump for what they share (CTLE /
  VGA front end, clocking and CDR), the same coefficient for both so the
  comparison only sees what differs (invariant 2);
- mixed-signal receiver: clocked comparators (data slicers, times N for an
  unrolled first DFE tap, an error slicer when the DFE adapts, an edge slicer
  for a bang-bang CDR) and the analog DFE taps;
- ADC receiver: the ADC from a Walden or Schreier figure of merit at the
  configured ENOB, and the digital FFE / DFE as multiplies and adds at the
  word widths ``numeric.*`` configures;
- sequence detector (either architecture): Viterbi branch and path metrics
  over ``N ** (len(pr.target) - 1 + mlsd.memory)`` states, or the sliding
  detector's three hypotheses over its window;
- optics (``topology.optical`` set): a per-bit lump for the E/O and O/E ends,
  a pluggable module's or a co-packaged engine's (``topology.optics_placement``);
  the difference stands for the module's own overhead (its power conversion
  and management, a driver and TIA reaching across the host's board trace);
- retimer (``topology.retimer == "both"``): two more receivers and
  transmitters, priced by the same rules from ``retimer_rx`` / ``retimer_tx``.

Digital energy is arithmetic only: Horowitz's 45 nm 32-bit add and multiply,
scaled by width (adder linear, array multiplier as the product of the two
widths) and to 7 nm by DeepScaleTool's energy factor. Pipeline registers, clock tree, the
parallelism a 100 GBd datapath needs, LMS updates and Viterbi survivor
memory are not counted, so digital blocks are a lower bound;
``digital_overhead`` multiplies all of them for a sensitivity check.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from ..config.schema import LinkConfig, RxConfig, TxConfig


@dataclass(frozen=True)
class PowerCoefficients:
    """Coefficients of the power model; each is an estimate (module docstring).

    Defaults and their sources (``cairn/功耗建模.md`` has the full table):

    - ``adc_fom_walden_j``: 70 fJ/conv-step, the median of the six
      time-interleaved SAR ADCs at >= 40 GS/s in 4-7 nm, ISSCC / VLSI
      2021-2026 (Murmann ADC survey; range 38.4-120.2 fJ);
    - ``adc_fom_schreier_db``: 146 dB, the median of the same six rows
      (143.5-150.1 dB);
    - ``mult32_j`` / ``add32_j``: 3.1 / 0.1 pJ, 32-bit integer multiply /
      add at 45 nm, 0.9 V (Horowitz, ISSCC 2014);
    - ``node_scale``: 0.175, the 45 nm -> 7 nm energy ratio of DeepScaleTool
      (Sarangi & Baas, ISCAS 2021: 0.11 / 0.63 against 65 nm; the tool stops
      at 7 nm, so 5 / 3 nm parts are cheaper than this);
    - ``comparator_j`` / ``analog_dfe_tap_j``: 100 fJ per decision / per tap
      per symbol, no public per-block figure found -- a bare dynamic
      comparator is 15-30 fJ, the margin is for the clock buffer at 10+ GHz;
    - ``rx_common_j_per_bit``: 1.2 pJ/b, a 7 nm 56 Gb/s CTLE-only PAM4
      receiver (1.41 pJ/b) less its slicers at ``comparator_j``; a 224 Gb/s
      5 nm ADC receiver leaves only about 0.57 pJ/b for the same blocks;
    - ``tx_j_per_bit``: 1.0 pJ/b, between a 16 nm 64 Gb/s transmitter
      (1.39 pJ/b) and a 3 nm 224 Gb/s DAC transmitter (sub-pJ/b);
    - ``optics_j_per_bit``: 9.0 pJ/b, measured 800G LPO modules
      (9-9.7 pJ/b; vendors quote 5-8 W per 800G, 6-10 pJ/b);
    - ``co_packaged_optics_j_per_bit``: 6.9 pJ/b, a co-packaged optical
      engine: Broadcom's 51.2T CPO at 5.5 W per 800G, and a 2026 switch-
      level comparison's 2024 CPO figure, 23% below LPO (range 5.6, NVIDIA's
      9 W per 1.6T port, to 8.0, Broadcom's previous CPO at 6.4 W per 800G).
    """

    adc_fom: Literal["walden", "schreier"] = "walden"
    adc_fom_walden_j: float = 70e-15        # [J per conversion step]
    adc_fom_schreier_db: float = 146.0      # [dB]
    mult32_j: float = 3.1e-12
    add32_j: float = 0.1e-12
    node_scale: float = 0.175
    digital_overhead: float = 1.0
    metric_bits: int = 12                   # Viterbi branch / path metric width
    comparator_j: float = 100e-15
    analog_dfe_tap_j: float = 100e-15
    rx_common_j_per_bit: float = 1.2e-12
    tx_j_per_bit: float = 1.0e-12
    optics_j_per_bit: float = 9.0e-12
    co_packaged_optics_j_per_bit: float = 6.9e-12

    def __post_init__(self):
        if self.adc_fom not in ("walden", "schreier"):
            raise ValueError(f"adc_fom must be 'walden' or 'schreier', got {self.adc_fom!r}")
        for name in ("adc_fom_walden_j", "mult32_j", "add32_j", "node_scale", "digital_overhead",
                     "comparator_j", "analog_dfe_tap_j", "rx_common_j_per_bit", "tx_j_per_bit",
                     "optics_j_per_bit", "co_packaged_optics_j_per_bit"):
            if not getattr(self, name) >= 0.0:
                raise ValueError(f"{name} must be >= 0, got {getattr(self, name)}")
        if self.metric_bits < 1:
            raise ValueError(f"metric_bits must be >= 1, got {self.metric_bits}")


@dataclass(frozen=True)
class PowerBlock:
    name: str          # dotted: "host.rx.adc", "retimer.tx", "optics"
    watts: float
    note: str = ""


@dataclass(frozen=True)
class PowerReport:
    blocks: tuple[PowerBlock, ...]
    bit_rate: float    # [bit/s], one direction

    @property
    def total_w(self) -> float:
        return float(sum(b.watts for b in self.blocks))

    @property
    def pj_per_bit(self) -> float:
        return self.total_w / self.bit_rate * 1e12

    def watts(self, prefix: str = "") -> float:
        """Sum of the blocks whose name starts with ``prefix``."""
        return float(sum(b.watts for b in self.blocks if b.name.startswith(prefix)))

    def pj(self, prefix: str = "") -> float:
        return self.watts(prefix) / self.bit_rate * 1e12

    def table(self) -> str:
        rows = [f"  {b.name:<22} {1e3 * b.watts:8.1f} mW {b.watts / self.bit_rate * 1e12:6.2f} pJ/b"
                + (f"  {b.note}" if b.note else "") for b in self.blocks]
        rows.append(f"  {'total':<22} {1e3 * self.total_w:8.1f} mW {self.pj_per_bit:6.2f} pJ/b")
        return "\n".join(rows)


# ---------------------------------------------------------------- primitives

def mult_energy(a_bits: int, b_bits: int, k: PowerCoefficients) -> float:
    """One a x b bit multiply [J]: the 32 x 32 figure scaled by the partial
    products, a * b / 1024."""
    return k.mult32_j * (a_bits * b_bits) / 1024.0 * k.node_scale * k.digital_overhead


def add_energy(bits: int, k: PowerCoefficients) -> float:
    """One ``bits``-wide add or compare [J]: the 32-bit figure scaled by width."""
    return k.add32_j * bits / 32.0 * k.node_scale * k.digital_overhead


def adc_power(fs: float, enob: float, k: PowerCoefficients) -> float:
    """ADC power [W] at sample rate ``fs`` (Nyquist) and ``enob`` effective bits.

    Walden: P = FOM_W * 2**ENOB * fs, 2x per bit. Schreier: FOM_S = SNDR +
    10 log10(fs / 2 / P) with SNDR = 6.02 ENOB + 1.76 dB, 4x per bit.
    """
    if k.adc_fom == "walden":
        return k.adc_fom_walden_j * 2.0 ** enob * fs
    sndr = 6.02 * enob + 1.76
    return fs / 2.0 * 10.0 ** ((sndr - k.adc_fom_schreier_db) / 10.0)


def trellis_length(cfg: LinkConfig, rx: RxConfig | None = None) -> int:
    """Cursors after the main one the Viterbi trellis spans: the controlled
    ones plus the residual memory (``engine/timedomain.py``
    ``_mlsd_post_detect``); 0 without a Viterbi detector."""
    rx = cfg.rx if rx is None else rx
    if rx.mlsd.kind != "viterbi":
        return 0
    return len(cfg.pr.target) - 1 + rx.mlsd.memory


def trellis_states(cfg: LinkConfig, rx: RxConfig | None = None) -> int:
    """States of the Viterbi trellis the receiver runs: N ** K, K the
    ``mlsd.state_memory`` of a reduced-state one or else the trellis length;
    0 without a Viterbi detector."""
    rx = cfg.rx if rx is None else rx
    if rx.mlsd.kind != "viterbi":
        return 0
    return (2 ** cfg.bits_per_symbol) ** _state_memory(cfg, rx)


def _state_memory(cfg: LinkConfig, rx: RxConfig) -> int:
    n_len = trellis_length(cfg, rx)
    return n_len if rx.mlsd.state_memory is None else min(rx.mlsd.state_memory, n_len)


# ------------------------------------------------------------------- blocks

def _sequence_detector(cfg: LinkConfig, rx: RxConfig, slicer_bits: int, baud: float,
                       k: PowerCoefficients, name: str) -> list[PowerBlock]:
    n = 2 ** cfg.bits_per_symbol
    if rx.mlsd.kind == "viterbi":
        s = trellis_states(cfg, rx)
        branches = s * n
        n_len = trellis_length(cfg, rx)
        fed_back = n_len - _state_memory(cfg, rx)           # cursors a reduced trellis feeds back
        # y times each cursor once, the per-branch metric from those shared
        # partial products (two adds), add-compare-select (one add per
        # branch, N - 1 compares per state); a reduced-state trellis adds,
        # per state, its survivor's fed-back cursors (cursor x level from a
        # table, one add each)
        e = ((n_len + 1) * mult_energy(slicer_bits, cfg.numeric.ffe_weight.wl, k)
             + (3 * branches + s * (n - 1) + s * fed_back) * add_energy(k.metric_bits, k))
        note = f"Viterbi {s} states" + (f", {fed_back} cursors fed back" if fed_back else "")
        return [PowerBlock(name, e * baud, note)]
    if rx.mlsd.kind == "sliding":
        # no-error and two flip hypotheses, a squared error each over the window
        ops = 3 * rx.mlsd.seq_len
        e = ops * (mult_energy(slicer_bits, slicer_bits, k) + add_energy(k.metric_bits, k))
        return [PowerBlock(name, e * baud, f"sliding, window {rx.mlsd.seq_len}")]
    return []


def rx_blocks(cfg: LinkConfig, rx: RxConfig, k: PowerCoefficients,
              prefix: str = "rx") -> list[PowerBlock]:
    """One receiver's blocks [W] at ``cfg``'s symbol rate and modulation."""
    baud = cfg.symbol_rate
    n = 2 ** cfg.bits_per_symbol
    out = [PowerBlock(f"{prefix}.common", k.rx_common_j_per_bit * cfg.data_rate,
                      "CTLE / VGA, clocking, CDR")]
    slicer_bits = rx.adc.n_bits + 2         # FFE output word the slicer and trellis see
    if rx.arch == "adc_dsp":
        enob = rx.adc.n_bits if rx.adc.enob is None else rx.adc.enob
        note = f"ENOB {enob:g}" + (" (enob unset: n_bits)" if rx.adc.enob is None else "")
        out.append(PowerBlock(f"{prefix}.adc", adc_power(baud, enob, k), note))
        taps = rx.ffe.n_pre + 1 + rx.ffe.n_post
        wl = cfg.numeric.ffe_weight.wl
        acc = rx.adc.n_bits + wl + math.ceil(math.log2(taps))
        e = taps * mult_energy(rx.adc.n_bits, wl, k) + (taps - 1) * add_energy(acc, k)
        out.append(PowerBlock(f"{prefix}.ffe", e * baud, f"{taps} taps"))
        # slicing: N - 1 compares; a DFE tap feeds back level x weight (a
        # select) and adds; the first tap is resolved speculatively at these
        # rates, N candidates each sliced
        n_dfe = rx.dfe.n_taps
        compares = (n - 1) * (n if n_dfe else 1)
        adds = (n + n_dfe - 1) if n_dfe else 0
        e = (compares + adds) * add_energy(slicer_bits, k)
        out.append(PowerBlock(f"{prefix}.dfe", e * baud,
                              f"{n_dfe} taps" if n_dfe else "slicer only"))
    else:
        n_dfe = rx.dfe.n_taps
        unrolled = n_dfe > 0 and rx.dfe.tap1_mode == "unrolled"
        n_cmp = (n - 1) * (n if unrolled else 1)
        n_cmp += 1 if rx.dfe.adapt != "none" else 0          # error slicer
        n_cmp += 1 if rx.cdr.kind == "bang_bang" else 0      # edge slicer
        out.append(PowerBlock(f"{prefix}.comparators", n_cmp * k.comparator_j * baud,
                              f"{n_cmp} comparators"))
        n_taps = n_dfe - (1 if unrolled else 0)
        out.append(PowerBlock(f"{prefix}.dfe", n_taps * k.analog_dfe_tap_j * baud,
                              f"{n_dfe} analog taps" + (", tap 1 unrolled" if unrolled else "")))
    out += _sequence_detector(cfg, rx, slicer_bits, baud, k, f"{prefix}.mlsd")
    return out


def tx_blocks(cfg: LinkConfig, tx: TxConfig, k: PowerCoefficients,
              prefix: str = "tx") -> list[PowerBlock]:
    """One transmitter's blocks [W]: the per-bit lump, and with a DAC the
    digital FIR (a symbol times a weight is a select, so one add per tap at
    the DAC width; a transmit-side PR target lengthens the FIR)."""
    out = [PowerBlock(f"{prefix}.driver", k.tx_j_per_bit * cfg.data_rate,
                      "serialiser, driver" + ("" if tx.dac_bits else ", analog FIR"))]
    if tx.dac_bits:
        taps = len(tx.fir_taps) + (len(cfg.pr.target) - 1 if cfg.pr.at_tx else 0)
        out.append(PowerBlock(f"{prefix}.fir", taps * add_energy(tx.dac_bits, k) * cfg.symbol_rate,
                              f"{taps} taps, {tx.dac_bits}-bit DAC"))
    return out


def power_report(cfg: LinkConfig, coeffs: PowerCoefficients | None = None) -> PowerReport:
    """Per-block power of one link direction of ``cfg`` (module docstring)."""
    k = PowerCoefficients() if coeffs is None else coeffs
    blocks = tx_blocks(cfg, cfg.tx, k, "host.tx") + rx_blocks(cfg, cfg.rx, k, "host.rx")
    topo = cfg.topology
    if topo is not None and topo.optical.kind != "none":
        co = topo.optics_placement == "co_packaged"
        j = k.co_packaged_optics_j_per_bit if co else k.optics_j_per_bit
        blocks.append(PowerBlock("optics", j * cfg.data_rate,
                                 f"{topo.optical.kind} E/O + O/E, "
                                 + ("co-packaged engine" if co else "pluggable module")))
    if topo is not None and topo.retimer == "both":
        # a retimer at the module's ingress and one at its egress
        pair = (tx_blocks(cfg, topo.retimer_tx, k, "retimer.tx")
                + rx_blocks(cfg, topo.retimer_rx, k, "retimer.rx"))
        blocks += [PowerBlock(b.name, 2.0 * b.watts, (b.note + ", " if b.note else "") + "x2")
                   for b in pair]
    return PowerReport(tuple(blocks), cfg.data_rate)
