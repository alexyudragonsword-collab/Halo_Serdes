"""Documentation must not drift from the code.

The README once claimed "Phase 0 完成", 87 tests and 8 examples while the
project had 6 phases, 283 tests and 31 examples — the kind of rot nobody
notices because nothing fails. These checks pin the facts that are cheap to
verify mechanically: referenced files exist, and the counts quoted in prose
match reality.
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
README = REPO / "README.md"
DOCS = REPO / "docs"


def _n_examples() -> int:
    return len(list((REPO / "examples").glob("*.py")))


def _n_tabs() -> int:
    from halo_serdes_gui.app import PANELS
    return len(PANELS)


@pytest.mark.parametrize("doc", sorted(DOCS.glob("*.md")) + [README])
def test_internal_links_resolve(doc):
    """Every relative markdown link points at a file that exists."""
    text = doc.read_text()
    missing = []
    for target in re.findall(r"\]\(([^)#][^)]*)\)", text):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        path = (doc.parent / target.split("#")[0]).resolve()
        if not path.exists():
            missing.append(target)
    assert not missing, f"{doc.name} links to missing files: {missing}"


def test_readme_example_count_is_current():
    m = re.search(r"把 (\d+) 个示例脚本", README.read_text())
    assert m, "README no longer states an example count"
    assert int(m.group(1)) == _n_examples()


def test_readme_tab_count_is_current():
    text = README.read_text()
    counts = {int(x) for x in re.findall(r"共 (\d+) 个能力标签页", text)}
    counts |= {int(x) for x in re.findall(r"(\d+) 个标签页的图文导览", text)}
    assert counts, "README no longer states a GUI tab count"
    assert counts == {_n_tabs()}, (counts, _n_tabs())


def test_readme_does_not_claim_an_early_phase_is_current():
    """Guards the specific rot that happened: a stale '当前状态(Phase N 完成)'."""
    assert "当前状态(Phase 0 完成)" not in README.read_text()


def test_usage_guide_exists_and_covers_the_main_entry_points():
    usage = (DOCS / "USAGE.md").read_text()
    for api in ("run_time_link", "run_statistical", "run_static_link",
                "load_config", "apply_overrides", "ChannelModel",
                "aggressor_bank", "Com93a", "jitter_tolerance",
                "load_ami_model", "run_fixed_datapath"):
        assert api in usage, f"USAGE.md never mentions {api}"


def test_example_numbering_referenced_by_readme_exists():
    """README points at examples/00-NN; the upper bound must really be there."""
    m = re.search(r"`examples/00`–`(\d+)`", README.read_text())
    assert m, "README no longer states the example range"
    last = m.group(1)
    assert list((REPO / "examples").glob(f"{last}_*.py")), \
        f"README references examples/{last} which does not exist"
