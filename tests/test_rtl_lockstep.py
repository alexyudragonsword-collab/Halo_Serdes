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


def test_rtl_loop_lockstep_bit_exact(tmp_path):
    """The whole back end with its CDR (rtl/adc_dsp_loop.sv) against the
    closed-loop golden: every PI code, slicer value and decision."""
    vec = tmp_path / "vectors"
    subprocess.run(["python", "rtl/gen_vectors.py", str(vec)],
                   cwd=REPO, check=True, capture_output=True, text=True)
    # delta target, 1 + aD + bD^2 with a and b adapted, precoded 1 + D, and
    # offset / gain mismatch with the background calibration on
    for d in (vec / "pr", vec / "pre", vec / "cal", vec):
        sim = tmp_path / f"loop_{d.name}.vvp"
        subprocess.run(["iverilog", "-g2012", "-o", str(sim), "-I", str(d),
                        "rtl/adc_dsp_loop.sv", "rtl/tb_adc_dsp_loop.sv"],
                       cwd=REPO, check=True, capture_output=True, text=True)
        r = subprocess.run(["vvp", str(sim), f"+vecdir={d}"], cwd=REPO,
                           capture_output=True, text=True)
        assert "LOOP LOCKSTEP PASS" in r.stdout, d.name + r.stdout + r.stderr
        assert r.returncode == 0
    ab = [int(x) for x in (vec / "pr" / "loop_ab.txt").read_text().split()]
    ab_end = [int(x) for x in (vec / "pr" / "loop_ab_end.txt").read_text().split()]
    assert ab != ab_end, "the PR vectors should adapt a and b"
    pi = [int(x) for x in (vec / "loop_pi.txt").read_text().split()]
    assert max(pi) - min(pi) >= 3, "the vectors should make the loop move the PI"
    cal_end = [int(x) for x in (vec / "cal" / "loop_cal_end.txt").read_text().split()]
    gains = cal_end[len(cal_end) // 2: -1]
    assert max(gains) - min(gains) > 1000, "the calibration vectors should move the lane gains"
    assert "CAL_ON = 1" in (vec / "cal" / "loop_dims.svh").read_text()

    # non-vacuous: one PI code off -> the check must FAIL
    f = vec / "loop_pi.txt"
    lines = f.read_text().splitlines()
    lines[len(lines) // 2] = str(int(lines[len(lines) // 2]) + 1)
    f.write_text("\n".join(lines) + "\n")
    r2 = subprocess.run(["vvp", str(sim), f"+vecdir={vec}"], cwd=REPO,
                        capture_output=True, text=True)
    assert "LOOP LOCKSTEP FAIL" in r2.stdout
    assert r2.returncode != 0


def test_rtl_mlsd_lockstep_bit_exact(tmp_path):
    """The sliding-detector MLSD (rtl/sliding_mlsd.sv) against dsp/fixed_mlsd."""
    vec = tmp_path / "vectors"
    subprocess.run(["python", "rtl/gen_vectors.py", str(vec)],
                   cwd=REPO, check=True, capture_output=True, text=True)
    sim = tmp_path / "mlsd.vvp"
    subprocess.run(["iverilog", "-g2012", "-o", str(sim), "-I", str(vec),
                    "rtl/sliding_mlsd.sv", "rtl/tb_sliding_mlsd.sv"],
                   cwd=REPO, check=True, capture_output=True, text=True)
    r = subprocess.run(["vvp", str(sim), f"+vecdir={vec}"], cwd=REPO,
                       capture_output=True, text=True)
    assert "MLSD LOCKSTEP PASS" in r.stdout, r.stdout + r.stderr
    flips = int(r.stdout.split("flips=")[1].split()[0])
    assert flips >= 20, "the vectors should make the detector correct decisions"

    f = vec / "mlsd_dec.txt"
    lines = f.read_text().splitlines()
    lines[100] = str((int(lines[100]) + 1) % 4)
    f.write_text("\n".join(lines) + "\n")
    r2 = subprocess.run(["vvp", str(sim), f"+vecdir={vec}"], cwd=REPO,
                        capture_output=True, text=True)
    assert "MLSD LOCKSTEP FAIL" in r2.stdout
    assert r2.returncode != 0


def test_rtl_viterbi_lockstep_bit_exact(tmp_path):
    """The Viterbi MLSD (rtl/viterbi_mlsd.sv) against dsp/fixed_viterbi."""
    vec = tmp_path / "vectors"
    subprocess.run(["python", "rtl/gen_vectors.py", str(vec)],
                   cwd=REPO, check=True, capture_output=True, text=True)
    sim = tmp_path / "vit.vvp"
    subprocess.run(["iverilog", "-g2012", "-o", str(sim), "-I", str(vec),
                    "rtl/viterbi_mlsd.sv", "rtl/tb_viterbi_mlsd.sv"],
                   cwd=REPO, check=True, capture_output=True, text=True)
    r = subprocess.run(["vvp", str(sim), f"+vecdir={vec}"], cwd=REPO,
                       capture_output=True, text=True)
    assert "VITERBI LOCKSTEP PASS" in r.stdout, r.stdout + r.stderr

    f = vec / "vit_dec.txt"
    lines = f.read_text().splitlines()
    lines[500] = str((int(lines[500]) + 1) % 4)
    f.write_text("\n".join(lines) + "\n")
    r2 = subprocess.run(["vvp", str(sim), f"+vecdir={vec}"], cwd=REPO,
                        capture_output=True, text=True)
    assert "VITERBI LOCKSTEP FAIL" in r2.stdout
    assert r2.returncode != 0

