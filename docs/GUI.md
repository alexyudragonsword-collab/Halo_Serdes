# Halo_Serdes GUI

A professional [Plotly Dash](https://dash.plotly.com/) studio that drives the
live behavioral engines — every analysis the 25 example scripts demonstrate,
made interactive. The GUI adds **no** simulation logic: it edits a
`LinkConfig`, calls the existing engines, and renders `SimResult` /
`StatResult` with Plotly.

## Install & launch

```bash
pip install -e ".[gui,jit,fec]"     # gui deps + numba speed + galois FEC
halo-serdes-gui                      # or: python -m halo_serdes_gui
```

Then open <http://127.0.0.1:8050/>. Options: `--host`, `--port`, `--debug`.

A screenshot-by-screenshot walkthrough of the tabs is in
[`GUI_tour.md`](GUI_tour.md).

Bootstrap (Flatly) is vendored under `src/halo_serdes_gui/assets/`, so the app
is fully self-contained — no network/CDN needed. `galois` (real RS codec) and
`pyibisami` (vendor IBIS-AMI models) are optional; without them the FEC
projection formulas and the native reference AMI model still work.

## Layout

**Left sidebar** — the configuration surface:

- **Presets**: the bundled `configs/*.yaml` plus library defaults. Choosing one
  loads it into the form.
- **▶ Run**: runs the engine(s) selected by `sim.engine` (`time` / `stat` /
  `both`) on a background job and populates every tab from the one result.
  While it runs: the stage, the elapsed time and a progress bar (the receiver
  loop's fraction; a moving stripe outside it), and **■ Cancel**. A cancel
  takes effect when the current stage ends: the time engine's front end
  (transmitter waveform, channel filtering) has no cancel point inside it,
  about 10 s per million symbols on a desktop (a 3 M-symbol NRZ 28G run
  stopped 47 s after the click). The page stays usable during a run.
- **Derived chips**: live UI / dt / Nyquist / data-rate, and a **mixed-signal
  envelope banner** (comfort / limit / hard-ceiling) computed from the
  `MS_*` constants.
- **YAML**: export the current form to YAML or import a YAML config.
- **Configuration accordion**: the `LinkConfig` fields, grouped
  (Link / Channel / Tx / Rx → CTLE, VGA, ADC, FFE, DFE, CDR / Sim / Numeric).
  Only what the configuration uses is shown (`field_meta.py`): the ADC and
  partial-response groups on an `adc_dsp` receiver, the RLGC line or the
  Touchstone file by `channel.kind`, the optical fields when
  `topology.optical.kind` is not `none`, the parameter of the chosen driver
  nonlinearity, and so on. Hidden fields keep their values. Labels with a
  dotted underline have hover help. Frequencies show in GHz/GBd; optional
  fields accept blank = "unset".

**Main area** — capability tabs. A run is cached server-side; switching tabs
re-renders from it instantly. Above the tabs:

- the **Runs** strip: each run of this session as `#n preset · k changed ·
  BER`, the one on screen highlighted. Click one to put it back on screen;
  ☆ pins it (pinned runs stay in the strip and are never evicted from the
  server's result registry, which otherwise keeps the last 24); **↺ Load this
  run's config** puts the shown run's configuration back into the form.
  Eight unpinned runs are kept.
- a banner when the form has changed since the run on screen, naming the
  fields;
- the run's warnings, once (not on every tab). The mixed-signal envelope
  warning is left to the sidebar banner; the solvers' numerical warnings
  (an overflow inside a fit, a covariance that could not be estimated) fold
  into one collapsed line.

Study tabs (Sweeps / Reach, Crosstalk, JTOL, AMI / COM, Fixed-Point,
Optical) take from about a second to about 20 s, so they show what they
compute and a **▶ Run** button instead of computing on tab switch; the result
is kept per run, so returning to the tab does not recompute it.

## Tabs

| Tab | Shows | Notes |
|---|---|---|
| **Single Run** | verdict vs the FEC threshold (KR4 for NRZ, KP4 for PAM; each engine's call), StatEye BER and the time / StatEye ratio, BER/SER/SNR/TJ cards, eye, slicer histogram, adapted taps | any run; the verdict fills in once StatEye has run |
| **Eyes** | mixed-signal: front-end + post-EQ eye. ADC/DSP: closed ADC-input eye + reconstructed post-FFE eye (upsampled baud taps ⊗ analog waveform) + slicer sample cloud (DFE output) | arch-aware |
| **Dual-Engine** | statistical eye, phase bathtub (+MC point), slicer PDF stat-vs-MC | computes StatEye on demand |
| **Channel** | insertion loss, impulse, pulse + ISI cursors, behavioral COM | from `LinkConfig` |
| **CTLE** | CTLE Bode + realized peaking | |
| **Adaptation** | DFE tap trajectories + convergence learning curve | enable `dfe.adapt` |
| **CDR** | recovered-phase acquisition + PD activity | |
| **ADC** | per-lane SER, code histogram, TI mismatch, converged FFE/DFE | `rx.arch = adc_dsp` |
| **Backchannel** | KR-style Tx-FIR training trajectories | runs `train_tx_fir` |
| **Sweeps / Reach** | statistical reach vs channel loss + KP4/KR4 | analytic channel |
| **FEC** | pre→post-FEC projection (KP4/KR4/concatenated), this-run marker | formulas |
| **Crosstalk** | FEXT/NEXT coupling sweep (StatEye) + multi-lane ICN (~√N) & 802.3 COM vs aggressor count | synthetic aggressors / aggressor_bank |
| **AMI / COM** | IEEE 802.3 COM (93A/178A) vs loss overlaid on the RSS FoM + IBIS-AMI status (compiled reference model runs over the real C ABI; pyibisami for vendor models) | analytic channel |
| **Fixed-Point** | word-length BER wall (datapath replay) | needs an ADC run |
| **Optical** | the topology's cascade loss split at the photodiode, per-level PD-node noise, reach over fibre length as configured vs DSP-retimed (statistical cascade) | `topology.optical` set; retimer RX/TX from `topology.retimer_*` |

## Performance notes


- Engines are numba-jitted (first call pays a one-time compile) and run on
  the background job runner the phone app also uses
  (`halo_serdes_app.api.start_job`); the page polls it every 400 ms.
- Study tabs use the analytic StatEye engine and the fixed-point datapath
  *replay* (no engine re-run), but a reach sweep is still ~20 s on the 28G
  preset; they run on their own button (above).
- Single Run computes StatEye after the time-domain cards are on screen
  (0.1–3.4 s on the presets), for the verdict.
- For heavy time-domain runs, lower `sim.n_symbols` while exploring, then
  raise it for a final BER estimate.

## Architecture

```
src/halo_serdes_gui/
  app.py            shell + tabs + callbacks
  config_bridge.py  form <-> LinkConfig <-> YAML, presets, envelope
  config_form.py    schema-driven form (pattern-matching ids)
  history.py        the Runs strip: entries, labels, pins
  field_meta.py     hover help and show-when rules for the form (desktop only;
                    phase 3 of the GUI plan moves them into the shared schema)
  runner.py         engine execution + in-process result registry
  studies.py        cached sweep/projection computes
  figures.py        Plotly builders from result objects
  theme.py          palette + shared components
  panels/           one module per tab
  assets/           vendored Bootstrap + polish CSS
```

Intermediate-node eye reconstruction lives in
`halo_serdes.analysis.reconstruct` so the example scripts and the GUI share
one implementation.

## Desktop app / Windows executable

A native-window build (pywebview, no-JIT) can be packaged into a Windows `.exe`
with PyInstaller or Nuitka — see [`../packaging/README.md`](../packaging/README.md).
Locally:

```bash
pip install -e ".[gui,desktop]"
halo-serdes-gui-desktop          # native window; --browser for a tab
```
