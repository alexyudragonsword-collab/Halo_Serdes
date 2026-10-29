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

The page is laid out as the link it simulates.

**Top bar**

- **Preset**: the bundled `configs/*.yaml` plus library defaults. Choosing one
  loads it into the form.
- **Mixed-signal / ADC + DSP**: the receiver architecture (`rx.arch`).
- **Derived chips**: live UI / dt / Nyquist / data rate / bits per symbol, and
  under them the **mixed-signal envelope banner** (comfort / limit / hard
  ceiling, from the `MS_*` constants).
- **▶ Run**: runs the engine(s) selected by `sim.engine` (`time` / `stat` /
  `both`) on a background job and fills every view from the one result.
  While it runs: the stage, the elapsed time and a progress bar (the receiver
  loop's fraction; a moving stripe outside it), and **■ Cancel**. A cancel
  takes effect when the current stage ends: the time engine's front end
  (transmitter waveform, channel filtering) has no cancel point inside it,
  about 10 s per million symbols on a desktop (a 3 M-symbol NRZ 28G run
  stopped 47 s after the click). The page stays usable during a run.

**Left: the link** — a chain of stages, each card saying what it is set to
now (`FIR 3 taps · 1 V`, `RLGC 0.28 m`, `CTLE 7 dB · noise 0.004 V`, `FFE 4+12 ·
DFE 2`, ...): Link & run, Transmitter, Channel (with crosstalk and the optical
topology), Receiver front end, ADC (on an `adc_dsp` receiver only),
Equaliser (FFE, DFE, MLSD, partial response), CDR. Click a stage to open its
parameters below the chain: the common ones first, the rest behind
**Advanced (n)**. Only what the configuration uses is shown (`field_meta.py`):
the RLGC line or the Touchstone file by `channel.kind`, the optical fields when
`topology.optical.kind` is not `none`, the parameter of the chosen driver
nonlinearity, and so on. Hidden fields keep their values. Labels with a dotted
underline have hover help. Frequencies show in GHz/GBd; optional fields accept
blank = "unset". **YAML** exports the form or imports a config.

**Right: four workspaces.** A run is cached server-side; switching views
re-renders from it instantly. Above them:

- the **Runs** strip: each run of this session as `#n preset · k changed ·
  BER`, the one on screen highlighted. Click one to put it back on screen;
  ☆ pins it (pinned runs stay in the strip and are never evicted from the
  server's result registry, which otherwise keeps the last 24); **↺ Load this
  run's config** puts the shown run's configuration back into the form.
  Eight unpinned runs are kept.
- a banner when the form has changed since the run on screen: the fields, or
  for a run of another configuration how many differ;
- the run's warnings, once. The mixed-signal envelope warning is left to the
  top bar; the solvers' numerical warnings (an overflow inside a fit, a
  covariance that could not be estimated) fold into one collapsed line.

Below 992 px the results come first and the link after them.

## Workspaces and views

| Workspace | View | Shows | Notes |
|---|---|---|---|
| **Verdict** | — | pass / fail against the FEC threshold (KR4 for NRZ, KP4 for PAM; each engine's call, margin), StatEye BER and the time / StatEye ratio, the phase bathtub, BER / SER / SNR cards (TJ@1e-12 at the CTLE output on a mixed-signal receiver), eye (ADC: the reconstructed FFE-output eye), slicer histogram, adapted taps | the verdict fills in once StatEye has run |
| **Diagnose** | Eyes | mixed-signal: front-end + post-EQ eye. ADC/DSP: closed ADC-input eye + reconstructed post-FFE eye (upsampled baud taps ⊗ analog waveform) + slicer sample cloud (DFE output) | arch-aware |
| | Dual-engine | statistical eye, phase bathtub (+MC point), slicer PDF stat-vs-MC | computes StatEye on demand |
| | Tx training | KR-style Tx-FIR training trajectories | runs `train_tx_fir` |
| | Channel | insertion loss, impulse, pulse + ISI cursors, behavioral COM | from `LinkConfig` |
| | CTLE | CTLE Bode + realized peaking | |
| | ADC | per-lane SER, code histogram, TI mismatch, converged FFE/DFE | `adc_dsp` only |
| | Equaliser | DFE tap trajectories + convergence learning curve | enable `dfe.adapt` |
| | CDR | recovered-phase acquisition + PD activity | |
| | Jitter | per-stage jitter budget; clock profile vs CDR | repeating pattern |
| **Studies** | Sweeps / Reach | statistical reach vs channel loss + KP4/KR4 | analytic channel |
| | FEC | pre→post-FEC projection (KP4/KR4/concatenated), this-run marker | formulas |
| | Crosstalk | FEXT/NEXT coupling sweep (StatEye) + multi-lane ICN (~√N) & 802.3 COM vs aggressor count | synthetic aggressors / aggressor_bank |
| | JTOL | tolerated sinusoidal jitter vs frequency + generic mask | short time-domain runs |
| | AMI / COM | IEEE 802.3 COM (93A/178A) vs loss overlaid on the RSS FoM + IBIS-AMI status | analytic channel |
| | Fixed-Point | word-length BER wall (datapath replay) | needs an ADC run |
| | Optical | cascade loss split at the photodiode, per-level PD-node noise, reach over fibre length as configured vs DSP-retimed, TDECQ | `topology.optical` set |
| **Compare** | — | pick two or more runs: StatEye bathtubs on one axis, a results table, the configuration fields that differ; **⇄ Run the shown run on the other architecture** runs it with `rx.arch` flipped and puts the two side by side | runs of this session |

In Diagnose, clicking a stage on the left shows that stage's view (Channel →
Channel, Receiver front end → CTLE, Equaliser → Equaliser, ...). Studies take
from about a second to about 20 s, so each shows what it computes and a
**▶ Run** button; the result is kept per run.

## Performance notes


- Engines are numba-jitted (first call pays a one-time compile) and run on
  the background job runner the phone app also uses
  (`halo_serdes_app.api.start_job`); the page polls it every 400 ms.
- Studies use the analytic StatEye engine and the fixed-point datapath
  *replay* (no engine re-run), but a reach sweep is still ~20 s on the 28G
  preset; they run on their own button (above).
- Verdict computes StatEye after the time-domain cards are on screen
  (0.1–3.4 s on the presets), for the verdict.
- For heavy time-domain runs, lower `sim.n_symbols` while exploring, then
  raise it for a final BER estimate.

## Architecture

```
src/halo_serdes_gui/
  app.py            shell: top bar, link chain, workspaces, callbacks
  config_bridge.py  form <-> LinkConfig <-> YAML, presets, envelope
  config_form.py    schema-driven form (pattern-matching ids)
  history.py        the Runs strip: entries, labels, pins
  field_meta.py     stages, common fields, hover help and show-when rules for
                    the form (desktop only;
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
