"""tools/draw_system_diagram.py: every label comes from the run, and the PNG,
the Mermaid source and summary.html's embedded copy are written from the same
values. Stub results stand in for example 03's run (the real one is ~15 s)."""

from __future__ import annotations

import base64
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("matplotlib")
pytest.importorskip("PIL")

from halo_serdes.config import apply_overrides, load_config  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def _tool():
    spec = importlib.util.spec_from_file_location("draw_system_diagram",
                                                  REPO / "tools" / "draw_system_diagram.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_globals():
    cfg = load_config(REPO / "configs" / "nrz_32g.yaml", overrides={
        "channel.file": str(REPO / "data/channels/TEC_Whisper42p8in_Meg6_THRU_C8C9.s4p"),
        "sim.n_symbols": 200_000})
    cfg = apply_overrides(cfg, {"rx.noise_rms": 0.003, "rx.ffe.n_pre": 4, "rx.ffe.n_post": 12,
                                "rx.dfe.n_taps": 3})
    res = SimpleNamespace(extras={"main_cursor": 0.01737}, slicer_snr_db=15.07,
                          ber=SimpleNamespace(n_errors=0, n_checked=199974))
    channel = SimpleNamespace(loss_at=lambda f: -31.83)
    return {"cfg": cfg, "res": res, "channel": channel, "eye_h": 0.00684}


def test_labels_come_from_the_config_and_the_results():
    v = _tool().values_from_run(_run_globals())
    assert v["slicer"] == "±17.4 mV" and v["snr"] == "slicer SNR 15.1 dB"
    assert v["eye"] == "内眼 6.8 mV" and v["ber"] == "BER = 0 / 199,974"
    assert v["loss"] == "-31.8 dB @ 16 GHz" and v["channel"] == 'Whisper 42.8"'
    assert v["ffe"].startswith("17 taps") and v["dfe"] == "3 taps"
    cdr = _run_globals()["cfg"].rx.cdr
    assert v["cdr"] == f"Kp=1/{2 ** cdr.kp_shift}  Ki=1/{2 ** cdr.ki_shift}"


def test_png_mermaid_and_the_summary_copy_are_written_from_the_same_values(tmp_path):
    tool = _tool()
    v = tool.values_from_run(_run_globals())
    png, mmd = tmp_path / "d.png", tmp_path / "d.mmd"
    tool.draw(v, png)
    tool.write_mmd(v, mmd)
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    text = mmd.read_text(encoding="utf-8")
    for key in ("slicer", "snr", "ber", "cdr", "loss", "eye"):
        assert v[key] in text, key
    # the page's copy is the one in front of the system-diagram caption
    dot = base64.b64encode(b"\x89PNG\r\n\x1a\nold").decode()
    html = tmp_path / "s.html"
    html.write_text(f'<img src="data:image/png;base64,{dot}"><figcaption>other</figcaption>'
                    f'<img src="data:image/png;base64,{dot}"><figcaption><span>'
                    f'{tool.SUMMARY_CAPTION}。</span></figcaption>', encoding="utf-8")
    assert tool.embed_in_summary(png, html, width=300)
    out = html.read_text(encoding="utf-8")
    assert out.count(dot) == 1                       # the unrelated image is untouched
    assert out.index(dot) < out.index("other")
    (tmp_path / "none.html").write_text("<p>no images</p>", encoding="utf-8")
    assert not tool.embed_in_summary(png, tmp_path / "none.html")
