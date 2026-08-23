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


def test_gui_shims_still_resolve():
    """The old halo_serdes_gui.* paths keep working after the move."""
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
