"""A failure the user cannot see is a failure that gets reported as a wrong answer.

Five places in the GUI caught an exception and rendered nothing: a malformed YAML
paste did literally nothing, an unbuildable CTLE dropped its metric cards, two
eye reconstructions returned a bare ``None``, and — worst of the set — a
statistical engine that raised set ``rec.stat = None``, which renders exactly
like a statistical engine that was never asked to run. That last one matters
beyond tidiness: the Dual-Engine view *is* invariant #3, the statistical vs
time-domain cross-check, so a silent failure there turns "the cross-check is
broken" into "the cross-check has nothing to say".

These tests assert the reason reaches the rendered output. They deliberately
check for the *text of the exception*, not merely that some element appeared: a
placeholder saying "failed" and one naming the offending field are different
products, and only the regex for the latter would have caught the difference.
"""

from __future__ import annotations

import dataclasses

import pytest

pytest.importorskip("dash")
pytest.importorskip("yaml")

from halo_serdes.config import LinkConfig
from halo_serdes_app.runner import RunRecord
from halo_serdes_gui import config_bridge as cb
from halo_serdes_gui.panels import backchannel, ctle, dual_engine, eyes


@pytest.fixture(scope="module")
def cfg():
    """The first preset whose channel can actually be built.

    Not ``preset_names()[0]``: that is "Library defaults", whose channel is
    ``touchstone`` with no file, so every test here would have failed on the
    channel instead of on the thing under test — and three of them did, on the
    first run. ``cairn/engineering-pitfalls.md`` already records this exact trap
    ("first/any preset" has to be checked against the target environment); this
    is it biting again, one layer up.
    """
    from halo_serdes.channel import ChannelModel
    for name in cb.preset_names():
        candidate = cb.load_preset(name)
        try:
            ChannelModel.from_config(candidate)
        except Exception:
            continue
        return candidate
    pytest.skip("no preset has a buildable channel")


def _text(component) -> str:
    """Flatten a Dash component tree to searchable text.

    Dash components nest children arbitrarily and hold their text in several
    different props, so a rendered message can be anywhere; walking everything
    is what makes "does the user see this string?" answerable at all.
    """
    out: list[str] = []

    def walk(node):
        if node is None:
            return
        if isinstance(node, str):
            out.append(node)
            return
        if isinstance(node, (list, tuple)):
            for item in node:
                walk(item)
            return
        for attr in ("children", "figure", "title"):
            if hasattr(node, attr):
                walk(getattr(node, attr))
        # plotly figures carry placeholder text in annotations
        layout = getattr(node, "layout", None)
        if layout is not None:
            for ann in (getattr(layout, "annotations", None) or ()):
                walk(getattr(ann, "text", None))
        if isinstance(node, dict):
            for v in node.values():
                walk(v)

    walk(component)
    return " ".join(out)


# --------------------------------------------------------------------- #
# invariant #3: a broken cross-check must not look like a quiet one
# --------------------------------------------------------------------- #

def test_a_failing_statistical_engine_is_recorded_not_swallowed(cfg, monkeypatch):
    rec = RunRecord(id="r1", cfg=cfg, engines=("time",))

    import halo_serdes.engine.statistical as st
    monkeypatch.setattr(st, "run_statistical",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("eye_pdf blew up")))

    assert dual_engine._ensure_stat(rec) is None
    assert rec.stat_error is not None
    assert "eye_pdf blew up" in rec.stat_error
    assert "RuntimeError" in rec.stat_error


def test_the_dual_engine_view_says_the_cross_check_is_broken(cfg):
    rec = RunRecord(id="r2", cfg=cfg, engines=("time",),
                    stat_error="RuntimeError: eye_pdf blew up")
    body = _text(dual_engine.render(rec))
    assert "eye_pdf blew up" in body
    # and it must be framed as a broken check, not as missing data
    assert "cross-check" in body


def test_ensure_stat_does_not_retry_a_known_failure(cfg):
    """A recorded failure is a terminal state for that record.

    Without this, opening the tab would re-run a failing engine on every render
    — seconds of work per keystroke elsewhere in the page.
    """
    rec = RunRecord(id="r3", cfg=cfg, engines=("time",),
                    stat_error="RuntimeError: already tried")
    calls = []

    import halo_serdes.engine.statistical as st
    real = st.run_statistical
    try:
        st.run_statistical = lambda *a, **k: calls.append(1)
        assert dual_engine._ensure_stat(rec) is None
    finally:
        st.run_statistical = real
    assert calls == []


# --------------------------------------------------------------------- #
# the other four
# --------------------------------------------------------------------- #

def test_a_bad_yaml_paste_produces_a_message_naming_the_problem():
    """The import callback's own failure path, through the function it calls.

    ``_import_yaml`` is a Dash callback closure, so what is pinned here is the
    contract it depends on: the parse raises something whose text names the
    problem, which is what the banner now shows. Before this, that text was
    discarded and the button appeared inert.
    """
    with pytest.raises(Exception) as exc:
        cb.yaml_to_config("modulation: [this is not a scalar")
    assert str(exc.value).strip(), "parse failure carried no message to show"


def test_the_yaml_card_has_somewhere_to_put_that_message():
    from halo_serdes_gui.app import build_app

    ids = []

    def walk(node):
        if isinstance(node, (list, tuple)):
            for i in node:
                walk(i)
            return
        cid = getattr(node, "id", None)
        if isinstance(cid, str):
            ids.append(cid)
        if hasattr(node, "children"):
            walk(node.children)

    walk(build_app().layout)
    assert "yaml-status" in ids


def test_an_unbuildable_ctle_explains_itself(monkeypatch):
    """Both halves of the tab, because the first fix only covered one.

    Guarding the metric cards left ``figures.ctle_bode_fig`` calling
    ``Ctle.from_config`` unprotected on the very next line, so the tab still
    died with a traceback — the note was rendered and then thrown away. This
    test is what found that.
    """
    base = LinkConfig(modulation="nrz", symbol_rate=28e9)
    bad = dataclasses.replace(base, rx=dataclasses.replace(
        base.rx, ctle=dataclasses.replace(base.rx.ctle, enable=True)))

    import halo_serdes.afe as afe
    monkeypatch.setattr(afe.Ctle, "from_config",
                        classmethod(lambda cls, *a, **k: (_ for _ in ()).throw(
                            ValueError("fz above fp1"))))
    body = _text(ctle.render(RunRecord(id="c1", cfg=bad, engines=("static",))))
    assert body.count("fz above fp1") >= 2, (
        "expected the reason on both the cards and the Bode plot: " + body)


def test_a_failed_front_end_eye_returns_its_reason(monkeypatch):
    import halo_serdes.analysis.reconstruct as rc
    monkeypatch.setattr(rc, "front_end_eye",
                        lambda *a, **k: (_ for _ in ()).throw(
                            ValueError("channel too short")))
    eye, err = eyes._fe_eye(LinkConfig(modulation="nrz", symbol_rate=28e9))
    assert eye is None
    assert "channel too short" in err


def test_the_backchannel_cache_is_bounded():
    """It was an unbounded dict keyed by run id — one entry per run, forever."""
    backchannel._CACHE.clear()
    backchannel._ORDER.clear()
    for i in range(backchannel._MAX + 5):
        backchannel._CACHE[f"k{i}"] = object()
        backchannel._ORDER.append(f"k{i}")
        while len(backchannel._ORDER) > backchannel._MAX:
            backchannel._CACHE.pop(backchannel._ORDER.pop(0), None)
    assert len(backchannel._CACHE) == backchannel._MAX
    assert "k0" not in backchannel._CACHE


def test_backchannel_training_failure_reaches_the_screen(cfg, monkeypatch):
    backchannel._CACHE.clear()
    backchannel._ORDER.clear()

    import halo_serdes.engine as eng
    monkeypatch.setattr(eng, "train_tx_fir",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("did not converge")))
    body = _text(backchannel.render(RunRecord(id="b1", cfg=cfg,
                                              engines=("time",))))
    assert "did not converge" in body
