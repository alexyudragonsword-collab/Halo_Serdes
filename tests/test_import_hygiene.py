"""Import hygiene — what the Android (Chaquopy) build can actually import.

Two independent constraints, both invisible to normal desktop testing because
every optional package happens to be installed here:

1. ``halo_serdes_app`` must not depend on a UI toolkit. That is the entire
   reason it was split out of ``halo_serdes_gui``, whose package ``__init__``
   imports the Dash app.
2. The compute path used on a phone (analytic channel + statistical engine +
   COM + FEC projection) must import with scikit-rf, PyYAML, matplotlib, numba
   and galois all absent — Chaquopy ships wheels only for numpy and scipy.

Both are enforced by blocking the modules in ``builtins.__import__`` and
importing for real, so a stray top-level import fails the suite immediately
rather than at ``pip install`` time on a device.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# Packages the Android build will not have (or that would defeat the split).
_UI_STACK = ("dash", "plotly", "dash_bootstrap_components", "diskcache")
_ABSENT_ON_ANDROID = ("skrf", "yaml", "matplotlib", "numba", "llvmlite", "galois")


def _run_isolated(blocked: tuple[str, ...], body: str) -> subprocess.CompletedProcess:
    """Run ``body`` in a fresh interpreter with ``blocked`` top-level imports denied.

    A subprocess is required: the modules under test are already in this
    process's ``sys.modules``, so an in-process guard would never see the
    import attempt.
    """
    prog = textwrap.dedent(f"""
        import builtins
        _real = builtins.__import__
        BLOCK = {blocked!r}
        def guard(name, *a, **k):
            if name.split(".")[0] in BLOCK:
                raise ImportError("blocked by test: " + name)
            return _real(name, *a, **k)
        builtins.__import__ = guard
    """) + textwrap.dedent(body)
    return subprocess.run([sys.executable, "-c", prog],
                          capture_output=True, text=True, timeout=300)


def test_app_layer_does_not_need_a_ui_toolkit():
    """halo_serdes_app is importable with the whole Dash stack blocked."""
    r = _run_isolated(_UI_STACK, """
        from halo_serdes_app import config_bridge, runner, studies
        assert config_bridge.SECTIONS and config_bridge.ALL_PATHS
        assert config_bridge.preset_names()
        assert runner.RunRecord is not None
        assert studies.fec_projection is not None
        print("OK")
    """)
    assert r.returncode == 0, f"stdout={r.stdout}\nstderr={r.stderr}"
    assert "OK" in r.stdout


def test_asset_resolution_survived_the_move():
    """Guard the depth trap: ``_find_configs_dir`` walks ``__file__.parents[N]``,
    so moving this module to another directory level silently breaks preset
    discovery — and the failure mode is a *degraded* preset list, not an error.
    """
    from halo_serdes_app import config_bridge as cb

    assert cb.CONFIGS_DIR.is_dir(), cb.CONFIGS_DIR
    names = cb.preset_names()
    # "Library defaults" is synthesised, so a broken CONFIGS_DIR still yields 1
    assert len(names) > 1, f"preset discovery degraded to {names}"
    assert len(list(cb.CONFIGS_DIR.glob("*.yaml"))) == len(names) - 1


def test_host_can_relocate_the_data_dir():
    """``$HALO_SERDES_DATA_DIR`` is how Android supplies ``configs/``.

    Chaquopy serves Python modules from an archive, so the ``__file__``-relative
    search finds nothing on a device; the host app extracts the assets and
    exports this variable before starting the interpreter. Read at import time,
    hence a subprocess.
    """
    from halo_serdes_app import config_bridge as cb

    r = _run_isolated((), f"""
        import os, shutil, tempfile, pathlib
        tmp = pathlib.Path(tempfile.mkdtemp())
        shutil.copytree({str(cb.CONFIGS_DIR)!r}, tmp / "configs")
        os.environ["HALO_SERDES_DATA_DIR"] = str(tmp)
        from halo_serdes_app import config_bridge as cb
        assert cb.CONFIGS_DIR == tmp / "configs", cb.CONFIGS_DIR
        assert len(cb.preset_names()) > 1
        print("OK")
    """)
    assert r.returncode == 0, f"stdout={r.stdout}\nstderr={r.stderr}"
    assert "OK" in r.stdout


def test_missing_preset_fails_where_the_cause_is():
    """A preset that cannot be found must raise, not degrade to defaults.

    ``LinkConfig()`` defaults to a touchstone channel with no file, so the old
    silent fallback turned "configs/ was not packaged" into "channel.file is
    unset" raised from deep inside the engine — which is precisely how the
    first on-device run misdiagnosed itself.
    """
    from halo_serdes_app import config_bridge as cb

    with pytest.raises(KeyError, match="unknown preset"):
        cb.load_preset("no such preset")


def test_gui_shims_still_resolve():
    """The old halo_serdes_gui.* paths keep working after the move."""
    # importing the shim pulls halo_serdes_gui/__init__ -> the Dash app, which
    # the import-clean CI job deliberately does not install
    pytest.importorskip("dash", reason="GUI extra not installed")
    from halo_serdes_gui import config_bridge as cb
    from halo_serdes_gui import runner, studies
    from halo_serdes_app import config_bridge as app_cb

    assert cb.SECTIONS is app_cb.SECTIONS          # same object, not a copy
    assert cb.build_config is app_cb.build_config
    assert runner.RunRecord.__module__.startswith("halo_serdes_app")
    assert studies.reach_study.__module__.startswith("halo_serdes_app")


@pytest.mark.parametrize("blocked", [
    pytest.param(_ABSENT_ON_ANDROID, id="all-absent"),
    pytest.param(("skrf",), id="no-scikit-rf"),
    pytest.param(("yaml",), id="no-pyyaml"),
])
def test_phone_compute_path_imports_without_optional_packages(blocked):
    """Import-only check: the modules a phone needs must not pull in absentees."""
    r = _run_isolated(blocked, """
        from halo_serdes.config.schema import LinkConfig
        from halo_serdes.channel import ChannelModel
        from halo_serdes.engine.statistical import run_statistical
        from halo_serdes.analysis.com import compute_com
        from halo_serdes.fec import pre_to_post_fec_ber
        print("OK")
    """)
    assert r.returncode == 0, f"stdout={r.stdout}\nstderr={r.stderr}"


def test_touchstone_path_works_without_pandas():
    """The Android build installs scikit-rf with ``--no-deps``. This is why.

    scikit-rf declares ``pandas>=1.1`` as a hard requirement and never imports
    it on the Touchstone path — so honouring the declaration would drag a
    large compiled wheel into the APK to satisfy metadata alone. ``--no-deps``
    skips it, which also means pip stops checking: a future scikit-rf that
    genuinely reaches for pandas would fail on the device, at import, with the
    app already in someone's hand.

    This is that check, moved to the host. It blocks pandas outright and runs
    the whole read the engine runs — parse, mixed-mode conversion, model
    construction, insertion loss.
    """
    files = sorted(Path(__file__).resolve().parents[1].glob("data/channels/*.s4p"))
    if not files:
        pytest.skip("no bundled .s4p to read")
    r = _run_isolated(("pandas", "matplotlib", "numba", "llvmlite", "galois"), f"""
        from halo_serdes.channel import ChannelModel, import_diff_network
        import numpy as np

        path = {str(files[0])!r}
        sdd = import_diff_network(path)
        assert sdd.f.size > 0
        ch = ChannelModel.from_touchstone(path, f_max=20e9, n_freq=512)
        il = ch.insertion_loss_db()
        assert np.isfinite(il[1:]).any(), "insertion loss is all non-finite"
        assert il[1:].min() < 0.0, "a through channel must show loss"
        print("OK")
    """)
    assert r.returncode == 0, f"stdout={r.stdout}\nstderr={r.stderr}"


def test_phone_compute_path_runs_without_optional_packages():
    """Not just importable — the analytic + statistical + COM + FEC path must
    actually produce numbers with every non-numpy/scipy package blocked."""
    r = _run_isolated(_ABSENT_ON_ANDROID, """
        from halo_serdes.config.schema import (
            ChannelConfig, CtleConfig, DfeConfig, LinkConfig, RxConfig,
            SimConfig, TxConfig,
        )
        from halo_serdes.channel import ChannelModel
        from halo_serdes.engine.statistical import run_statistical
        from halo_serdes.analysis.com import compute_com
        from halo_serdes.fec import pre_to_post_fec_ber

        cfg = LinkConfig(
            modulation="nrz", symbol_rate=28e9, osr=16,
            channel=ChannelConfig(kind="analytic", length_m=0.28, rdc=5.0,
                                  r_skin=2e-3, loss_tangent=0.012, n_freq=4096),
            tx=TxConfig(swing=1.0, fir_taps=(-0.08, 0.85, -0.05), fir_n_pre=1),
            rx=RxConfig(arch="mixed_signal",
                        ctle=CtleConfig(enable=True, peak_db=7.0),
                        dfe=DfeConfig(n_taps=2), noise_rms=0.004),
            sim=SimConfig(n_symbols=2000, seed=5, pattern="prbs13"))
        ch = ChannelModel.from_config(cfg)
        s = run_statistical(cfg, channel=ch)
        c = compute_com(ch, cfg)
        f = pre_to_post_fec_ber(2.4e-4, "kp4")
        assert 0.0 <= s.ber <= 1.0, s.ber
        assert -50.0 < c.com_db < 50.0, c.com_db
        assert 0.0 <= f < 1e-6, f
        print("OK")
    """)
    assert r.returncode == 0, f"stdout={r.stdout}\nstderr={r.stderr}"
    assert "OK" in r.stdout
