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
    # a line that gained or lost numbers cannot be paired: every old one is suspect
    (ch,) = drift.changes("32_x", "margin 3.92 dB\n", "margin fails\n")
    assert ch.pairs == [("3.92", None)]
    # one whose words changed but not its numbers moved nothing
    (ch,) = drift.changes("04_x", "BER 6.08e-04 at best phase (1.03x)\n",
                          "BER 6.08e-04 at the receiver's phase (1.03x)\n")
    assert ch.pairs == []


def test_prose_of_an_example_is_its_docstrings_and_comments(drift):
    src = '"""Margin 3.92 dB."""\nx = 3.92  # tail 4.1\n\n\ndef f():\n    """Inner 5.5."""\n    return 6\n'
    assert drift._prose_lines(src) == ['"""Margin 3.92 dB."""', "# tail 4.1", "", "", "",
                                       '    """Inner 5.5."""', ""]


def test_citations(drift):
    assert drift._cited("示例 36 / 38 与 examples/05、`32_lpo_vs_cpo.py`;example 12 and 14") == \
        {"36", "38", "05", "32", "12", "14"}
    # a count after the list is not another example
    assert drift._cited("实测(示例 38,40 万符号)") == {"38"}


def _repo(tmp_path, text):
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / "A.md").write_text(text, encoding="utf-8")
    return tmp_path


DOC = """# One

示例 32 的第三问:裕度 20.2 dB,误码 7.7e-6,2026-10-08 测,共 3 点。

同节另一段:20.2、7.7e-6、3。

# Two

别处:20.2 dB;20.17;7.7e-6。
"""


def test_stale_quotes_are_found_by_scope(drift, tmp_path):
    index = drift.DocIndex(drift.all_doc_numbers(_repo(tmp_path, DOC)))
    ch = drift.Change("32_lpo", "", "", [("20.17", "20.05"), ("7.73e-06", "7.80e-06"), ("3", "4")])
    hits = sorted((d.lineno, d.tok, where) for d, _, _, where in drift.stale(ch, index))
    assert hits == [(3, "20.2", "block"), (3, "7.7e-6", "block"),
                    (5, "20.2", "section"), (5, "7.7e-6", "section"),
                    (9, "20.17", "elsewhere"), (9, "20.2", "elsewhere")]  # "7.7e-6" too short
    # one digit is never enough: the block's "3" and the section's are both left out
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


def test_registered_derived_quotes_hold(drift):
    # examples/derived.yaml: the docs' differences / sums / ranges of printed
    # values, checked against the expected outputs -- an `accept` that moves an
    # operand without fixing the quote fails here
    entries = drift.load_derived()
    assert entries, "examples/derived.yaml is empty"
    bad, broken = drift.check_derived(entries, drift._outputs(drift.EXPECTED))
    assert not broken, broken
    assert not bad, [(e.name, doc, ln, tok, value) for e, doc, ln, tok, value in bad]


def test_derived_expressions_are_arithmetic_only(drift):
    env = {"a": 33.2, "b": 36.2, "x": __import__("numpy").array([5.07, 1.49]), "y": __import__("numpy").array([4.14, 1.40])}
    assert round(drift.evaluate("b - a", env), 6) == 3.0
    assert round(float(drift.evaluate("min(x - y)", env)), 6) == 0.09
    assert drift.evaluate("x[-1]", env) == 1.49
    # the largest of several operands ("TX PR within 0.4 dB of its prediction")
    assert round(float(drift.evaluate("max(abs(a - b), x[0] - y[0])", env)), 6) == 3.0
    for bad in ("__import__('os')", "a.real", "open('f')", "[a, b]", "a if b else 0"):
        with pytest.raises(ValueError):
            drift.evaluate(bad, env)


DERIVED_YAML = """
- name: levers
  values:
    a: {example: "21", line: "A. base: reach {} dB"}
    b: {example: "21", line: "B. + fec: reach {} dB"}
    col: {example: "21", after: "== second ==", line: "row", each: "{*}:{}"}
  quotes:
    - {doc: docs/A.md, text: "FEC 多 {b - a} dB({a} → {b})"}
    - {doc: docs/A.md, text: "under {b - a |upper} dB"}
    - {doc: docs/A.md, text: "range {min(col)}–{max(col)}, last {col[-1]}"}
"""

OUT21 = """A. base: reach 33.2 dB
B. + fec: reach 36.2 dB
== first ==
  row 1:9.0  2:9.5
== second ==
  row 1:0.42  2:1.38
"""


def test_derived_quotes_are_read_from_their_lines(drift, tmp_path):
    repo = _repo(tmp_path, "**结论**:FEC 多 3.0 dB(33.2 → 36.2),<b>under 4 dB</b>。\n\nrange 0.42–1.4, last 1.38\n")
    (repo / "examples").mkdir()
    (repo / "examples" / "derived.yaml").write_text(DERIVED_YAML, encoding="utf-8")
    entries = drift.load_derived(repo / "examples" / "derived.yaml")
    assert drift.check_derived(entries, {"21_full": OUT21}, repo) == ([], [])
    # the operand moved: the difference and the bound go stale, the rest still round
    bad, _ = drift.check_derived(entries, {"21_full": OUT21.replace("36.2", "37.5")}, repo)
    assert [(doc, ln, tok, round(v, 2)) for _, doc, ln, tok, v in bad] == \
        [("docs/A.md", 1, "3.0", 4.3), ("docs/A.md", 1, "36.2", 37.5), ("docs/A.md", 1, "4", 4.3)]
    # a reworded doc is reported, not passed
    (repo / "docs" / "A.md").write_text("FEC gains 3.0 dB\n", encoding="utf-8")
    bad, _ = drift.check_derived(entries, {"21_full": OUT21}, repo)
    assert {(doc, ln, v) for _, doc, ln, _, v in bad} == {("docs/A.md", 0, None)}
    # an operand line that is gone or ambiguous breaks the entry
    _, broken = drift.check_derived(entries, {"21_full": OUT21.replace("B. + fec", "B. fec")}, repo)
    assert "matches 0 lines" in broken[0][1]
    _, broken = drift.check_derived(entries, {"21_full": OUT21 + "A. base: reach 33.0 dB\n"}, repo)
    assert "matches 2 lines" in broken[0][1]


def test_compare_lists_derived_quotes_gone_stale(drift, tmp_path, capsys):
    repo = _repo(tmp_path / "repo", "FEC 多 3.0 dB(33.2 → 36.2)\n\nunder 4 dB\n\nrange 0.42–1.4, last 1.38\n")
    (repo / "examples").mkdir()
    (repo / "examples" / "derived.yaml").write_text(DERIVED_YAML, encoding="utf-8")
    exp, new = tmp_path / "exp", tmp_path / "new"
    exp.mkdir()
    new.mkdir()
    (exp / "21_full.txt").write_text(OUT21)
    (new / "21_full.txt").write_text(OUT21.replace("33.2", "33.0"))
    assert drift.compare(exp, new, repo) == 1
    out = capsys.readouterr().out
    # the operand itself is quoted too (and found by the plain lookup as well)
    assert "derived: levers: docs/A.md:1  '3.0'  (now 3.2)" in out
    assert "derived: levers: docs/A.md:1  '33.2'  (now 33)" in out
    assert "2 derived quote(s) no longer hold" in out


def test_candidates_are_stated_comparisons_no_cited_example_prints(drift, tmp_path, capsys):
    # line 1 is registered; line 3 states two numbers example 21 does not print
    # (the bare -18.2 dB is a loss, not a comparison); line 5's +9.5 dB is printed
    # and its 0.4 dB rounds from a printed 0.42, so only --strict lists it;
    # the last line's section cites no example
    repo = _repo(tmp_path / "repo", "示例 21:FEC 多 3.0 dB(33.2 → 36.2)。\n\n"
                                    "示例 21:ADC 再加 1.7 dB,比值 1.5×,损耗 −18.2 dB。\n\n"
                                    "示例 21:reach +9.5 dB,多 0.4 dB。\n\n"
                                    "# 别处\n\n多 2.2 dB。\n")
    (repo / "examples").mkdir()
    (repo / "examples" / "derived.yaml").write_text(DERIVED_YAML, encoding="utf-8")
    exp = tmp_path / "exp"
    exp.mkdir()
    (exp / "21_full.txt").write_text(OUT21)
    assert drift.candidates(repo, exp) == [("docs/A.md", 3, ["再加 1.7 dB", "1.5×"], ["21"])]
    assert drift.candidates(repo, exp, strict=True) == [
        ("docs/A.md", 3, ["再加 1.7 dB", "1.5×"], ["21"]), ("docs/A.md", 5, ["多 0.4 dB"], ["21"])]
    assert drift.candidates(repo, exp, docs=["docs/B.md"]) == []
    # the command, on this repository
    assert drift.main(["candidates", "README.md"]) == 0
    assert "line(s) to read" in capsys.readouterr().out
