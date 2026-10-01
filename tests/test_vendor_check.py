"""Vendor drift check: the vendored tree matches pinned upstream.

Runs the real ``tools/vendor_check.py`` logic. Skips when the sibling
repo (pll_simulator) is not checked out — the same graceful behaviour the
CI job relies on — so a bare checkout still passes; the ``vendor-drift`` CI
job checks the sibling out and passes ``--fail-on-skip`` so that there, a
skip is red. Adapted from polar_tx@bfa460c tests/test_vendor_check.py.

The sibling is looked for at ``/home/user/pll_simulator`` by default (the
tool's convention) and at ``$HALO_SIBLING_pll_simulator``; a read-only
clone made through the session's git proxy lands one directory deeper, so
that location is tried too.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "vendor_check", REPO / "tools" / "vendor_check.py")
vc = importlib.util.module_from_spec(_spec)
sys.modules["vendor_check"] = vc  # so dataclass can resolve annotations
_spec.loader.exec_module(vc)


def _siblings():
    roots = {}
    for cand in ("/home/user/alexyudragonsword-collab/pll_simulator",):
        if (Path(cand) / ".git").exists() and vc.Siblings({}).path("pll_simulator") is None:
            roots["pll_simulator"] = cand
    return vc.Siblings(roots)


def _have_siblings(sib):
    return sib.path("pll_simulator") is not None


def test_no_drift():
    sib = _siblings()
    if not _have_siblings(sib):
        pytest.skip("sibling repo pll_simulator not present")
    manifest = vc.json.loads(vc.MANIFEST.read_text()) if vc.MANIFEST.exists() else {}
    results = vc.check(sib, manifest)
    drift = [r for r in results if r.status == "DRIFT"]
    assert not drift, "vendor drift:\n" + "\n".join(
        f"  {r.rel}: {r.detail}" for r in drift)


def test_every_file_has_header():
    """Every vendored .py (bar __init__) carries an attribution header —
    this part needs no sibling checkout."""
    bad = []
    for f in sorted((REPO / "src/halo_serdes/vendor").rglob("*.py")):
        if f.name == "__init__.py":
            continue
        first = f.read_text().splitlines()[0]
        if not vc.HEADER.match(first):
            bad.append(str(f.relative_to(REPO)))
    assert not bad, "vendored files missing attribution header: " + ", ".join(bad)


def test_manifest_files_exist():
    """Manifest never references a vendored file that has been removed."""
    if not vc.MANIFEST.exists():
        pytest.skip("no manifest")
    declared = vc.json.loads(vc.MANIFEST.read_text()).get("files", {})
    missing = [rel for rel in declared
               if not (REPO / "src/halo_serdes/vendor" / rel).exists()]
    assert not missing, "stale manifest entries: " + ", ".join(missing)


def test_detects_injected_drift(tmp_path, monkeypatch):
    """A non-import edit to a verbatim file is reported as DRIFT."""
    sib = _siblings()
    if not _have_siblings(sib):
        pytest.skip("sibling repo pll_simulator not present")
    target = REPO / "src/halo_serdes/vendor/pllsim/jitter.py"
    original = target.read_text()
    try:
        target.write_text(original + "\nINJECTED_DRIFT = 1\n")
        manifest = vc.json.loads(vc.MANIFEST.read_text())
        results = vc.check(sib, manifest)
        drift = [r for r in results if r.status == "DRIFT"]
        assert any("jitter.py" in r.rel for r in drift)
    finally:
        target.write_text(original)
