"""GUI figure builders + runner: valid figures from canned data, run end-to-end."""

import numpy as np
import pytest

pytest.importorskip("plotly")
import plotly.graph_objects as go  # noqa: E402

from halo_serdes_gui import figures  # noqa: E402


def test_placeholder_and_empty_inputs_return_figures():
    assert isinstance(figures.placeholder("x"), go.Figure)
    assert isinstance(figures.eye_fig(None), go.Figure)
    assert isinstance(figures.slicer_hist_fig(None), go.Figure)
    assert isinstance(figures.taps_fig(None, None), go.Figure)


def test_eye_fig_from_traces():
    rng = np.random.default_rng(0)
    traces = rng.normal(size=(500, 32))
    fig = figures.eye_fig(traces, title="Eye")
    assert isinstance(fig, go.Figure)
    assert fig.data and fig.data[0].type == "heatmap"


def test_taps_fig_both_and_single():
    fig2 = figures.taps_fig(np.arange(15) / 15, np.array([0.4, 0.2, 0.1]), ffe_pre=4)
    assert len([t for t in fig2.data if t.type == "bar"]) == 2
    fig1 = figures.taps_fig(None, np.array([0.4, 0.2]))
    assert len([t for t in fig1.data if t.type == "bar"]) == 1


def test_lines_fig_logy():
    fig = figures.lines_fig([{"x": [1, 2, 3], "y": [1e-3, 1e-6, 1e-9], "name": "a"}],
                            logy=True, ytitle="BER")
    assert fig.layout.yaxis.type == "log"


def test_g1_figure_builders_from_result():
    """stat/channel/ctle/jitter builders return figures from a real run."""
    import dataclasses

    from halo_serdes.channel import ChannelModel
    from halo_serdes_gui import config_bridge as cb
    from halo_serdes_gui import runner

    cfg = cb.load_preset("NRZ 16G mixed-signal")
    cfg = dataclasses.replace(cfg, sim=dataclasses.replace(
        cfg.sim, n_symbols=127 * 12, pattern="prbs7", engine="both"))
    rec = runner.run_link(cfg, collect_eye=True, collect_jitter=True)
    assert rec.ok and rec.stat is not None

    assert figures.stat_eye_fig(rec.stat).data[0].type == "heatmap"
    assert isinstance(figures.bathtub_fig(rec.stat, mc_ber=rec.sim.ber.ber), go.Figure)
    assert isinstance(figures.slicer_pdf_compare_fig(rec.stat, rec.sim.y_slicer),
                      go.Figure)
    cm = ChannelModel.from_config(cfg)
    assert isinstance(figures.channel_loss_fig(cm, cfg.f_nyquist), go.Figure)
    assert isinstance(figures.pulse_cursors_fig(cm, cfg.dt, cfg.osr), go.Figure)
    assert isinstance(figures.ctle_bode_fig(cfg), go.Figure)
    jb = rec.sim.extras.get("jitter_budget")
    assert isinstance(figures.jitter_bar_fig(jb, cfg.ui), go.Figure)


def test_runner_end_to_end_and_error_capture():
    from halo_serdes_gui import runner

    vals = {
        "modulation": "nrz", "symbol_rate": 16.0, "osr": 16,
        "channel.kind": "analytic", "channel.length_m": 0.2,
        "rx.arch": "mixed_signal", "rx.noise_rms": 0.003,
        "rx.dfe.n_taps": 2, "sim.n_symbols": 2000, "sim.pattern": "prbs7",
        "sim.engine": "time",
    }
    rec = runner.run_from_values(vals, collect_eye=True)
    assert rec.ok, rec.error
    assert rec.sim is not None and rec.sim.eye_data is not None
    assert runner.get(rec.id) is rec

    # touchstone kind with no file -> error captured, not raised
    bad = runner.run_from_values({**vals, "channel.kind": "touchstone",
                                  "channel.file": ""})
    assert not bad.ok and "error" in (bad.error or "").lower() or bad.error
