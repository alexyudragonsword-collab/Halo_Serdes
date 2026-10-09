"""tools/example_drift.py: the expected outputs cover every example and are
stored masked; a moved number is paired with its replacement and found in the
docs that quote it at the old value's precision -- and not where the quote's
rounding still holds for the new value. Running the examples at full size is
the examples-full workflow's job, not a unit test's."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def drift():
    spec = importlib.util.spec_from_file_location("example_drift", REPO / "tools" / "example_drift.py")
    mod = importlib.util.module_from_spec(spec)
    # dataclasses resolve string annotations through sys.modules
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop(spec.name, None)


def test_expected_outputs_cover_every_example(drift):
    # a new example without an expected output would never be compared; a
    # renamed one would leave its old output compared against nothing
    examples = {p.stem for p in (REPO / "examples").glob("[0-9][0-9]_*.py")}
    assert {p.stem for p in drift.EXPECTED.glob("*.txt")} == examples


def test_expected_outputs_are_stored_normalised(drift):
    for p in sorted(drift.EXPECTED.glob("*.txt")):
        text = p.read_text(encoding="utf-8")
        assert drift.normalise(text) == text, p.name


def test_timings_and_paths_are_masked(drift):
    raw = ("a  [3.2s]\nb [12 s]  \n18 runs in 33s\nloss 3 s-params\n"
           "  wrote /home/runner/work/H/H/examples/output/01_x.png\n== pkg: /home/u/H/examples/output/rtl ==\n")
    assert drift.normalise(raw) == ("a  [~s]\nb [~s]\n18 runs in ~s\nloss 3 s-params\n"
                                    "  wrote examples/output/01_x.png\n== pkg: examples/output/rtl ==\n")


def test_number_tokens(drift):
    line = "-22.7dB C2M 24.8dB 1.1e-100 PAM4 802.3dj v1.5.2 (11) x2 180m"
    assert drift.NUM.findall(line) == ["-22.7", "24.8", "1.1e-100", "802.3", "11", "180"]


def test_rounding_and_precision(drift):
    assert drift.rounds_to("4.7", 4.66) and drift.rounds_to("4.7", -4.74)
    assert not drift.rounds_to("4.7", 4.76)
    assert drift.rounds_to("2.4e-4", 2.41e-4) and not drift.rounds_to("2.4e-4", 2.46e-4)
    assert [drift._sig(t) for t in ("0.005", "20.2", "100", "1.50e-04", "180")] == [1, 3, 1, 3, 2]


def test_changes_pair_numbers_on_a_line_of_the_same_shape(drift):
    old = "head\n  4dB  180m  7.73e-06  20.2dB  [~s]\ntail\n"
    new = "head\n  4dB  180m  7.80e-06  20.1dB  [~s]\ntail\nextra 5\n"
    moved, added = drift.changes("32_x", old, new)
    assert moved.pairs == [("7.73e-06", "7.80e-06"), ("20.2", "20.1")]
    assert added.old_line is None and added.new_line == "extra 5"
    # a line that changed shape cannot be paired: every old number is suspect
    (ch,) = drift.changes("32_x", "margin 3.92 dB\n", "margin fails\n")
    assert ch.pairs == [("3.92", None)]


def test_prose_of_an_example_is_its_docstrings_and_comments(drift):
    src = '"""Margin 3.92 dB."""\nx = 3.92  # tail 4.1\n\n\ndef f():\n    """Inner 5.5."""\n    return 6\n'
    assert drift._prose_lines(src) == ['"""Margin 3.92 dB."""', "# tail 4.1", "", "", "",
                                       '    """Inner 5.5."""', ""]


def test_citations(drift):
    assert drift._cited("示例 36 / 38 与 examples/05、`32_lpo_vs_cpo.py`;example 12 and 14") == \
        {"36", "38", "05", "32", "12", "14"}


def _repo(tmp_path, text):
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / "A.md").write_text(text, encoding="utf-8")
    return tmp_path


DOC = """# One

示例 32 的第三问:裕度 20.2 dB,误码 7.7e-6,2026-10-08 测。

同节另一段:20.2、7.7e-6、3。

# Two

别处:20.2 dB;20.17;7.7e-6。
"""


def test_stale_quotes_are_found_by_scope(drift, tmp_path):
    index = drift.DocIndex(drift.all_doc_numbers(_repo(tmp_path, DOC)))
    ch = drift.Change("32_lpo", "", "", [("20.17", "20.05"), ("7.73e-06", "7.80e-06"), ("3", "4")])
    hits = sorted((d.lineno, d.tok, where) for d, _, _, where in drift.stale(ch, index))
    assert hits == [(3, "20.2", "block"), (3, "7.7e-6", "block"),
                    (5, "20.2", "section"), (5, "7.7e-6", "section"),   # "3" too short there
                    (9, "20.17", "elsewhere"), (9, "20.2", "elsewhere")]  # "7.7e-6" too short
    # 20.17 -> 20.19: a doc saying 20.2 is still right; one saying 20.17 is not
    ch = drift.Change("32_lpo", "", "", [("20.17", "20.19")])
    assert [(d.lineno, d.tok) for d, *_ in drift.stale(ch, index)] == [(9, "20.17")]


def test_compare_and_accept(drift, tmp_path, capsys):
    repo = _repo(tmp_path / "repo", "示例 32:裕度 20.2 dB。\n")
    exp, new = tmp_path / "exp", tmp_path / "new"
    exp.mkdir()
    new.mkdir()
    (exp / "32_x.txt").write_text("margin 20.2 dB [~s]\n")
    (exp / "33_y.txt").write_text("other 1\n")
    (new / "32_x.txt").write_text("margin 20.2 dB [41.0s]\n")  # only the timing moved
    assert drift.compare(exp, new, repo) == 0
    assert "not compared: 33_y" in capsys.readouterr().out
    (new / "32_x.txt").write_text("margin 19.8 dB [41.0s]\n")
    assert drift.compare(exp, new, repo) == 1
    out = capsys.readouterr().out
    assert "docs/A.md:1  '20.2'  (20.2 -> 19.8; block)" in out
    assert drift.accept(new, exp) == ["32_x"]
    assert (exp / "32_x.txt").read_text() == "margin 19.8 dB [~s]\n"
    assert drift.compare(exp, new, repo) == 0
    (new / "34_z.txt").write_text("new example\n")
    assert drift.main(["compare", str(new), "--expected", str(exp)]) == 1
    assert "34_z: no expected output" in capsys.readouterr().out
