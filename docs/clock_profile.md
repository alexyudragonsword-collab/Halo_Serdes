# Clock phase-noise profile

The file a PLL simulator hands to this link simulator, and the only thing the
two share. `pll_simulator` writes it (`pllsim export <preset> --clock-profile`,
or `examples/ex22_export_clock_profile.py` for the seven shipped files);
Halo_Serdes reads it through `tx.clock.kind: profile`. Neither library imports
the other. The format is defined here, on the consumer side; the producer
obeys it and adds nothing but `#` comment lines.

## Why a file

`tx/jitter.py` used to model clock jitter the way a jitter spec sheet states
it: independent Gaussian edge noise (`rj_ui`), one sinusoidal tone (`sj_ui`,
`sj_freq`) and duty-cycle distortion (`dcd_ui`). A PLL does not make that
clock. It makes 1/f³ and 1/f² close to the carrier, a flat in-band floor where
the reference and the phase detector dominate, and spurs at the reference and
fractional offsets. A CDR is a high-pass to clock jitter, so two clocks with
the same 200 fs RMS leave very different residues behind it. The profile is
how that question gets asked: "this PLL, this CDR bandwidth, does the link
close?"

A profile is data a frozen `LinkConfig` can name (invariant #1). A PLL object
is not. And the producer needs matplotlib, which the Android build, carrying
numpy and scipy alone, cannot have.

## Format

YAML, one file per clock.

```yaml
# comment lines are ignored
f0_hz: 6253015600.0            # carrier the phase noise is measured against [Hz]
f_hz: [100.0, 103.9, ...]      # offset grid, ascending, > 0 [Hz]
l_dbc_hz: [-92.6, -92.7, ...]  # total L(f) on that grid, same length [dBc/Hz]
spurs:                          # may be empty: `spurs: []`
  - {f_hz: 62400.0, dbc: -65.6}
source: "pllsim preset bench_wu19_spll_frac_52m_6p253g, linear model (analyze), pllsim 0.9.6, commit 67fe1c7268ab"
```

| key | required | meaning |
|---|---|---|
| `f0_hz` | yes | PLL output carrier. **Timing is phase / (2π f0_hz).** |
| `f_hz` | yes | Offset frequencies. The producer writes a log-uniform grid from 100 Hz to f0/2 at 60 points per decade; the reader accepts any ascending positive grid with at least two points. |
| `l_dbc_hz` | yes | Total single-sideband L(f) = S_φ/2 in dBc/Hz. Same length as `f_hz`. |
| `spurs` | no | List of `{f_hz, dbc}`. `dbc` is the single-sideband spur level: a sinusoidal phase modulation of peak A rad has sidebands 20·log10(A/2) dB below the carrier. |
| `source` | no | Free text, English: what produced the curve (preset, model, package version, commit). Shown to a human; never parsed. |

Unknown keys are an error, like everywhere else in the config layer.

## Three conventions that are easy to lose

**Phase against f0, not against the baud rate.** The same dBc/Hz on a
half-rate clock at f_baud/2 is twice the seconds of a full-rate clock. The file
states f0 so the reader never derives it from `1/UI`. `tx.clock.f0_hz` in the
link config overrides the file's value and changes only this scale, not the
curve — the case for it is a profile measured at one carrier and reused for a
divided or multiplied clock.

**L(f) is single-sideband.** The synthesiser wants S_φ(f), the one-sided PSD of
the phase in rad²/Hz, whose integral is the phase variance. S_φ = 2·10^(L/10).
Dropping the factor of two is a √2 on every jitter number downstream.

**Spur `dbc` is the sideband level.** A = 2·10^(dbc/20) rad peak; the RMS phase
of the tone is A/√2 = √(2·10^(dbc/10)). This is how pll_simulator computes its
analytic spurs (`20·log10(Δφ/2)`), and `tests/test_clock_profile.py` reads the
sideband back off a synthesised phase spectrum to pin it.

## What the reader does with it

`halo_serdes.tx.clock.ClockProfile.load(path)` parses and validates the file.
`edge_offsets_s(n_edges, ui, rng)` then:

1. interpolates L(f) linearly in (log f, dB) onto the FFT grid of an
   `n_edges`-point sequence at the edge rate 1/UI;
2. holds the first value flat below `f_hz[0]` and sets S_φ to **zero above
   f0/2** — phase noise of a clock at f0 is defined up to its own Nyquist and
   no further, and holding the floor flat out to f_baud/2 would add noise the
   profile never stated;
3. synthesises φ[k] with the vendored `synth_from_psd` (FFT-domain shaping of
   white Gaussian noise; DC bin zeroed, so nothing below 1/(n_edges·UI) is
   representable);
4. adds one sinusoid per spur with a uniformly random starting phase;
5. divides by 2π f0 to get seconds.

The result is the same per-edge offset array `tx/jitter.py` always produced;
the white-kind terms (`rj_ui`, `sj_ui`, `dcd_ui`) are added on top, so a
profile clock can still carry an SJ tone for a JTOL sweep. Random numbers are
drawn only for `kind: profile`, before the white-kind draws, which is what
keeps `kind: white` byte-identical to the engine before profiles existed.

## Two things the numbers will do that look like bugs

**A pure 1/f² profile does not integrate to one number per run.** Its phase
variance is dominated by the lowest frequencies the run can resolve, each of
which is a single χ²(2) draw, so the RMS of one realisation scatters by tens of
percent around its expectation; and that expectation is the *bin sum*
Σ S_φ(k·Δf)·Δf, which for 1/f² exceeds the continuous integral from Δf by
exactly π²/6. A loop-shaped profile — flat below the PLL bandwidth, as every
real one is — has neither problem, and the shipped seven are within a few
percent of their integral in a single run.

**`calc_jitter` files coloured jitter under Pj, not Rj.** Its spectral split
flags any bin far above the median as periodic; a 1/f² spectrum puts its
lowest bins orders of magnitude above the median, so the random walk is
reported as Pj and Rj comes out at a few percent of the true σ. The total TIE
(`std(JitterResult.tie)`) is the number to compare against the profile's
integral. `analysis/jitter.py` is the acceptance instrument for this feature
and was left unchanged on purpose.

## Using it

```yaml
tx:
  clock:
    kind: profile
    file: data/clock_profiles/bench_wu19_spll_frac_52m_6p253g.yaml
    # f0_hz: 6.253e9       # optional carrier override
    # sj_ui: 0.02          # white-kind terms still apply on top
    # sj_freq: 10.0e6
```

Repo-relative paths resolve the way `channel.file` does: through
`halo_serdes_app.config_bridge.resolve_data_file`, which also finds the files
beside a frozen desktop bundle and under `$HALO_SERDES_DATA_DIR` on a phone.
A library caller passes an absolute path.

Shipped profiles (`data/clock_profiles/`): the seven JSSC benchmark anchors of
pll_simulator's ex14, exported by its ex22 at commit 67fe1c7 on the
`feat/clock-profile-export` branch. Carriers run 1.5–10.25 GHz; integrated
jitter 100 Hz..f0/2 runs 40–830 fs.

## Vendored code

`src/halo_serdes/vendor/pllsim/colored.py` (`synth_from_psd`) and
`jitter.py` (`integrate_pn`, `sphi_from_ldbc`, `ldbc_from_sphi`) are
byte-identical copies of `pll_simulator@931cfaf src/pllsim/core/*.py` with a
two-line attribution header. `tools/vendor_check.py` re-derives that against a
sibling checkout and the `vendor-drift` CI job fails on any undeclared edit.
See `src/halo_serdes/vendor/__init__.py`.

## Through the CDR (phase 2)

A CDR is a high-pass to the clock: wander inside its bandwidth is followed,
the rest lands on the sampling instant, and the loop adds some jitter of its
own. The time engine gets this for free by running the loop. The statistical
engine cannot run anything, so `cdr/linear.py` gives it the loop in closed
form and `engine/statistical.py` smears its bathtub with the result instead
of with `rj_ui`:

```
sigma_sampling^2 = integral S_phi(f) |1 - H(f)|^2 df / (2 pi f0)^2   (what the loop left)
                 + var(q) / k_pd^2 * mean |H(f)|^2                    (what the loop added)
```

`H` is the closed-loop response of the PI loop the kernels actually run
(`kp = 2^-kp_shift`, `ki = 2^-ki_shift`, one update per symbol for the
mixed-signal kernel, one per interleave block for the ADC kernel), `k_pd` the
linearised detector gain and `q` the detector's noise. The band starts at
1/(N·UI), the lowest offset a run of N symbols resolves, so both engines look
at the same part of the profile. The two detectors differ only in `k_pd` and
`var(q)`:

| detector | gain | noise | how |
|---|---|---|---|
| bang-bang (mixed-signal) | `rho sqrt(2/pi) / sigma_e`, `sigma_e` = jitter at its input | `rho - rho^2/3` per update | fixed point on `sigma_e^2 = untracked^2 + self^2 + edge^2`; `edge` is AWGN + ISI at the edge sample over the edge slope, read off the pre-DFE pulse at the lock point |
| Mueller-Muller (ADC) | mean slope of `x (sign x+ - sign x-)` | its density at low frequency, over `n_lanes`; a short repeating pattern's data share as lines at multiples of its repetition rate | seeded Monte-Carlo over random symbols on the pulse's cursor set at the lock point; on equalised samples `x` is the slicer input -- the DFE's (and a PR target's) postcursors taken out at their lock-point values, the lock at the peak |

Both read the pulse *at the lock point*, not at its peak: an NRZ pulse through
a short channel is a 1-UI plateau whose argmax is its leading corner, and the
Alexander edge locks half a UI into it. Reading at the peak was a factor of
several in the first cut.

**How well it holds** (`tests/test_clock_profile_cdr.py`, all time-engine
measurements of the recovered clock minus the Tx clock, mean removed):

| link | kp_shift | measured / model sigma |
|---|---|---|
| 16 GBd NRZ, lossy + AWGN, 5 MHz-corner 1/f² at 0.11 UI | 4 / 6 / 8 | 0.97 / 0.99 / 1.00 |
| same, clean channel, no noise | 4 / 6 / 8 | 0.82 / 0.95 / 1.08 |
| 112 GBd PAM4 ADC, SSPLL scaled to 200 fs | 5 / 7 / 9 | 1.02 / 1.23 / 0.96 |
| same, RMS over six seeds, the model a run from rest (2026-10-15) | 4 / 7 / 9 | 1.06 / 1.10 / 1.09 |

(The ADC row is example 31's printout, 2026-10-14 full-size rerun after
ROADMAP P2 #24: the model's detector reads the slicer input after the DFE, as
the kernel does, and its noise is the density at low frequency that the loop
integrates -- on PRBS13Q the receiver noise's alone, see below. Before it,
with the DFE's postcursor left in the model's samples and the per-symbol
variance taken for white noise, 0.82 / 1.03 / 0.80, 2026-10-13; 0.95 / 1.22 /
0.92 on the 2026-10-08 rerun with the DFE idle, and 0.96 / 0.96 / 0.73 before
that.) Over kp_shift 4-9 the model is 0.81-1.07x the measured on both PLL
profiles, lowest at mid gain where the preset's loop (`ki_shift 15` with a
tiny detector gain: integrator-dominated, underdamped) peaks. Measured with a
frozen loop the detector's gain is 0.0276 V/UI, the model's 0.032; with the
DFE's postcursor left in the model's samples it was 0.0195, and the CDR error
26-39 % high at both ends of the sweep. (2026-10-28: the 0.032 was read at the
equalised pulse's peak, two samples after where the loop samples; read there
(`mm_pd_statistics(lock_shift=)`) it is the kernel's, 0.0292 against 0.0292 with
the equalisers frozen, and the loop's |E(f)| under sinusoidal jitter matches
the kernel's to 2 % at 0.5-4 MHz -- 13-15 % low in band and 1.65x low at
kp_shift 8's resonance before. Example 31's model CDR error moved 1-4 %,
its loop bandwidth 1.78 / 0.89 / 0.75 -> 1.56 / 0.82 / 0.71 MHz at
kp_shift 4 / 5 / 6.)

(2026-10-15 correction, ROADMAP P2 #28: the 112 GBd row above and the
"0.81-1.07x" were one seed -- example 31's seed 3, its second half -- against
the steady state. Over 16 seeds the steady state reads the RMS 0.97-1.05x up
to kp_shift 6 and 1.14 / 1.34 / 1.70x at 7 / 8 / 9; one seed scatters
0.32-1.02 samples at kp_shift 9. See the next paragraph.)

**A run is not the steady state.** From kp_shift 7 example 31's loop is
underdamped: its error response peaks 4 / 8 / 16x near 1.1 MHz (damping
~0.02), and the peak builds over ``~Q / (pi f)`` -- far longer than 200k
symbols (1.8 us). A time-domain run starts the loop from rest, and its clock
sits on the lines ``k / (N UI)`` the synthesiser puts it on, so it never sees
the peak rung up. Driven the same way, the linear loop model reproduces the
time engine's 16-seed RMS (0.636 / 0.705 against 0.618 / 0.674 samples at
kp_shift 8 / 9), and in closed form
(`ErrorResponse.run_mean_sq`, `ClockProfile.run_untracked_sigma_s`) it reads
it 1.00-1.04x over kp_shift 4-9. That is what the statistical engine now
smears with; the steady state is reported (`LoopSolution.sigma_steady_ui`,
the GUI's jitter panel) and warned when over 1.25x the run's -- a longer run,
and the link in service, see it. The run's error grows as the loop rings up
(0.36 -> 0.90 samples over the run at kp_shift 9), and a Gaussian of its mean
reads the tail short, so the smear is averaged over twelve points of the run
(`sigma_over_run_ui`). Under it the DFE is held at the lock point's taps, as
on white clocks, and where the smear is under two samples the curve is
computed between samples: read linearly between whole samples a steep bathtub
came out 1.6-3.6x high half a sample off the lock. Example 31's link over
kp_shift 4-9 and both PLL profiles: 0.66-1.40x the time engine's BER over 12
seeds (until then 0.24-1.42x). What is left at kp_shift 8-9: the time engine's
LMS FFE follows part of the slow wander, 1.6-1.8x lower BER than with it
frozen, which this does not model.

(2026-10-15 update, ROADMAP P2 #29: it is modelled now, and the
"curve reads 0.6-0.8x off the lock" registered with it was the PAM4 slicer's
thresholds, re-placed at each phase's own main cursor -- the time engine sets
its levels once. Held with the taps, the curve reads the time engine frozen
off the lock 0.95-1.19x (re-placed 0.42-0.79x, example 31 and the PAM4 32G
preset). The LMS FFE moves its taps along their timing derivative and takes
up ``n_lanes * mu * lambda'`` of the offset per loop update
(``lambda' = w'Rw' / w'w'``; 2.4e-5 on example 31 at mu 1e-3, where the
time engine's CDR error fits 3e-5): with the detector on the FFE's output a
second tracking path beside the loop, on raw samples a high-pass after it
(`LoopParams.ffe_track`; the GUI's jitter panel draws `|E|` with it). Over
kp_shift 4-9, both profiles and mu 0 / 1e-3 / 1e-2: 0.68-1.61x the time
engine over 12 seeds (mu 1e-2 at kp_shift 9 was ~12x), the loop model's
sigma 0.93-1.01x the measured CDR error. With the equalisers frozen the
read stays 0.68-0.88x -- 0.78-0.87x at kp_shift 4 where sigma matches -- left
with the Gaussian smear's tails, ROADMAP P2 #30.)

**The Mueller-Muller detector's noise is not white.** Summed over a block,
`x[n] (sign x[n+1] - sign x[n-1])` is `x[n] sign x[n+1] - x[n+1] sign x[n]` up
to the two end terms: neighbours' main cursors cancel, up to the difference
of their magnitudes on PAM4, and what a loop far slower than the symbol rate
integrates is a fifth of the per-symbol variance on both links measured.
Taken as white, the model read the self-noise 2-3x high against the kernel on
random symbols. And the data's share is a function of the pattern: PRBS13Q
repeats every 8191 symbols, so it sits on lines at multiples of 13.7 MHz at
112 GBd -- above the loop -- and the kernel's self-noise on example 31's link
is 0.0003-0.0008 UI against 0.0031-0.0102 UI on random symbols (kp_shift
5-9; model 0.0008-0.0015 and 0.0037-0.0105). The model takes the period from
`sim.pattern` (PRBS-N, up to the size where its lines crowd into a
continuum; PRBS31 and PRQS10 count as random) and passes the lines through
`|H|^2` one by one.

Two things a linear model of this loop does not see on its own. On the
fingerprint's 53 Gb/s link the detector's timing function (the kernel's, with
a frozen loop, and the model's agree over +-3 samples) turns over 0.42
samples (0.026 UI) after the lock point, at most 2e-4 above zero: past the
turnover the pull back weakens, past its next zero it reverses, and the
integrator winds the loop off for good -- on random symbols the kernel
wanders off by 7.7 UI while the model reads 0.06 UI. Since 2026-10-14
(ROADMAP P2 #26) the model reports how far the pull keeps growing
(`LoopSolution.hold_range_ui`, `cdr.linear.mm_hold_range_ui`) and the
statistical engine warns when that is under 8x the loop's own wander
(`sigma_wander_ui`: its detector noise plus the clock it follows late inside
its bandwidth; jitter above the bandwidth only blurs the timing function, and
counting it warned on every one of example 31's loops, which hold). Over
PRBS7Q-15Q and kp_shift 4-10 on the SSPLL profile, 4 seeds of 200k symbols
each, the kernel lost lock at 2.8-6.9x and never from 8.6x; on a white clock
it held down to 1.4x. The ADC presets turn over 0.075-0.15 UI out, 15-33x
on random symbols; example 31's loops sit at 15-37x. An ADC receiver on a
white clock has no loop model and so no such check. (Until then this said the kernel holds on
PRBS13Q: on a white clock it does, 4 seeds of 4; on the profile clock it lost
lock on 1 of 4 at kp_shift 6 and on 3 of 4 from 8.) And a slow loop takes long to settle: on that link at
kp_shift 6 the time engine's error falls 0.0091 -> 0.0061 -> 0.0046 ->
0.0036 UI over successive quarter-runs of 400k symbols (model 0.0039), so a
short run reads its acquisition, not its tracking.

**White clocks.** A bang-bang loop hunts on any clock, so since 2026-10-15
(ROADMAP P2 #27) the mixed-signal receiver runs the same loop model on white
clocks too: the receiver's RJ through the error response, the transmitter's
RJ in the detector's edge noise (it randomises the edge samples; it reaches
the slicer as edge noise, not a sampling shift). On PAM4 the detector's sign
changes between unequal magnitudes (+-1 <-> -+1/3) cross 0.14-0.15 UI off the
lock point and carry noise but no timing; the model takes the transitions by
class. Hunting, model / measured: NRZ 1.02-1.07x, PAM4 1.04-1.3x (taking
every sign change at the lock read 0.65-0.72x). Under the smear the DFE stays
at the taps of the phase read -- the loop moves far faster than the sign-sign
LMS -- while the bathtub re-adapts at every phase. (Profile clocks kept the
re-adapted read under the smear until ROADMAP P2 #28: with the taps held,
example 31 read 6.6-15x high, which was the steady-state sigma and the linear
read between samples -- see "A run is not the steady state". Now held on
both.) Stat / time on steep
bathtubs (20+ errors): NRZ 16G 0.11-0.54x before, 0.50-0.89x now; PAM4 32G
0.48-0.71x before, 0.99-1.03x now; NRZ 28G 0.53-0.98x before, 0.75-1.12x now.
What is left on NRZ 16G is the Gaussian: the sampler's own phase distribution
reads 1.15-1.4x what a Gaussian of its sigma does there. (With the curve taken
between samples, 2026-10-15: NRZ 16G 0.45-0.89x, PAM4 32G 0.99-1.01x, NRZ 28G
0.67-1.08x. With the PAM4 slicer's thresholds held as well, ROADMAP P2 #29:
PAM4 32G 1.03-1.13x, and deeper -- 37-119 errors over 8 M symbols -- 1.09-1.20x
at 0.03 UI RJ and 1.31-1.40x at 0.01 UI, ROADMAP P2 #30; NRZ unchanged.)

(2026-10-15 update, ROADMAP P2 #30: that deeper excess was two things, not
the Gaussian's tails -- the measured phase distribution in its place moves
the read 0-15 %. PAM4's off-lock sign changes give a detector output the
data sets, +1 from the larger magnitude to the smaller and -1 back, which
consecutive transitions largely cancel: counted as white noise of its full
power it read the hunting 1.11-1.23x the kernel's; taken at its
low-frequency density, or on a repeating pattern's lines
(`cdr.linear.bb_data_noise`), 0.97-1.01x. And the time engine sets a
mixed-signal receiver's PAM4 levels once, at the pulse peak -- two samples
before its bang-bang loop locks on the PAM4 32G preset -- so the smear now
holds the thresholds there rather than at the lock: PAM4 32G 0.94-1.15x deep
and 1.04-1.12x from 100 errors; the cross-check links 1.00-1.04x. NRZ 16G's
0.45-0.89x is neither: its phase is correlated with the data pattern while
the loop runs free, ROADMAP P2 #32.)

The bang-bang figure on a quiet clock is limited by the limit cycle not being
white noise. On the cross-check link the two engines' BER agree to 1.3–1.5×
with the profile clock (invariant #3 asks for 2×).

**What it cannot say.** A bang-bang loop moves at most `kp` per update on the
half of updates that see a transition. A profile whose RMS phase *rate* inside
the loop bandwidth is a sizeable part of that makes the kernel slip cycles
(measured: BER 0.2–0.5, tracking error of several UI); the statistical engine
warns (`slew-limited`) from 0.3 of the limit, where the kernel was still locked,
and the kernel slipped from about 0.4. Since scoring realigns around whole-UI
slips (`extras["cycle_slips"]`), the BER figure there is the in-segment one: on
the test's fast profile at `kp_shift 8`, 16 slips in 194k symbols and 0.32 —
the loop spends long stretches sampling far off centre, not just one UI off.

The same-RMS comparison the feature exists for, as measured on the clean
16 GBd link at `kp_shift 6`: 0.11 UI of white phase noise leaves 0.119 UI on
the sampler (model 0.120); the same 0.11 UI as a 5 MHz-corner 1/f² profile
leaves 0.013 UI (model 0.015). `examples/31_pll_clock_profile.py` does the
PAM4 112 GBd version with three clocks and a `kp_shift` sweep.

**Measuring it yourself.** `analysis/cdr_tracking.py::cdr_tracking_error_s(cfg, res)`
returns the per-decision sampling error off a time run's `phase_track`
(skip the acquisition transient — the default skips the engine's settling
count; a slow ADC loop needs more). `StatResult.extras["clock_loop"]` carries
the model's `k_pd`, bandwidth, untracked and self-noise sigmas; the GUI's
Jitter tab draws L(f) with `20 log|1 − H|` over it and lists both numbers
beside the measurement.

## The receiver's own clock (phase 3)

A receiver samples with a clock of its own, usually a second PLL. `rx.clock`
is the same `ClockConfig` as `tx.clock` (profile file, white RJ, SJ; `dcd_ui`
is meaningless for a sampler and ignored) and defaults to ideal. Both
kernels take the per-symbol offsets it produces (`cdr/rx_clock.py`) as a new
argument, `rx_clock_offset_samples`, and add them to the loop phase before
every data and edge sample; `phase_track` reports where the sampler actually
fired. A CDR therefore tracks the *difference* between the two clocks, as a
real one does, and the statistical engine puts both profiles through the same
`|1 − H(f)|` (their untracked powers add; the receiver's white RJ rides
through the same response, while the transmitter's moves each edge on its
own and reaches the slicer as noise, not as a sampling shift -- see
`tx_edge_jitter` in `engine/statistical.py`, ROADMAP P2 #25). An all-zero offset array reproduces the pre-change kernels bit for
bit on both JIT paths — pinned by output hashes in `tests/test_rx_clock.py` and
by the engine fingerprint over every preset — and an ideal clock draws no
random numbers, so every older configuration is unchanged.

Two things measured while pinning this:

- **Independent clocks add in power only where the loop is linear.** On the
  Mueller-Muller loop the residual power above the ideal-clock floor with both
  clocks is the sum of the two alone (0.98–1.09 over seeds and gains, with
  broadband profiles so one realisation converges). On the bang-bang loop the
  detector gain falls with its *total* input jitter, so a second clock also
  narrows the loop: the two-clock residual is superadditive (1.24× the sum on
  the clean 16 GBd link) while the model, which carries that dependence,
  still lands within 10% of it.
- **A bang-bang detector drowned in broadband jitter loses lock**, and the
  slew check (wander inside the bandwidth) does not see it: 0.08 UI RMS of
  phase error at the detector stayed locked, 0.14 UI slipped. The statistical
  engine warns (`noise-limited`) from 0.1 UI.

### The live bridge

`io/pll_bridge.py` builds a `ClockProfile` straight from pll_simulator for the
desktop loop that does not want a file in between: `profile_from_analysis(ar)`
reads an `AnalysisResult` the way the exporter does (no import of pllsim at
all); `profile_from_preset(name)` builds the preset and analyses it on the
format's grid, importing `pllsim` inside the function — absent, it raises an
`ImportError` naming `pip install "halo-serdes[pll]"`. Against a sibling
checkout of pll_simulator the bridge reproduces the seven shipped files to
the last digit and its jitter over pllsim's own integration band equals
`ar.jitter_fs`. Nothing on the phone's compute path imports the module.

## Still open

Whether the two clocks share a reference (correlated low-frequency wander
the CDR need not follow) — both are independent here. Multi-phase clocks are
full-rate here: `f0_hz` gets the seconds right, the correlation between
phases is not modelled.
