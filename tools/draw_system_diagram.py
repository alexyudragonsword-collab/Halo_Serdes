"""Draw the 32G NRZ system diagram from a run of example 03.

``docs/figures/nrz32_system_diagram.png`` says "参数为实际仿真值" (the numbers
are the simulator's), but it was drawn once by hand and never redrawn: by
2026-10 it still showed the slicer SNR and levels of 2026-08. This runs
``examples/03_eq_eye_nrz32.py`` -- the link the figure describes -- takes
every number on the figure from that run (config and results alike), and
writes the PNG and the Mermaid source ``docs/nrz32_system_diagram.mmd`` from
the same values, so the two cannot disagree with each other or with the
example. ``docs/summary.html`` carries its own base64 copy of the PNG (the page
is self-contained); that copy is refreshed too, at the 1500 px width it had.

    python tools/draw_system_diagram.py            # ~15 s: runs example 03
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
PNG = REPO / "docs" / "figures" / "nrz32_system_diagram.png"
MMD = REPO / "docs" / "nrz32_system_diagram.mmd"
SUMMARY_HTML = REPO / "docs" / "summary.html"
#: how summary.html captions the embedded copy (the image just before it)
SUMMARY_CAPTION = "32G NRZ 链路系统框图"

plt.rcParams["font.sans-serif"] = ["WenQuanYi Zen Hei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

TX, RX, MEAS = "#3a6ab4", "#b8691d", "#5b6472"
FILL_TX, FILL_RX = "#e9f0fb", "#fdf1e3"


def _pow2(shift: int) -> str:
    return f"1/{2 ** int(shift)}"


def _channel_name(cfg) -> str:
    """'TEC_Whisper42p8in_Meg6_THRU_C8C9.s4p' -> 'Whisper 42.8"'."""
    import re

    if not cfg.channel.file:
        return cfg.channel.kind
    stem = Path(cfg.channel.file).stem
    m = re.search(r"([A-Za-z]+)(\d+)p(\d+)in", stem)
    return f'{m.group(1)} {m.group(2)}.{m.group(3)}"' if m else stem


def values_from_run(g: dict) -> dict:
    """Every label on the figure, from example 03's globals (``cfg``, ``res``,
    ``channel``, ``eye_h``) -- nothing typed in by hand."""
    from halo_serdes.afe import Ctle

    cfg, res = g["cfg"], g["res"]
    ctle = Ctle.from_config(cfg.rx.ctle, cfg.f_nyquist) if cfg.rx.ctle.enable else None
    ffe, dfe, cdr = cfg.rx.ffe, cfg.rx.dfe, cfg.rx.cdr
    taps = ", ".join(f"{t:g}" for t in cfg.tx.fir_taps)
    return {
        "rate": f"{cfg.data_rate / 1e9:.0f}G",
        "pattern": cfg.sim.pattern.upper(),
        "n_sym": f"{cfg.sim.n_symbols / 1e3:.0f}k",
        "fir": f"[{taps}]",
        "swing": f"{cfg.tx.swing:.1f} Vpp",
        "channel": _channel_name(cfg),
        "loss": f"{g['channel'].loss_at(cfg.f_nyquist):.1f} dB @ {cfg.f_nyquist / 1e9:.0f} GHz",
        "ctle": (f"峰值 {cfg.rx.ctle.peak_db:g} dB(实际 {ctle.peaking_db():.1f})"
                 if ctle is not None else "关闭"),
        "noise": f"+{cfg.rx.noise_rms * 1e3:g} mV 噪声",
        "osr": f"OSR{cfg.osr}→波特率",
        "ffe": f"{ffe.n_pre + 1 + ffe.n_post} taps({ffe.n_pre} pre + 1 + {ffe.n_post} post)",
        "ffe_short": f"{ffe.n_pre + 1 + ffe.n_post} taps\n{ffe.n_pre} pre + 1 + {ffe.n_post} post",
        "slicer": f"±{abs(res.extras['main_cursor']) * 1e3:.1f} mV",
        "dfe": f"{dfe.n_taps} taps",
        "dfe_adapt": dfe.adapt.replace("_", "-"),
        "cdr": f"Kp={_pow2(cdr.kp_shift)}  Ki={_pow2(cdr.ki_shift)}",
        "cdr_kind": {"bang_bang": "BB-CDR", "mueller_muller": "MM-CDR"}.get(cdr.kind, cdr.kind),
        "pd": {"bang_bang": "Alexander PD", "mueller_muller": "Mueller-Muller PD"}.get(cdr.kind, ""),
        "ber": f"BER = {res.ber.n_errors} / {res.ber.n_checked:,}",
        "snr": f"slicer SNR {res.slicer_snr_db:.1f} dB",
        "eye": f"内眼 {g['eye_h'] * 1e3:.1f} mV",
    }


def _box(ax, x, y, w, h, title, body, edge, fill, text="#1c2430"):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.01,rounding_size=0.12",
                                lw=2.2, ec=edge, fc=fill))
    ax.text(x + w / 2, y + h * 0.72, title, ha="center", va="center", fontsize=15, color=text)
    ax.text(x + w / 2, y + h * 0.36, body, ha="center", va="center", fontsize=10, color=text,
            linespacing=1.35)


def _arrow(ax, a, b, color="#333333", style="-|>", ls="-", rad=0.0, lw=1.8):
    ax.add_patch(FancyArrowPatch(a, b, arrowstyle=style, mutation_scale=16, color=color, lw=lw,
                                 linestyle=ls, connectionstyle=f"arc3,rad={rad}"))


def draw(v: dict, path: Path) -> None:
    """The figure: Tx row, channel, Rx row, and the loops / checks below."""
    fig, ax = plt.subplots(figsize=(18.9, 8.15), dpi=100)
    ax.set_xlim(0, 18.9)
    ax.set_ylim(0, 8.15)
    ax.axis("off")
    ax.text(9.45, 7.75, f"{v['rate']} NRZ 链路仿真系统框图(halo_serdes,参数为实际仿真值,"
                        "由 tools/draw_system_diagram.py 从示例 03 的运行生成)",
            ha="center", fontsize=15)
    ax.text(4.05, 7.15, "发射机 Tx", ha="center", fontsize=15, color=TX)
    ax.text(15.3, 7.15, "接收机 Rx(mixed-signal)", ha="center", fontsize=15, color=RX)

    w, h, y = 1.85, 1.85, 4.75
    tx = [("PRBS", f"{v['pattern']} 码型\n{v['n_sym']} 符号"),
          ("Tx FIR", f"去加重\n{v['fir']}"),
          ("驱动器", f"ZOH, {v['swing']}\n(可注入 RJ/SJ/DCD)")]
    xs = [0.3, 2.4, 4.7]
    for x, (t, b) in zip(xs, tx):
        _box(ax, x, y, w if t != "驱动器" else 2.2, h, t, b, TX, FILL_TX)
    _box(ax, 7.25, y - 0.15, 2.7, h + 0.3, "信道", f"{v['channel']} 背板 s4p\n混模 + 端接\n{v['loss']}",
         "#2a3a55", "#2a3a55", text="white")
    rx = [(10.3, "CTLE", f"1 零 2 极\n{v['ctle']}\n{v['noise']}"),
          (12.4, "采样器", f"Farrow 插值\n{v['osr']}"),
          (14.5, "FFE", f"{v['ffe_short']}\nMMSE"),
          (17.1, "判决", v["slicer"])]
    for x, t, b in rx:
        _box(ax, x, y, w if t != "判决" else 1.6, h, t, b, RX, FILL_RX)
    ax.add_patch(Circle((16.75, y + h / 2), 0.25, fc="white", ec=RX, lw=2))
    ax.text(16.75, y + h / 2, "Σ", ha="center", va="center", fontsize=16, color=RX)

    mid = y + h / 2
    for a, b in ((2.15, 2.4), (4.25, 4.7), (6.9, 7.25), (9.95, 10.3), (12.15, 12.4),
                 (14.25, 14.5), (16.35, 16.5), (17.0, 17.1)):
        _arrow(ax, (a, mid), (b, mid))

    yb, hb = 1.2, 1.6
    _box(ax, 8.6, yb, 3.0, hb, "PRBS 检错", f"{v['ber']}\n{v['snr']} · {v['eye']}",
         "#1f4d2a", "#1f4d2a", text="white")
    _box(ax, 12.6, yb, 2.6, hb, v["cdr_kind"], f"{v['pd']}\n{v['cdr']}", "#4a3b20", "#4a3b20",
         text="white")
    _box(ax, 15.9, yb, 2.6, hb, f"DFE {v['dfe']}", f"{v['dfe_adapt']} LMS\n反馈 = -sum(w * v_hat)", RX, FILL_RX)

    _arrow(ax, (17.4, y), (11.2, yb + hb), color="#1f4d2a", rad=0.25)           # bits to the checker
    ax.text(9.9, 3.75, "恢复比特", color="#1f4d2a", fontsize=11)
    _arrow(ax, (13.4, yb + hb), (13.1, y), color="#8a6d2c", ls="--", rad=-0.15)   # phase to the sampler
    ax.text(12.4, 3.4, "相位 φ[k]", color="#8a6d2c", fontsize=11)
    _arrow(ax, (15.3, y), (14.2, yb + hb), color="#8a6d2c", ls="--", rad=0.15)    # data / edge samples
    ax.text(14.6, 3.6, "数据/边沿采样", color="#8a6d2c", fontsize=11)
    _arrow(ax, (17.9, y), (17.9, yb + hb), color=RX)                              # decision history
    ax.text(18.05, 3.75, "判决历史", color=RX, fontsize=11, rotation=90)
    _arrow(ax, (16.9, yb + hb), (16.75, mid - 0.25), color=RX, rad=-0.1)          # feedback
    ax.text(15.95, 3.7, "DFE 反馈", color=RX, fontsize=11)

    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=100, facecolor="white", bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)


def write_mmd(v: dict, path: Path) -> None:
    """The same diagram as Mermaid source, from the same values."""
    path.write_text(f"""%% generated by tools/draw_system_diagram.py from example 03 -- do not edit by hand
flowchart LR
    subgraph TX["发射机 Tx"]
        PRBS["{v['pattern']} 码型<br/>{v['n_sym']} 符号"] --> FIR["Tx FIR 去加重<br/>{v['fir']}"]
        FIR --> DRV["驱动器 ZOH<br/>摆幅 {v['swing']}<br/>(可选 RJ/SJ/DCD 抖动)"]
    end

    subgraph CH["信道 Channel"]
        S4P["{v['channel']} 背板 s4p<br/>混模转换 + 端接<br/><b>{v['loss']}</b>"]
    end

    subgraph RX["接收机 Rx (mixed-signal)"]
        CTLE["CTLE<br/>1 零 2 极<br/>{v['ctle']}"] --> N["{v['noise']}"]
        N --> SAMP["采样器<br/>Farrow 插值<br/>{v['osr']}"]
        SAMP --> FFE["FFE {v['ffe']}<br/>MMSE"]
        FFE --> SUM(("Σ"))
        SUM --> SLC["判决器<br/>{v['slicer']}"]
        SLC --> DFE["DFE {v['dfe']}<br/>({v['dfe_adapt']} LMS)"]
        DFE -->|"反馈 −Σwᵢ·v̂ₖ₋ᵢ"| SUM
        SLC -.->|"数据/边沿采样"| CDR["{v['cdr_kind']}<br/>{v['pd']}<br/>{v['cdr']}"]
        CDR -.->|"相位 φ[k]"| SAMP
    end

    subgraph MEAS["验证 & 观测"]
        CHK["PRBS 自同步检错<br/><b>{v['ber']}</b>"]
        EYE["眼图: 均衡前闭合 →<br/>均衡后{v['eye']}"]
        SNR["{v['snr']}<br/>统计引擎浴盆可外推 1e-15"]
    end

    TX --> CH --> RX
    SLC --> CHK
    SAMP -.-> EYE
    SLC -.-> SNR

    style S4P fill:#2b3a55,color:#fff
    style CHK fill:#1e4620,color:#fff
    style CDR fill:#4a3b1f,color:#fff
""", encoding="utf-8")


def embed_in_summary(png: Path, html: Path, width: int = 1500) -> bool:
    """Replace the base64 PNG in front of the system-diagram caption in
    ``html`` with ``png`` scaled to ``width``; False if the page has none."""
    import base64
    import io
    import re

    from PIL import Image

    text = html.read_text(encoding="utf-8")
    pat = re.compile(r'(src="data:image/png;base64,)([A-Za-z0-9+/=]+)(")')
    target = None
    for m in pat.finditer(text):
        # the caption has to belong to this image: before the next one
        nxt = text.find("<img", m.end())
        if SUMMARY_CAPTION in text[m.end(): m.end() + 2000 if nxt < 0 else min(nxt, m.end() + 2000)]:
            target = m
            break
    if target is None:
        return False
    img = Image.open(png)
    img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    data = base64.b64encode(buf.getvalue()).decode("ascii")
    html.write_text(text[: target.start(2)] + data + text[target.end(2):], encoding="utf-8")
    return True


def main() -> int:
    sys.path.insert(0, str(REPO / "src"))
    g = runpy.run_path(str(REPO / "examples" / "03_eq_eye_nrz32.py"), run_name="__main__")
    v = values_from_run(g)
    draw(v, PNG)
    write_mmd(v, MMD)
    embedded = embed_in_summary(PNG, SUMMARY_HTML)
    print("\n".join(f"  {k:10s} {val}" for k, val in v.items()))
    print(f"wrote {PNG.relative_to(REPO)} and {MMD.relative_to(REPO)}"
          + (f"; refreshed the copy in {SUMMARY_HTML.relative_to(REPO)}" if embedded else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
