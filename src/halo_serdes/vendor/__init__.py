"""Vendored (adapted-copy) modules from sibling repositories.

Halo_Serdes is self-contained: the two phase-noise primitives behind
``ClockConfig(kind="profile")`` come from
``alexyudragonsword-collab/pll_simulator`` (package ``pllsim``, commit
931cfaf) and are copied here rather than imported, because

* the Android build carries numpy and scipy and nothing else, and pllsim
  declares matplotlib as a hard dependency;
* the interface between the two libraries is a *file* -- the clock
  phase-noise profile -- not a Python import.  A profile is data a frozen
  ``LinkConfig`` can name (invariant #1); a PLL object is not.

Each file carries a header naming its origin.  Both copies are verbatim:

- ``pllsim/colored.py``: ``synth_from_psd`` (FFT-domain shaping of white
  Gaussian noise to a target one-sided PSD).  The ``OscPhaseNoiseGen`` class
  in the same file is unused here and kept so the file stays byte-identical
  to upstream.
- ``pllsim/jitter.py``: ``integrate_pn``, ``sphi_from_ldbc``,
  ``ldbc_from_sphi`` and the dBc/jitter unit conversions.

Everything else in Halo_Serdes imports these exclusively through
``halo_serdes.vendor.pllsim...`` so the boundary stays a single seam;
upstream fixes are pulled by re-copying the file.  ``tools/vendor_check.py``
re-derives each file's relationship to the pinned upstream and fails CI on
an undeclared edit.
"""
