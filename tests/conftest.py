"""Shared pytest hooks.

``needs_jit``: an engine-level run (hundreds of thousands of symbols, often
two engines compared) that takes a minute or more on the pure-Python
fallback kernels. Under ``HALO_NO_JIT=1`` -- the CI leg that exercises those
kernels -- it is skipped: the kernels' parity with numba is checked bit for
bit by the kernel tests (``test_cdr_kernel``, ``test_chunked_kernels``,
``test_adc_rx``, ``test_rtl_lockstep``, ``test_fixed_loop``), so a cross-check
between the statistical and the time engine learns nothing new from the
slow path. Measured 2026-10-22 on Python 3.11: the whole suite 2644 s without
numba, the tests now marked 1245 s of it; the 3.10 nojit leg had reached
42 of its 45 CI minutes three PRs in a row. A test file that already defines
its own ``needs_jit = pytest.mark.skipif(<kernel is the Python one>, ...)``
keeps it; both skip the same way.
"""
import os

import pytest

NO_JIT = os.environ.get("HALO_NO_JIT") == "1"


def pytest_collection_modifyitems(config, items):
    if not NO_JIT:
        return
    skip = pytest.mark.skip(reason="needs_jit: minutes on the pure-Python kernels, "
                                   "whose parity the kernel tests cover (HALO_NO_JIT=1)")
    for item in items:
        if "needs_jit" in item.keywords:
            item.add_marker(skip)
