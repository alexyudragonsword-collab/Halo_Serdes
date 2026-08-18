"""Documentation must not drift from the code.

The README once claimed "Phase 0 完成", 87 tests and 8 examples while the
project had 6 phases, 283 tests and 31 examples — the kind of rot nobody
notices because nothing fails. These checks pin the facts that are cheap to
verify mechanically: referenced files exist, and the counts quoted in prose
match reality.
"""

import re
import subprocess
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


def _is_generated_artifact(path: Path) -> bool:
    """True when git deliberately ignores the target (a build product).

    Figures under ``examples/output/`` are produced by running the examples and
    are gitignored on purpose, so they are absent in a fresh clone. Requiring
    them here would fail on every clean checkout while telling us nothing about
    link correctness — but a genuinely wrong path must still fail.
    """
    r = subprocess.run(["git", "check-ignore", "-q", str(path)],
                       cwd=REPO, capture_output=True)
    return r.returncode == 0


@pytest.mark.parametrize("doc", sorted(DOCS.glob("*.md")) + [README])
def test_internal_links_resolve(doc):
    """Every relative markdown link points at a file that exists (or is a
    deliberately-gitignored generated artifact)."""
    text = doc.read_text()
    missing = []
    for target in re.findall(r"\]\(([^)#][^)]*)\)", text):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        path = (doc.parent / target.split("#")[0]).resolve()
        if not path.exists() and not _is_generated_artifact(path):
            missing.append(target)
    assert not missing, f"{doc.name} links to missing files: {missing}"


def test_docs_do_not_silently_embed_uncommitted_figures():
    """A doc that inlines generated figures must say how to produce them.

    docs/SUMMARY.md embeds 8 PNGs from the gitignored examples/output/, so on
    GitHub — and in any fresh clone — they render as broken images. The figures
    stay out of the repo (existing artifact policy); the doc has to tell the
    reader that, rather than fail silently.
    """
    for doc in sorted(DOCS.glob("*.md")):
        text = doc.read_text()
        if "examples/output/" not in text:
            continue
        assert re.search(r"python examples/|运行.*示例|run the examples", text), (
            f"{doc.name} embeds generated figures but never says how to "
            f"generate them")


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
