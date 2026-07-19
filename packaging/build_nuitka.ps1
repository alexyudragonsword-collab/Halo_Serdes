# Build the Windows desktop exe with Nuitka (standalone, no-JIT).
# Run from the repo root on Windows:  powershell -File packaging\build_nuitka.ps1
# NOTE: Nuitka compiles numpy/scipy/scikit-rf to C — the build is slow
# (tens of minutes). Use --standalone (a folder), not --onefile, for fast startup.
$ErrorActionPreference = "Stop"

python -m pip install --upgrade pip
pip install -e ".[gui,desktop]"
pip install "nuitka>=2.0" ordered-set zstandard

python -m nuitka --standalone --assume-yes-for-downloads `
  --include-package=dash --include-package=plotly `
  --include-package=dash_bootstrap_components `
  --include-package=halo_serdes --include-package=halo_serdes_gui `
  --include-package-data=dash --include-package-data=plotly `
  --include-package-data=dash_bootstrap_components `
  --include-package-data=skrf `
  --include-data-dir=src/halo_serdes_gui/assets=halo_serdes_gui/assets `
  --nofollow-import-to=numba --nofollow-import-to=llvmlite `
  --nofollow-import-to=galois --nofollow-import-to=pytest `
  --windows-console-mode=disable `
  --output-dir=build_nuitka --output-filename=Halo_Serdes_GUI `
  src/halo_serdes_gui/desktop.py

Write-Host "`nBuilt: build_nuitka\desktop.dist\Halo_Serdes_GUI.exe"
& "build_nuitka\desktop.dist\Halo_Serdes_GUI.exe" --selfcheck
