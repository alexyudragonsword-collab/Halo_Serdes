# Packaging — Windows desktop executable

Packages the GUI into a **native-window Windows app** (via
[`pywebview`](https://pywebview.flowlib.org/)) that ships **without numba**
(`HALO_NO_JIT` is forced in `desktop.py`, so llvmlite/numba/galois are excluded
and the pure-Python kernels are used). Interactive sweeps use the fast
statistical engine, so the no-JIT build stays responsive.

Entry point: `src/halo_serdes_gui/desktop.py` (console script
`halo-serdes-gui-desktop`). It starts the Dash server on a free localhost port
in a daemon thread and opens a pywebview window; `--browser` forces a browser
tab, `--selfcheck` verifies the server starts and exits (used by CI).

## Requirements (Windows)

- Python 3.11
- WebView2 runtime (preinstalled on current Windows 10/11) for the native window
- One of the builders below

## PyInstaller (recommended — fast, reliable)

```powershell
powershell -File packaging\build_pyinstaller.ps1
# -> dist\Halo_Serdes_GUI\Halo_Serdes_GUI.exe   (one-folder, ~300 MB)
```

The spec `packaging/halo_serdes_gui.spec` collects the Dash/Plotly/dbc/skrf
package data and the vendored Bootstrap assets, and excludes numba/llvmlite/galois.

## Nuitka (compiles to C — faster startup, much slower build)

```powershell
powershell -File packaging\build_nuitka.ps1
# -> build_nuitka\desktop.dist\Halo_Serdes_GUI.exe   (standalone folder)
```

Nuitka compiles numpy/scipy/scikit-rf, so the build takes tens of minutes. Use
`--standalone` (a folder), not `--onefile`, for fast startup. If the pywebview
backend doesn't bundle cleanly, the app still runs — `desktop.py` falls back to
the system browser.

## CI

`.github/workflows/build-windows.yml` builds **both** on a `windows-latest`
runner, smoke-tests each frozen bundle with `--selfcheck`, and uploads them as
artifacts (`Halo_Serdes_GUI-pyinstaller`, `Halo_Serdes_GUI-nuitka`). Trigger it
from the Actions tab (workflow_dispatch) or by pushing changes under
`src/halo_serdes_gui/` or `packaging/`.

## Notes

- **Cross-compile is not possible** — a Windows exe must be built on Windows
  (local machine or the CI runner above).
- One-folder / standalone is preferred over one-file: one-file re-extracts to a
  temp dir on every launch (slow) and trips some antivirus.
- The PyInstaller spec was validated by building and running `--selfcheck` on
  the frozen bundle (the server starts and serves the app).
