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

A screenshot-by-screenshot walkthrough of all 15 tabs is in
[`GUI_tour.md`](GUI_tour.md).

Bootstrap (Flatly) is vendored under `src/halo_serdes_gui/assets/`, so the app
is fully self-contained — no network/CDN needed. `galois` (real RS codec) and
`pyibisami` (vendor IBIS-AMI models) are optional; without them the FEC
projection formulas and the native reference AMI model still work.

## Layout

**Left sidebar** — the configuration surface:

- **Presets**: the four `configs/*.yaml` plus library defaults. Pick and *Load*.
- **▶ Run**: runs the engine(s) selected by `sim.engine` (`time` / `stat` /
  `both`) and populates every tab from the one result.
- **Derived chips**: live UI / dt / Nyquist / data-rate, and a **mixed-signal
  envelope banner** (comfort / limit / hard-ceiling) computed from the
  `MS_*` constants.
- **YAML**: export the current form to YAML or import a YAML config.
- **Configuration accordion**: every `LinkConfig` field, grouped
  (Link / Channel / Tx / Rx → CTLE, VGA, ADC, FFE, DFE, CDR / Sim / Numeric).
  Frequencies show in GHz/GBd; optional fields accept blank = "unset".

**Main area** — capability tabs. A run is cached server-side; switching tabs
re-renders from it instantly (study sweeps are cached per run).

## Tabs

| Tab | Shows | Notes |
|---|---|---|
| **Single Run** | BER/SER/SNR/TJ cards, eye, slicer histogram, adapted taps | any run |
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
| **Crosstalk** | FEXT/NEXT coupling sweep (StatEye) | synthetic aggressors |
| **AMI / COM** | behavioral COM vs loss + IBIS-AMI seam status | analytic channel |
| **Fixed-Point** | word-length BER wall (datapath replay) | needs an ADC run |

## Performance notes

- Engines are blocking + numba-jitted (first call pays a one-time compile).
  The main area shows a spinner while a run is in flight.
- Interactive sweeps use the fast analytic StatEye engine and the fixed-point
  datapath *replay* (no engine re-run), so study tabs are responsive.
- For heavy time-domain runs, lower `sim.n_symbols` while exploring, then
  raise it for a final BER estimate.

## Architecture

```
src/halo_serdes_gui/
  app.py            shell + tabs + callbacks
  config_bridge.py  form <-> LinkConfig <-> YAML, presets, envelope
  config_form.py    schema-driven form (pattern-matching ids)
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
