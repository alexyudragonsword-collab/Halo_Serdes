"""tools/fingerprint.py: compare catches a moved value anywhere in the tree,
including one that is present on one side only, and result_values reads the
fields every engine result carries. The full record (every preset through
every engine) is the tool's own job, not a unit test's."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def fp():
    spec = importlib.util.spec_from_file_location("fingerprint", REPO / "tools" / "fingerprint.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_compare_finds_every_kind_of_difference(fp):
    a = {"presets": {"p": {"time": {"ser": "0.1", "y": "abc"}}}, "synthetic": {"x": ["1", "2"]}}
    assert fp.compare(a, json.loads(json.dumps(a))) == (3, [])
    b = {"presets": {"p": {"time": {"ser": "0.1", "y": "abd", "new": "1"}}},
         "synthetic": {"x": ["1", "3"]}}
    n, diffs = fp.compare(a, b)
    assert n == 4
    assert diffs == ["presets / p / time / new: None -> '1'",
                     "presets / p / time / y: 'abc' -> 'abd'",
                     "synthetic / x: ['1', '2'] -> ['1', '3']"]


def test_compare_cli_exit_codes(fp, tmp_path, capsys):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps({"k": "1"}))
    b.write_text(json.dumps({"k": "1"}))
    assert fp.main(["compare", str(a), str(b)]) == 0
    b.write_text(json.dumps({"k": "2"}))
    assert fp.main(["compare", str(a), str(b)]) == 1
    assert "k: '1' -> '2'" in capsys.readouterr().out


def test_result_values_reads_the_engine_fields(fp):
    r = SimpleNamespace(ser=1e-3, slicer_snr_db=17.5, n_symbols=1000, sample_phase=None,
                        ber=SimpleNamespace(ber=5e-4, n_errors=1, n_checked=2000),
                        ffe_taps=np.array([0.1, 1.0]), dfe_taps=np.zeros(0), y_slicer=None,
                        extras={"lock": True, "trace": np.arange(3.0), "skip": [1, 2]})
    v = fp.result_values(r)
    assert v["ser"] == "0.001" and v["ber.n_checked"] == "2000" and v["ffe_taps.n"] == 2
    assert "dfe_taps" not in v and "sample_phase" not in v and "x.skip" not in v
    assert v["x.lock"] == "True" and v["x.trace"] == fp.digest(np.arange(3.0))
    # the statistical engine's BER is a bare float
    assert fp.result_values(SimpleNamespace(ber=2e-5))["ber"] == "2e-05"


def test_raised_entries_are_listed_not_hidden(fp, tmp_path, capsys):
    # both sides failing the same way compares equal; the tool must still say so
    side = {"presets": {"p": {"com": {"error": "TypeError: bad call"}, "time": {"ser": "0.0"}},
                        "defaults": {"channel_error": "ValueError"}}}
    assert fp.raised(side) == ["presets / defaults: ValueError",
                               "presets / p / com: TypeError: bad call"]
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(side))
    b.write_text(json.dumps(side))
    assert fp.main(["compare", str(a), str(b)]) == 0
    out = capsys.readouterr().out
    assert "bit-identical" in out and "2 entries raised" in out and "TypeError: bad call" in out
