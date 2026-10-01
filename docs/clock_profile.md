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

## Not in this phase

The statistical engine still smears the bathtub with `rj_ui` alone ("RJ only
for now"); the CDR-untracked residue ∫S_φ·|1−H_cdr|² that would let the two
engines cross-check a profile clock is phase 2. The receiver's own sampling
clock is phase 3.
