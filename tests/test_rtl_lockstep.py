"""RTL lockstep: the SystemVerilog datapath must match the Python golden
bit-for-bit. Skipped when Icarus Verilog (iverilog) is not installed."""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(shutil.which("iverilog") is None,
                                reason="iverilog not installed")


def _compile(vec: Path, sim: Path):
    subprocess.run(
        ["iverilog", "-g2012", "-o", str(sim), "-I", str(vec),
         "rtl/ffe_dfe_datapath.sv", "rtl/tb_ffe_dfe.sv"],
        cwd=REPO, check=True, capture_output=True, text=True)


def test_rtl_lockstep_bit_exact(tmp_path):
    vec = tmp_path / "vectors"
    # generate golden vectors + dims.svh from a small fixed-point run
    subprocess.run(["python", "rtl/gen_vectors.py", str(vec)],
                   cwd=REPO, check=True, capture_output=True, text=True)
    sim = tmp_path / "sim.vvp"
    _compile(vec, sim)
    r = subprocess.run(["vvp", str(sim), f"+vecdir={vec}"],
                       cwd=REPO, capture_output=True, text=True)
    assert "LOCKSTEP PASS" in r.stdout, r.stdout + r.stderr
    assert r.returncode == 0

    # non-vacuous: corrupt one golden decision -> the check must FAIL
    dec_file = vec / "dec.txt"
    lines = dec_file.read_text().splitlines()
    lines[0] = str((int(lines[0]) + 1) % 4)
    dec_file.write_text("\n".join(lines) + "\n")
    r2 = subprocess.run(["vvp", str(sim), f"+vecdir={vec}"],
                        cwd=REPO, capture_output=True, text=True)
    assert "LOCKSTEP FAIL" in r2.stdout
    assert r2.returncode != 0
