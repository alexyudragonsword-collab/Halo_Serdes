# Build the Windows desktop exe with PyInstaller (one-folder, no-JIT).
# Run from the repo root on Windows:  powershell -File packaging\build_pyinstaller.ps1
$ErrorActionPreference = "Stop"

python -m pip install --upgrade pip
pip install -e ".[gui,desktop]"
pip install "pyinstaller>=6.0"

pyinstaller --noconfirm --clean packaging\halo_serdes_gui.spec

Write-Host "`nBuilt: dist\Halo_Serdes_GUI\Halo_Serdes_GUI.exe"
# smoke-test the frozen bundle (starts server, verifies, exits)
& "dist\Halo_Serdes_GUI\Halo_Serdes_GUI.exe" --selfcheck
