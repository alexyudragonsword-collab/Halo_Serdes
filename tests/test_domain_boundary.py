"""Project invariant #5, as an assertion instead of a sentence.

The rule is that the waveform domain (``osr`` samples per UI) and the symbol
domain (one value per UI) are never implicitly converted — only through an
explicit sampler. It was documented in ``core/waveform.py`` and in
``cairn/architecture-invariants.md``, and it was not true: there was no sampler
module, ``SymbolStream`` was defined and never constructed anywhere in the
project, and eleven call sites crossed the domains inline with a stride slice or
a ``np.repeat``.

So the rule now has a test. Two halves, and the second is the load-bearing one:

* the sampler primitives do what they claim, including the properties that make
  them safe to share (round-trip, phase, tap placement);
* **no module outside** ``core/sampler.py`` **contains the idiom**. This is the
  half that keeps the invariant true next month.

What this deliberately does not claim: that it catches *every* conceivable
implicit conversion. It matches the idioms — stride-by-``osr`` indexing and
``np.repeat`` by ``osr`` — which is what the eleven real sites all looked like.
Someone determined to alias ``osr`` to another name can still get around it. The
value is that the obvious way to write the bug now fails the build, and the
non-obvious way has to be chosen on purpose.

Scope is ``src/halo_serdes`` only. The app and GUI layers thin series down for
plotting (``halo_serdes_app.api._decimate``, ``figures.py``), which reduces
*point count* for a chart and is not a rate-domain conversion at all.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

from halo_serdes.core.sampler import (
    baud_samples,
    hold,
    sample_baud,
    upsampled_taps,
)
from halo_serdes.core.waveform import SymbolStream, Waveform

CORE = Path(__file__).resolve().parents[1] / "src" / "halo_serdes"
SAMPLER = CORE / "core" / "sampler.py"

#: ``y[phase::osr]``, ``y[::osr]``, ``p[pk % osr :: self.osr]`` — a stride slice
#: whose step is the oversampling ratio is a waveform being sampled down.
STRIDE = re.compile(r"\[[^\]\n]*::\s*[\w.]*\bosr\b\s*\]")

#: ``np.repeat(v, osr)`` — a symbol sequence being held out to the sample grid.
HOLD = re.compile(r"np\.repeat\s*\([^)\n]*,\s*[\w.]*\bosr\b\s*\)")


def _offenders(root: Path = CORE) -> list[str]:
    """Every line in ``root`` that crosses the domains without the sampler.

    ``root`` is a parameter rather than a module global so the self-test below
    can point it at a planted violation instead of monkeypatching this module.
    """
    hits = []
    for path in sorted(root.rglob("*.py")):
        if path == SAMPLER:
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            for pat, what in ((STRIDE, "stride-by-osr slice"),
                              (HOLD, "np.repeat by osr")):
                if pat.search(code):
                    rel = path.relative_to(root.parent)
                    hits.append(f"{rel}:{n}: {what} -- {line.strip()}")
    return hits


# --------------------------------------------------------------------- #
# the enforcement
# --------------------------------------------------------------------- #

def test_no_module_crosses_the_rate_domains_by_hand():
    offenders = _offenders()
    assert not offenders, (
        "invariant #5: the waveform and symbol domains may only be crossed "
        "through halo_serdes.core.sampler. Use sample_baud/baud_samples to go "
        "down, hold() for a zero-order hold, upsampled_taps() to place "
        "symbol-rate filter taps on the sample grid:\n  "
        + "\n  ".join(offenders))


def test_the_check_can_actually_fail(tmp_path):
    """Break it once, on purpose.

    A regex that matches nothing passes every assertion built on it, and this
    project has already shipped one blind check (``assertDrew`` counting colours
    through ``Color.value.toInt()``, which is the same number for every sRGB
    colour). So: plant a violation in a scanned tree and require the scan to see
    it — otherwise the green above means only that the scanner is broken.
    """
    fake = tmp_path / "halo_serdes"
    (fake / "engine").mkdir(parents=True)
    (fake / "engine" / "bad.py").write_text(
        "def f(y, osr, phase):\n"
        "    return y[phase::osr]\n")
    (fake / "engine" / "alsobad.py").write_text(
        "import numpy as np\n"
        "def g(v, cfg):\n"
        "    return np.repeat(v, cfg.osr)\n")
    found = _offenders(fake)
    assert len(found) == 2, found
    assert any("bad.py" in f and "stride" in f for f in found)
    assert any("alsobad.py" in f and "repeat" in f for f in found)


def test_a_comment_mentioning_the_idiom_is_not_a_violation(tmp_path):
    """The scan reads code, not prose — otherwise the docs could not discuss it."""
    fake = tmp_path / "halo_serdes"
    fake.mkdir(parents=True)
    (fake / "notes.py").write_text(
        "# never write y[::osr] here; use the sampler\n"
        "X = 1\n")
    assert _offenders(fake) == []


def test_symbolstream_is_actually_constructed():
    """The symptom that started this: a documented type nobody built.

    ``sample_baud`` is the typed boundary, so it has to be reachable from
    production code — not merely exist beside an untyped primitive everyone uses
    instead.
    """
    users = [p.relative_to(CORE).as_posix()
             for p in CORE.rglob("*.py")
             if p != SAMPLER and "sample_baud(" in p.read_text()]
    assert users, "sample_baud has no caller — SymbolStream is dead again"


# --------------------------------------------------------------------- #
# the primitives
# --------------------------------------------------------------------- #

def test_baud_samples_picks_one_per_ui_at_the_requested_phase():
    osr = 8
    y = np.arange(5 * osr, dtype=float)
    for phase in range(osr):
        got = baud_samples(y, osr, phase)
        assert got.size == 5
        # exact values, not just the count: an off-by-one in the phase is the
        # error that shifts a whole eye and still looks plausible
        assert np.array_equal(got, np.arange(phase, 5 * osr, osr))


def test_sample_baud_carries_ui_and_phase():
    osr, dt = 16, 1e-12
    wave = Waveform(np.arange(4 * osr, dtype=float), dt)
    s = sample_baud(wave, osr, phase=3)
    assert isinstance(s, SymbolStream)
    assert s.ui == pytest.approx(osr * dt)
    assert s.phase == pytest.approx(3 * dt)
    assert np.array_equal(s.y, baud_samples(wave.y, osr, 3))


@pytest.mark.parametrize("phase", [-1, 8, 99])
def test_a_phase_outside_the_ui_is_rejected(phase):
    with pytest.raises(ValueError, match="phase"):
        baud_samples(np.zeros(64), 8, phase)


def test_hold_then_sample_is_the_identity():
    """The round trip that makes the pair trustworthy.

    Holding symbols out and sampling back at any phase must return exactly what
    went in — at *every* phase, because a zero-order hold is flat across the UI.
    """
    v = np.array([-3.0, 1.0, 3.0, -1.0, 1.0])
    osr = 8
    y = hold(v, osr)
    assert y.size == v.size * osr
    for phase in range(osr):
        assert np.array_equal(baud_samples(y, osr, phase), v)


def test_upsampled_taps_matches_the_idiom_it_replaced():
    """Bit-for-bit against the inline form, which is the contract.

    Six call sites wrote ``np.zeros((n-1)*osr+1)`` then ``[::osr] = taps``. This
    pins the replacement to that exact array, because the refactor claimed to
    change no numbers.
    """
    rng = np.random.default_rng(0)
    for n in (1, 2, 5, 17):
        for osr in (1, 2, 8, 32):
            taps = rng.normal(size=n)
            want = np.zeros((n - 1) * osr + 1)
            want[::osr] = taps
            assert np.array_equal(upsampled_taps(taps, osr), want)


def test_upsampled_taps_pads_to_a_requested_length():
    got = upsampled_taps([1.0, 2.0], 4, length=16)
    assert got.size == 16
    assert got[0] == 1.0 and got[4] == 2.0
    assert np.count_nonzero(got) == 2


def test_upsampled_taps_accepts_a_plain_list():
    """Callers pass ``cfg.tx.fir_taps``, which the config layer may hand over as
    a tuple or list rather than an array."""
    assert np.array_equal(upsampled_taps((1.0, 0.0, -0.5), 2),
                          np.array([1.0, 0.0, 0.0, 0.0, -0.5]))


@pytest.mark.parametrize("fn", [baud_samples, hold])
def test_a_nonsense_osr_is_rejected(fn):
    with pytest.raises(ValueError, match="osr"):
        fn(np.zeros(16), 0)
