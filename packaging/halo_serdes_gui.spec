# PyInstaller spec — one-folder, no-JIT desktop build.
#
# Ships without numba/llvmlite/galois (the framework imports them lazily and
# HALO_NO_JIT=1 in desktop.py forces the pure-Python kernels). Collects the
# Dash/Plotly/dbc/skrf package data files and the vendored assets folder.
#
# Build (on Windows):  pyinstaller packaging/halo_serdes_gui.spec
# Output:              dist/Halo_Serdes_GUI/Halo_Serdes_GUI(.exe)

import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
SRC = os.path.join(ROOT, "src")


def _data(pkg):
    try:
        return collect_data_files(pkg)
    except Exception:
        return []


def _subs(pkg):
    try:
        return collect_submodules(pkg)
    except Exception:
        return []


datas = []
hidden = []
for pkg in ("dash", "plotly", "dash_bootstrap_components", "skrf", "webview"):
    datas += _data(pkg)
    hidden += _subs(pkg)

# vendored assets (Bootstrap + polish css) -> halo_serdes_gui/assets in bundle
datas += [(os.path.join(SRC, "halo_serdes_gui", "assets"),
           "halo_serdes_gui/assets")]
# example presets -> configs/ (so the Load dropdown is populated in the exe)
datas += [(os.path.join(ROOT, "configs"), "configs")]
# touchstone channels referenced by the presets -> data/channels/
datas += [(os.path.join(ROOT, "data", "channels"), "data/channels")]
hidden += _subs("halo_serdes") + _subs("halo_serdes_gui")

a = Analysis(
    [os.path.join(SRC, "halo_serdes_gui", "desktop.py")],
    pathex=[SRC],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    excludes=["numba", "llvmlite", "galois", "tkinter", "pytest",
              "IPython", "notebook"],
    noarchive=False,
)
pyz = PYZ(a.pure)
_icon = os.path.join(SPECPATH, "icon.ico")
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name="Halo_Serdes_GUI",
    console=False,               # GUI app (pywebview window)
    disable_windowed_traceback=False,
    icon=_icon if os.path.exists(_icon) else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Halo_Serdes_GUI")
