"""Optical tab — the topology's cascade, where the noise enters, how much each
level carries, reach over fibre length with and without a retimer, and the
transmitter's TDECQ."""

from __future__ import annotations

import dash_bootstrap_components as dbc
import numpy as np
from dash import html

from .. import figures, theme
from .. import studies
from ..runner import RunRecord
from . import common

TITLE = "Optical"
TAB_ID = "optical"


def _loss_fig(cm, f_nyq):
    """Insertion loss of the whole chain, of the part up to the photodiode, and
    of the O/E plus segment B."""
    f = cm.f / 1e9
    opt = cm.optical
    traces = [
        {"x": f, "y": cm.insertion_loss_db(), "name": "whole chain", "mode": "lines",
         "color": theme.PRIMARY},
        {"x": f, "y": opt.pre_pd.insertion_loss_db(), "name": "to the photodiode",
         "mode": "lines", "dash": "dot", "color": theme.MUTED},
        {"x": f, "y": opt.post_pd.insertion_loss_db(), "name": "O/E + segment B",
         "mode": "lines", "dash": "dash", "color": theme.ACCENT},
    ]
    fig = figures.lines_fig(traces, title="Cascade insertion loss (relative to the ideal chain)",
                            xtitle="frequency [GHz]", ytitle="dB", height=340)
    fig.add_vline(x=f_nyq / 1e9, line=dict(color=theme.GOOD, width=1, dash="dot"),
                  annotation_text="Nyquist")
    fig.update_yaxes(range=[-40, 3])
    return fig


def _sigma_fig(noise):
    """Noise sigma at the photodiode node against each level's optical power."""
    sig = noise.sigma_per_level() * 1e3
    p_dbm = 10 * np.log10(noise.level_powers_w * 1e3)
    traces = [{"x": p_dbm, "y": sig, "name": "per-sample sigma at the PD node",
               "mode": "lines+markers", "color": theme.PRIMARY}]
    return figures.lines_fig(traces, title="Noise follows the level's optical power",
                             xtitle="level power [dBm]", ytitle="sigma [mV / sample]", height=340)


def _tdecq_section(rec: RunRecord):
    """The transmitter as 802.3 would accept it: TDECQ after the fibre, the
    optical R_LM the L-I curve leaves, and where ER and bandwidth move it."""
    t = studies.tdecq_study(rec)
    if "error" in t:
        return dbc.Alert(t["error"], color="light", className="border")
    limit = 4.4 if rec.cfg.symbol_rate < 80e9 else 3.4
    tone = "good" if t["tdecq_db"] <= limit else "crit"
    cards = [
        theme.metric_card("TDECQ (after fibre)", f"{t['tdecq_db']:.2f} dB", tone,
                          f"802.3 limit {limit} dB (SR1 / DR1)"),
        theme.metric_card("Optical R_LM", f"{t['rlm']:.3f}", "info",
                          f"L-I curve alone {t['curve_rlm']:.3f}"),
        theme.metric_card("Equaliser noise gain C_eq", f"{t['ceq']:.2f}", "info",
                          f"OMA_outer {t['oma_dbm']:+.2f} dBm"),
    ]
    hl = [{"y": limit, "text": f"limit {limit} dB", "color": theme.GOOD}]
    er = figures.lines_fig([{"x": t["er_db"], "y": t["tdecq_er"], "name": "TDECQ",
                             "mode": "lines+markers", "color": theme.PRIMARY}],
                           title="TDECQ vs extinction ratio", xtitle="ER [dB]",
                           ytitle="TDECQ [dB]", height=300, hlines=hl)
    bw = figures.lines_fig([{"x": t["bw_ghz"], "y": t["tdecq_bw"], "name": "TDECQ",
                             "mode": "lines+markers", "color": theme.ACCENT}],
                           title="TDECQ vs laser bandwidth", xtitle="f_r / 3 dB BW [GHz]",
                           ytitle="TDECQ [dB]", height=300, hlines=hl)
    return html.Div([
        theme.section_title("Transmitter: TDECQ (802.3 reference receiver and equaliser)"),
        common.cards_row(cards),
        dbc.Row([dbc.Col(common.graph(er), lg=6), dbc.Col(common.graph(bw), lg=6)],
                className="g-2"),
    ])


def render(rec: RunRecord):
    """The optical topology: chain loss, optical power, level-dependent noise,
    TDECQ (PAM4) and reach over fibre length with and without a retimer."""
    if rec is None:
        return common.need_run_message()
    cfg = rec.cfg
    if cfg.topology is None:
        return dbc.Alert("This link is electrical. Set topology → Optics to vcsel_mmf or "
                         "eml_smf (segments A / B, laser, fibre, PD + TIA) and Run.",
                         color="light", className="border")
    try:
        from halo_serdes.channel import ChannelModel
        from halo_serdes.optical import fiber as fiber_mod
        cm = ChannelModel.from_config(cfg)
    except Exception as exc:
        return dbc.Alert(f"optical chain unavailable: {exc}", color="warning",
                         className="border")
    opt = cfg.topology.optical
    noise = cm.optical.noise
    sig = noise.sigma_per_level() * 1e3
    cards = [
        theme.metric_card("Chain loss @ Nyquist", f"{cm.loss_at(cfg.f_nyquist):.1f} dB", "info",
                          f"{cfg.f_nyquist / 1e9:.1f} GHz, vs ideal 0.25 chain"),
        theme.metric_card("Optical power", f"OMA {opt.oma_dbm:+.1f} dBm", "info",
                          f"ER {opt.er_db:.1f} dB · P_low {10 * np.log10(opt.p_low_w * 1e3):+.1f} dBm"),
        theme.metric_card("PD-node sigma, bottom → top",
                          f"{sig[0]:.2f} → {sig[-1]:.2f} mV", "info",
                          f"×{sig[-1] / sig[0]:.2f} across the levels"),
    ]
    if opt.kind == "vcsel_mmf":
        bw = fiber_mod.modal_bandwidth_hz(opt.modal_bw_mhz_km, opt.length_m)
        cards.append(theme.metric_card("Fibre −3 dBo", f"{bw / 1e9:.1f} GHz", "info",
                                       f"OM EMB {opt.modal_bw_mhz_km:.0f} MHz·km / {opt.length_m:.0f} m"))
    else:
        ff = fiber_mod.first_fade_hz(opt.dispersion_ps_nm_km or 0.0, opt.length_m, opt.wavelength_nm)
        cards.append(theme.metric_card("First dispersion fade",
                                       f"{ff / 1e9:.0f} GHz" if np.isfinite(ff) else "none",
                                       "info", f"D {opt.dispersion_ps_nm_km} ps/(nm·km), {opt.length_m:.0f} m"))
    blocks = [common.warnings_block(rec), common.cards_row(cards),
              dbc.Row([dbc.Col(common.graph(_loss_fig(cm, cfg.f_nyquist)), lg=7),
                       dbc.Col(common.graph(_sigma_fig(noise)), lg=5)], className="g-2")]
    if cfg.modulation == "pam4":
        blocks.append(_tdecq_section(rec))

    r = studies.optical_study(rec)
    if "error" in r:
        blocks.append(dbc.Alert(r["error"], color="light", className="border"))
    else:
        traces = [
            {"x": r["length_m"], "y": np.maximum(r["kp4"], 1e-30),
             "name": f"as configured (retimer: {r['retimer']})", "mode": "lines+markers",
             "color": theme.PRIMARY},
            {"x": r["length_m"], "y": np.maximum(r["kp4_retimed"], 1e-30),
             "name": "DSP retimed (both ends)", "mode": "lines+markers", "color": theme.ACCENT},
        ]
        fig = figures.lines_fig(traces, title="Reach over fibre length (statistical, post-KP4)",
                                xtitle="fibre length [m]", ytitle="post-KP4 BER", logy=True,
                                height=400, hlines=[{"y": 1e-15, "text": "1e-15 target",
                                                     "color": theme.GOOD}])
        fig.update_yaxes(range=[-30, 0])
        rc, rr = r["reach_m"], r["reach_m_retimed"]
        blocks += [
            theme.section_title("LPO / CPO vs retimed: same optics, the segments either side"),
            common.cards_row([
                theme.metric_card("Reach as configured", f"{rc:.0f} m" if rc else "> swept range",
                                  "info", "post-KP4 < 1e-15"),
                theme.metric_card("Reach retimed", f"{rr:.0f} m" if rr else "> swept range",
                                  "good" if (rr or 0) >= (rc or 0) else "warn",
                                  "retimer RX/TX from topology.retimer_*"),
            ]),
            common.graph(fig),
            html.Div("Unretimed, the host receiver equalises traces and optics together; "
                     "retimed, the optics carry only their own ISI and each segment is scored "
                     "on its own, combined as 1 − ∏(1 − pᵢ). Time-engine ladders and the "
                     "lever table are in examples 32 and 33.",
                     style={"fontSize": "0.75rem", "color": "#5b6472"}),
        ]
    return html.Div(blocks)
