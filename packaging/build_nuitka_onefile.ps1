# Build the Windows desktop exe with Nuitka as a SINGLE FILE (--onefile).
# Run from the repo root on Windows:  powershell -File packaging\build_nuitka_onefile.ps1
#
# Trade-off vs build_nuitka.ps1 (--standalone, the default):
#   + one self-contained .exe (easiest to distribute)
#   - slower cold start: numpy/scipy/scikit-rf (~150-250 MB) are extracted to a
#     temp dir on launch. --onefile-tempdir-spec pins a versioned cache dir so
#     the SECOND launch reuses the extraction and starts fast.
# Bundled data (configs, channels, assets) resolve via config_bridge's
# __file__-relative roots, which cover the onefile temp extraction dir.
$ErrorActionPreference = "Stop"

python -m pip install --upgrade pip
pip install -e ".[gui,desktop]"
pip install "nuitka>=2.0" ordered-set zstandard

python -m nuitka --onefile --assume-yes-for-downloads `
  --include-package=dash --include-package=plotly `
  --include-package=dash_bootstrap_components `
  --include-package=halo_serdes --include-package=halo_serdes_gui `
  --include-package-data=dash --include-package-data=plotly `
  --include-package-data=dash_bootstrap_components `
  --include-package-data=skrf `
  --include-data-dir=src/halo_serdes_gui/assets=halo_serdes_gui/assets `
  --include-data-dir=configs=configs `
  --include-data-dir=data/channels=data/channels `
  --nofollow-import-to=numba --nofollow-import-to=llvmlite `
  --nofollow-import-to=galois --nofollow-import-to=pytest `
  --windows-console-mode=disable `
  --windows-icon-from-ico=packaging/icon.ico `
  --onefile-tempdir-spec="{CACHE_DIR}/Halo_Serdes/{VERSION}" `
  --output-dir=build_nuitka_onefile --output-filename=Halo_Serdes_GUI `
  src/halo_serdes_gui/desktop.py

Write-Host "`nBuilt: build_nuitka_onefile\Halo_Serdes_GUI.exe"
& "build_nuitka_onefile\Halo_Serdes_GUI.exe" --selfcheck
