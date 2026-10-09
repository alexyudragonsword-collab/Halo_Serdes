"""Which documented numbers did a change move? Example outputs against the docs.

The docs quote what the examples print at their own sizes, and a model change
moves those numbers without failing anything: the 2026-10-08 full-size rerun
found about forty stale ones, one of them a conclusion (a "300x" that was
31x). ``examples/expected/<name>.txt`` keeps each example's full-size stdout
as of the last time the docs were checked against it. A change is compared
with that, line by line, and every number that moved is looked up in the docs:

    python tools/run_examples.py --jobs 4 --save new/    # full size, 10-20 min on 4 cores
    python tools/example_drift.py compare new/           # what moved; which doc lines quote it
    python tools/example_drift.py accept new/            # after the docs are fixed

``compare`` exits 1 if any output differs, whether or not a doc quotes it: the
expected files must match the code, or the next comparison mixes two changes.
For each moved number it lists the doc lines holding a number that rounds to
the old value and not to the new one -- a doc that says "4.7 dB" is not stale
when 4.66 becomes 4.71. The lookup is a heuristic and errs towards listing:

- a number in a block (paragraph or table) that cites the example, or in the
  docstrings and comments of the example's own file, or in a section
  (between headings) that cites it, at two or more significant digits;
- one anywhere else, only at three or more ("1.5e-2" is some BER in half the
  documents).

One-digit numbers are never listed: a "4" rounds from anything in 3.5..4.5
and every block has some (the first real run, example 32 after the 2026-10-09
noise-kernel fix, listed 170 lines, most of them a lone digit).

Each listed line says which of the three it is. Expect some that are history
on purpose ("before the fix it read 3e-2"); the tool cannot tell those apart.

What it cannot see: numbers the docs derive (a difference of two printed
values, a ratio), and a quote too short to be told from any other number
outside a block that names the example. CI's ``examples-full`` workflow runs
the comparison on every change to the library or the examples, and weekly
(dependency updates move numbers too).

Timings (``[3.2s]``, ``in 33s``) are not seeded and depend on the machine,
and the figures are written under the checkout's absolute path; both are
masked on both sides. History (``CHANGELOG.md``, ``cairn/LOG.md``)
records what was true then and is not searched.
"""

from __future__ import annotations

import argparse
import ast
import bisect
import difflib
import io
import re
import sys
import tokenize
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EXPECTED = REPO / "examples" / "expected"

#: the documents that state current results (history is left alone)
DOC_GLOBS = ("README.md", "ROADMAP.md", "CONTRIBUTING.md", "docs/*.md", "docs/*.html",
             "cairn/*.md", "examples/[0-9][0-9]_*.py")
DOC_SKIP = ("cairn/LOG.md",)

# what differs between machines and runs without a change: timings, and the
# checkout's absolute path in the "wrote <figure>" lines
_MASKS = ((re.compile(r"\[\s*\d+(?:\.\d+)?\s*s\]"), "[~s]"),
          (re.compile(r"\bin \d+(?:\.\d+)?\s*s\b"), "in ~s"),
          (re.compile(r"(?<!\S)/\S*?/(?=examples/output\b)"), ""))

# A number: not glued to a preceding identifier or decimal point ("PAM4",
# "ffe15", "802.3"'s tail), units may follow ("24.2dB", "180m").
NUM = re.compile(r"(?<![\w.])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?(?![\d.]\d|\.\d)")
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_SUP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺", "0123456789-+")
_SCI_SUP = re.compile(r"(\d(?:\.\d+)?)\s*[×x]\s*10([⁻⁺]?[⁰¹²³⁴⁵⁶⁷⁸⁹]+)")
_POW_SUP = re.compile(r"(?<![\w.])10([⁻⁺]?[⁰¹²³⁴⁵⁶⁷⁸⁹]+)")
# "示例 36 / 38", "examples/32", "example 05 and 39", "32_lpo_vs_cpo.py"
_CITE = re.compile(r"(?:示例|[Ee]xamples?/?|\bex)\s*(\d{2})((?:\s*(?:/|、|,|，|和|与|and|or|或)\s*\d{2}\b)*)")
_CITE_FILE = re.compile(r"\b(\d{2})_[a-z]\w*\.py\b")
_HEADING = re.compile(r"^\s*(?:#{1,6}\s|<h[1-6]\b|<section\b)")
_TAG = re.compile(r"<[^>]+>")


def normalise(text: str) -> str:
    """The comparable form of an output: timings and the checkout's path
    masked, trailing blanks gone."""
    for rx, mask in _MASKS:
        text = rx.sub(mask, text)
    return "\n".join(ln.rstrip() for ln in text.rstrip().splitlines()) + "\n"


def _plain(line: str) -> str:
    """A doc line with what is not a quoted number taken out (dates, tags, the
    example numbers of citations) and superscript powers of ten written as
    exponents."""
    line = _TAG.sub(" ", _DATE.sub(" ", line))
    line = _CITE_FILE.sub(" ", _CITE.sub(" ", line)).replace("−", "-")
    line = _SCI_SUP.sub(lambda m: f"{m.group(1)}e{m.group(2).translate(_SUP)}", line)
    return _POW_SUP.sub(lambda m: f"1e{m.group(1).translate(_SUP)}", line)


def _sig(tok: str) -> int:
    """Significant digits a quoted number carries ("0.005" one, "20.2" three,
    "100" one: an integer's trailing zeros say nothing about its precision)."""
    mant = re.split(r"[eE]", tok.lstrip("+-"))[0]
    digits = mant.replace(".", "").lstrip("0")
    return len(digits if "." in mant else digits.rstrip("0"))


def _half_ulp(tok: str) -> float:
    mant, _, exp = tok.lstrip("+-").lower().partition("e")
    dec = len(mant.split(".")[1]) if "." in mant else 0
    return 0.5 * 10.0 ** (int(exp or 0) - dec)


def rounds_to(tok: str, value: float) -> bool:
    """Whether the quoted ``tok`` is ``value`` at the precision it is quoted to.
    Signs are ignored: the docs say "a loss of 22.7 dB" for a printed -22.7."""
    return abs(abs(float(tok)) - abs(value)) <= _half_ulp(tok) * (1 + 1e-9)


@dataclass
class Change:
    """One output line that moved, and its numbers as (old, new) pairs; new is
    None when the line gained or lost numbers and they cannot be paired. A line
    only added has no old side, one only removed no new side."""
    example: str
    old_line: str | None
    new_line: str | None
    pairs: list = field(default_factory=list)


def changes(example: str, old: str, new: str) -> list[Change]:
    """Line-level diff of two normalised outputs, numbers paired where they can be."""
    a, b = old.splitlines(), new.splitlines()
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if tag == "insert":
            # moved nothing a doc could quote, but the expected file is out of date
            out.extend(Change(example, None, ln) for ln in b[j1:j2])
            continue
        news = b[j1:j2] if tag == "replace" else []
        for k, line in enumerate(a[i1:i2]):
            nl = news[k] if k < len(news) else None
            ot = NUM.findall(line)
            nt = NUM.findall(nl) if nl is not None else []
            if nl is not None and len(ot) == len(nt):
                # same numbers in the same order, whatever the words around
                # them did (a relabelled line moved none of its numbers)
                pairs = [(o, n) for o, n in zip(ot, nt) if o != n]
            else:
                pairs = [(o, None) for o in ot]
            out.append(Change(example, line, nl, pairs))
    return out


@dataclass
class DocNumber:
    path: str
    lineno: int
    tok: str
    block: frozenset   # examples cited in the same block (or the example's own file)
    section: frozenset  # ... in the same section


def _cited(text: str) -> set[str]:
    out = set(_CITE_FILE.findall(text))
    for m in _CITE.finditer(text):
        out.add(m.group(1))
        out.update(re.findall(r"\d{2}", m.group(2)))
    return out


def _prose_lines(source: str) -> list[str]:
    """An example script with only its docstrings and comments left (the rest
    blanked, line numbers kept): its code is full of numbers that are settings,
    not results."""
    lines = source.splitlines()
    keep = [""] * len(lines)
    for node in ast.walk(ast.parse(source)):
        body = getattr(node, "body", None)
        if (isinstance(body, list) and body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str)):
            for i in range(body[0].lineno - 1, body[0].end_lineno):
                keep[i] = lines[i]
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            i = tok.start[0] - 1
            keep[i] = (keep[i] + " " + tok.string).strip()
    return keep


def doc_numbers(path: Path, rel: str) -> list[DocNumber]:
    text = path.read_text(encoding="utf-8")
    lines = _prose_lines(text) if path.suffix == ".py" else text.splitlines()
    own = {rel.split("/")[-1][:2]} if rel.startswith("examples/") else set()
    blocks, sections = [0] * len(lines), [0] * len(lines)
    b = s = 0
    for i, ln in enumerate(lines):
        if not ln.strip():
            b += 1
        if _HEADING.match(ln):
            s += 1
            b += 1
        blocks[i], sections[i] = b, s
    bcite, scite = {}, {}
    for i, ln in enumerate(lines):
        c = _cited(ln)
        bcite.setdefault(blocks[i], set()).update(c)
        scite.setdefault(sections[i], set()).update(c)
    out = []
    for i, ln in enumerate(lines):
        bl = frozenset(bcite[blocks[i]] | own)
        se = frozenset(scite[sections[i]] | own)
        for tok in NUM.findall(_plain(ln)):
            if float(tok) != 0.0:
                out.append(DocNumber(rel, i + 1, tok, bl, se))
    return out


def all_doc_numbers(repo: Path = REPO) -> list[DocNumber]:
    out, seen = [], set()
    for g in DOC_GLOBS:
        for p in sorted(repo.glob(g)):
            rel = p.relative_to(repo).as_posix()
            if rel in DOC_SKIP or rel in seen:
                continue
            seen.add(rel)
            out.extend(doc_numbers(p, rel))
    return out


def _scope(d: DocNumber, example: str) -> str | None:
    """Where a doc number this precise could be a quote of ``example``: "block",
    "section", "elsewhere", or None when it is too short to tell from any other
    number there."""
    if _sig(d.tok) < 2:
        return None
    if example in d.block:
        return "block"
    if example in d.section:
        return "section"
    return "elsewhere" if _sig(d.tok) >= 3 else None


class DocIndex:
    """The doc numbers sorted by magnitude. A quote rounds to a value only within
    half its last digit, which is at most half its own magnitude, so a value v
    can only be quoted by numbers in [|v| / 1.5, 2 |v|]."""

    def __init__(self, docs: list[DocNumber]):
        self.docs = sorted(docs, key=lambda d: abs(float(d.tok)))
        self.mag = [abs(float(d.tok)) for d in self.docs]

    def near(self, v: float) -> list[DocNumber]:
        v = abs(v)
        return self.docs[bisect.bisect_left(self.mag, v / 1.5):bisect.bisect_right(self.mag, 2 * v)]


def stale(change: Change, index: DocIndex) -> list[tuple[DocNumber, str, str | None, str]]:
    """The doc numbers that quote a value this change moved:
    (doc number, old, new, scope)."""
    ex = change.example[:2]
    hits = []
    for o, n in change.pairs:
        ov = float(o)
        nv = float(n) if n is not None else None
        for d in index.near(ov):
            if rounds_to(d.tok, ov) and not (nv is not None and rounds_to(d.tok, nv)):
                where = _scope(d, ex)
                if where is not None:
                    hits.append((d, o, n, where))
    return hits


def compare(expected: Path, new: Path, repo: Path = REPO, out=None) -> int:
    """Print what moved and where the docs quote it; 1 if anything differs.
    Only the examples in ``new`` are compared, so a run of a few is enough to
    check those few."""
    out = out or sys.stdout
    exp = {p.stem: p for p in expected.glob("*.txt")}
    got = {p.stem: p for p in new.glob("*.txt")}
    docs = None
    moved, n_stale, unquoted = 0, 0, 0
    not_run = sorted(exp.keys() - got.keys())
    if not_run:
        names = ", ".join(not_run) if len(not_run) <= 5 else f"{len(not_run)} examples"
        print(f"not in {new}, not compared: {names}", file=out)
    for name in sorted(got):
        if name not in exp:
            print(f"{name}: no expected output -- run `accept` once it is checked", file=out)
            moved += 1
            continue
        a = normalise(exp[name].read_text(encoding="utf-8"))
        b = normalise(got[name].read_text(encoding="utf-8"))
        if a == b:
            continue
        moved += 1
        docs = docs if docs is not None else DocIndex(all_doc_numbers(repo))
        chs = changes(name, a, b)
        print(f"\n{name}: {len(chs)} line(s) changed", file=out)
        for ch in chs:
            if ch.old_line is not None:
                print(f"  - {ch.old_line.strip()}", file=out)
            print(f"  + {ch.new_line.strip()}" if ch.new_line is not None else "  + (gone)", file=out)
            if ch.old_line is None:
                continue
            hits = stale(ch, docs)
            for d, o, nn, where in hits:
                print(f"      {d.path}:{d.lineno}  '{d.tok}'  ({o} -> {nn if nn is not None else '?'}; "
                      f"{where})", file=out)
            n_stale += len(hits)
            unquoted += not hits
    if not moved:
        print(f"{len(got)} output(s) match {expected}", file=out)
        return 0
    print(f"\n{moved} output(s) differ; {n_stale} doc number(s) quote a moved value; "
          f"{unquoted} changed line(s) found in no doc", file=out)
    return 1


def accept(new: Path, expected: Path, only=()) -> list[str]:
    """Copy (normalised) outputs into the expected set; returns the names written."""
    expected.mkdir(parents=True, exist_ok=True)
    done = []
    for p in sorted(new.glob("*.txt")):
        if only and not any(p.stem.startswith(o) for o in only):
            continue
        (expected / p.name).write_text(normalise(p.read_text(encoding="utf-8")), encoding="utf-8")
        done.append(p.stem)
    return done


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compare", help="diff new outputs with the expected ones; exit 1 on any")
    c.add_argument("new", type=Path, help="directory from run_examples.py --save")
    c.add_argument("--expected", type=Path, default=EXPECTED)
    a = sub.add_parser("accept", help="make new outputs the expected ones")
    a.add_argument("new", type=Path)
    a.add_argument("only", nargs="*", help="number prefixes (default: all)")
    a.add_argument("--expected", type=Path, default=EXPECTED)
    args = ap.parse_args(argv)
    if args.cmd == "compare":
        return compare(args.expected, args.new)
    names = accept(args.new, args.expected, args.only)
    print(f"wrote {len(names)} expected output(s) to {args.expected}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
