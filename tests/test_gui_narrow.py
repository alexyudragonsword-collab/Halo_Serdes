"""The desktop GUI at phone width (ROADMAP P2 #41).

Below 992 px the page used to be one long column -- results, then 160
fields of configuration -- and the way to the form and back was scrolling.
The configuration is now a drawer over the results, opened from a bottom
bar that also runs and goes back to the results. Wide screens are as before.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("dash")

from halo_serdes_gui import app as gui_app  # noqa: E402
from halo_serdes_gui.app import build_app  # noqa: E402


@pytest.fixture(scope="module")
def callbacks():
    out = {}
    for spec in build_app().callback_map.values():
        fn = getattr(spec.get("callback"), "__wrapped__", None)
        if fn is not None:
            out[fn.__name__] = fn
    return out


def _find(node, cid):
    if isinstance(node, (list, tuple)):
        return next((f for c in node if (f := _find(c, cid)) is not None), None)
    if getattr(node, "id", None) == cid:
        return node
    kids = getattr(node, "children", None)
    return None if kids is None else _find(kids, cid)


def test_the_bottom_bar_is_for_narrow_screens_only():
    layout = build_app().layout
    bar = _find(layout, "nav-config")
    assert bar is not None and _find(layout, "nav-run") is not None
    nav = next(c for c in layout.children if "bottom-nav" in (getattr(c, "className", "") or ""))
    assert "d-lg-none" in nav.className
    col = _find(layout, "cfg-col")
    assert "cfg-drawer" in col.className and "open" not in col.className.split()
    css = (gui_app.__file__.rsplit("/", 1)[0] + "/assets/app.css")
    text = open(css, encoding="utf-8").read()
    assert "@media (max-width: 991.98px)" in text and ".cfg-drawer.open" in text


@pytest.mark.parametrize("trigger, was_open, opens", [
    ("nav-config", False, True), ("nav-config", True, False),
    ("nav-results", True, False), ("nav-run", True, False), ("run-btn", True, False),
])
def test_the_drawer_opens_from_configure_and_closes_for_results(callbacks, monkeypatch,
                                                              trigger, was_open, opens):
    monkeypatch.setattr("halo_serdes_gui.app.ctx", SimpleNamespace(triggered_id=trigger))
    cls = gui_app._DRAWER + (" open" if was_open else "")
    out = callbacks["_drawer"](1, 1, 1, 1, cls)
    assert ("open" in out.split()) is opens
    assert "order-2" in out and "order-lg-1" in out          # the wide layout is untouched
