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

What it cannot see by itself: numbers the docs derive (a difference of two
printed values, a ratio, a sum of rounded rows), and a quote too short to be
told from any other number outside a block that names the example. Those are
registered in ``examples/derived.yaml``: where each operand is printed, and
the doc text that quotes the result. ``compare`` re-derives them from the new
outputs and lists the quotes that went stale; ``derived`` (and the test suite)
checks every registered quote against ``examples/expected/``, so an ``accept``
that leaves one behind fails the tests. An unregistered one stays invisible;
``candidates`` lists the doc lines that state a difference or a ratio ("多
2.3 dB", "1.5×") no cited example prints, to be read and registered (most of
what it lists is history or a separate measurement). CI's ``examples-full``
workflow runs the comparison on every change to the library or the examples,
and weekly (dependency updates move numbers too).

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

import numpy as np

REPO = Path(__file__).resolve().parent.parent
EXPECTED = REPO / "examples" / "expected"
DERIVED = REPO / "examples" / "derived.yaml"

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


# --- derived numbers ---------------------------------------------------------
#
# examples/derived.yaml, a list of entries:
#
#   - name: ex19 DSP depth lever
#     values:                       # operands, each read from one output line
#       ffe: {example: "19", line: "FFE + MLSD mem2 (example 18): {} dB"}
#       dfe: {example: "19", line: "FFE + DFE8 + MLSD mem3: {} dB"}
#     quotes:                       # doc text; {expr} marks the quoted number
#       - {doc: docs/SUMMARY.md, text: "只多 {dfe - ffe} dB({ffe} → {dfe} dB)"}
#
# In ``line``, ``{}`` is the number read and ``{*}`` any number skipped; the
# pattern must match exactly one line of the example's output, or with
# ``after`` the first line below the one line that pattern matches. With
# ``each`` the operand is every match of that pattern within the line (a list:
# ``x[0]``, ``x[-1]``, ``min(x - y)``). In a quote, ``{expr}`` is an arithmetic
# expression over the operands (+ - * /, min, max, abs, sum, log10, indexing;
# ``max(a - b, c - d)`` takes the largest of several),
# ``{*}`` any number, and ``{expr |upper}`` / ``{expr |lower}`` a quote that
# bounds the value ("under 1 dB") instead of rounding it. Whitespace matches
# loosely and HTML tags are ignored on both sides; every place the text occurs
# must hold, and a quote found nowhere is reported (the doc was reworded).

_ONUM = r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
_DNUM = r"[-+−]?\d+(?:\.\d+)?(?:[eE][-+−]?\d+)?"
_HOLE = re.compile(r"\{([^{}]*)\}")


def _flat(text: str) -> str:
    return " ".join(_TAG.sub(" ", text).split())


def _lit(text: str, gap: str) -> str:
    """Literal text, its inner whitespace matching ``gap`` and its edges any."""
    return r"\s*" + gap.join(map(re.escape, text.split())) + r"\s*" if text.strip() else r"\s*"


def _line_pattern(text: str) -> re.Pattern:
    """An output-line pattern: ``{}`` captures a number, ``{*}`` skips one."""
    out, pos = [], 0
    for m in _HOLE.finditer(text):
        out.append(_lit(text[pos:m.start()], r"\s+"))
        out.append(f"({_ONUM})" if m.group(1) == "" else _ONUM)
        pos = m.end()
    out.append(_lit(text[pos:], r"\s+"))
    return re.compile("".join(out))


def _quote_pattern(text: str) -> tuple[re.Pattern, list[str]]:
    """A doc-quote pattern and the expression of each captured number."""
    out, exprs, pos = [], [], 0
    for m in _HOLE.finditer(text):
        out.append(_lit(text[pos:m.start()], r"\s*"))
        body = m.group(1).strip()
        if body == "*":
            out.append(_DNUM)
        else:
            out.append(rf"(?<![\d.])({_DNUM})(?![\d])")
            exprs.append(body)
        pos = m.end()
    out.append(_lit(text[pos:], r"\s*"))
    return re.compile("".join(out)), exprs


_FUNCS = {"min": np.min, "max": np.max, "abs": np.abs, "sum": np.sum, "log10": np.log10}
_OPS = {ast.Add: np.add, ast.Sub: np.subtract, ast.Mult: np.multiply, ast.Div: np.divide}


def evaluate(expr: str, env: dict):
    """The value of an arithmetic expression over named operands (no Python
    beyond numbers, names, + - * /, indexing and the functions in ``_FUNCS``:
    the registry is data, and must not be able to run anything)."""
    def ev(n):
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            return -ev(n.operand) if isinstance(n.op, ast.USub) else ev(n.operand)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.Name) and n.id in env:
            return env[n.id]
        if isinstance(n, ast.Subscript):
            return ev(n.value)[int(ev(n.slice))]
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS
                and n.args and not n.keywords):
            args = [ev(a) for a in n.args]
            return _FUNCS[n.func.id](args[0] if len(args) == 1 else np.array(args))
        raise ValueError(f"not allowed in a derived expression: {ast.unparse(n)!r}")
    return ev(ast.parse(expr, mode="eval").body)


@dataclass
class Derived:
    name: str
    values: dict
    quotes: list

    def operands(self, outputs: dict[str, str]) -> dict:
        """Each operand read from ``outputs`` (example name -> normalised text).
        Raises ValueError when a line is missing or ambiguous."""
        env = {}
        for key, src in self.values.items():
            ex = str(src["example"])
            text = next((t for n, t in outputs.items() if n.startswith(ex + "_")), None)
            if text is None:
                raise ValueError(f"{self.name}: no output for example {ex}")
            lines = text.splitlines()
            if "after" in src:
                anchor = _line_pattern(src["after"])
                at = [i for i, ln in enumerate(lines) if anchor.search(ln)]
                if len(at) != 1:
                    raise ValueError(f"{self.name}: {key}: {src['after']!r} matches {len(at)} lines of example {ex}")
                lines = lines[at[0] + 1:]
            rx = _line_pattern(src["line"])
            hits = [ln for ln in lines if rx.search(ln)]
            if "after" in src:
                hits = hits[:1]
            if len(hits) != 1:
                raise ValueError(f"{self.name}: {key}: {src['line']!r} matches {len(hits)} lines of example {ex}")
            if "each" in src:
                found = _line_pattern(src["each"]).findall(hits[0])
                if not found:
                    raise ValueError(f"{self.name}: {key}: {src['each']!r} not in {hits[0].strip()!r}")
                env[key] = np.array([float(t) for t in found])
            else:
                env[key] = float(rx.search(hits[0]).group(1))
        return env


def load_derived(path: Path = DERIVED) -> list[Derived]:
    import yaml

    if not path.exists():
        return []
    return [Derived(e["name"], e["values"], e["quotes"]) for e in yaml.safe_load(path.read_text(encoding="utf-8")) or []]


def _holds(tok: str, value: float, bound: str | None) -> bool:
    q = abs(float(tok.replace("−", "-")))
    if bound == "upper":
        return abs(value) <= q
    if bound == "lower":
        return abs(value) >= q
    return rounds_to(tok.replace("−", "-"), value)


def check_derived(entries: list[Derived], outputs: dict[str, str], repo: Path = REPO):
    """Every registered quote against the operands in ``outputs``: a list of
    (entry, doc path, line number, quoted, value) for the ones that do not
    hold -- value None for a quote not found -- and the entries whose
    operands could not be read, as (entry, message)."""
    bad, broken = [], []
    texts: dict[str, list[str]] = {}
    for e in entries:
        try:
            env = e.operands(outputs)
        except ValueError as exc:
            broken.append((e, str(exc)))
            continue
        for q in e.quotes:
            doc = q["doc"]
            if doc not in texts:
                raw = (repo / doc).read_text(encoding="utf-8").splitlines()
                texts[doc] = [_flat(ln) if len(ln) < 3000 else "" for ln in raw]  # skip inlined figures
            rx, exprs = _quote_pattern(q["text"])
            found = False
            for i, ln in enumerate(texts[doc], 1):
                for m in rx.finditer(ln):
                    found = True
                    for tok, expr in zip(m.groups(), exprs):
                        body, _, bound = (s.strip() for s in expr.partition("|"))
                        value = float(evaluate(body, env))
                        if not _holds(tok, value, bound or None):
                            bad.append((e, doc, i, tok, value))
            if not found:
                bad.append((e, doc, 0, q["text"], None))
    return bad, broken


def _outputs(directory: Path) -> dict[str, str]:
    return {p.stem: normalise(p.read_text(encoding="utf-8")) for p in directory.glob("*.txt")}


def report_derived(outputs: dict[str, str], repo: Path = REPO, entries=None, out=None) -> int:
    """Print the registered quotes that do not hold for ``outputs``; how many."""
    out = out or sys.stdout
    entries = load_derived() if entries is None else entries
    bad, broken = check_derived(entries, outputs, repo)
    for e, msg in broken:
        print(f"  derived: {msg}", file=out)
    for e, doc, ln, tok, value in bad:
        if value is None:
            print(f"  derived: {e.name}: {doc}: quote {tok!r} not found", file=out)
        else:
            print(f"  derived: {e.name}: {doc}:{ln}  '{tok}'  (now {value:.4g})", file=out)
    return len(bad) + len(broken)


# A number stated as a comparison: "多 2.3 dB", "差 0.03 dB", "+0.34 dB",
# "+81 m", "1.5×", "31 倍" (after _plain, so "−" is already "-"). A bare
# "-18.2 dB" is left out: in these docs that is a loss or a level (RIN, SNR).
_COMPARE = re.compile(
    r"(?:多|差|省|掉|亏|赚|高出?|低|再加|相差|代价|buys?|more|apart|gains?|costs?|saves?|penalty|better|worse)"
    r"\s*(?:约|~|≈)?\s*[+-]?(\d+(?:\.\d+)?)\s*(?:dB|m\b|倍|×|x\b)"
    r"|(?<![\w.])\+(\d+(?:\.\d+)?)\s*(?:dB|m\b)"
    r"|(?<![\w.])(\d+(?:\.\d+)?)\s*(?:×|倍)", re.I)


def candidates(repo: Path = REPO, expected: Path = EXPECTED, docs=(), strict: bool = False):
    """Doc lines stating a difference or a ratio ("多 2.3 dB", "+0.34 dB",
    "1.5×") that no example they cite prints, on lines no quote in
    ``examples/derived.yaml`` covers: a list of (doc, line, phrases, examples).
    Unregistered derived numbers are among them; most of the rest are history,
    separate measurements and settings, which only reading tells apart. A
    printed number that rounds to the quote by chance hides it ("约 0.3 dB"
    beside a printed 0.30); ``strict`` wants the quoted digits printed as they
    are, at the price of listing every rounded quote too."""
    by_ex = {name[:2]: text for name, text in _outputs(expected).items()}
    printed = {ex: [float(t) for t in NUM.findall(text)] for ex, text in by_ex.items()}
    covered, flat = set(), {}
    for e in load_derived(repo / DERIVED.relative_to(REPO)):
        for q in e.quotes:
            doc = q["doc"]
            if doc not in flat:
                raw = (repo / doc).read_text(encoding="utf-8").splitlines() if (repo / doc).exists() else []
                flat[doc] = [_flat(ln) if len(ln) < 3000 else "" for ln in raw]  # skip inlined figures
            rx, _ = _quote_pattern(q["text"])
            covered.update((doc, i) for i, ln in enumerate(flat[doc], 1) if rx.search(ln))
    cites = {}
    for d in all_doc_numbers(repo):
        if not docs or d.path in docs:
            cites[(d.path, d.lineno)] = (d.block or d.section) & by_ex.keys()
    texts, out = {}, []
    for (doc, ln), exs in sorted(cites.items()):
        if not exs or (doc, ln) in covered:
            continue
        if doc not in texts:
            src = (repo / doc).read_text(encoding="utf-8")
            texts[doc] = _prose_lines(src) if doc.endswith(".py") else src.splitlines()
        phrases = []
        for m in _COMPARE.finditer(_plain(texts[doc][ln - 1])):
            tok = next(g for g in m.groups() if g)
            if strict:
                seen = any(re.search(rf"(?<![\d.]){re.escape(tok)}(?!\d)", by_ex[e]) for e in exs)
            else:
                seen = any(rounds_to(tok, v) for e in exs for v in printed[e])
            if not seen:
                phrases.append(m.group(0).strip())
        if phrases:
            out.append((doc, ln, phrases, sorted(exs)))
    return out


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
    n_derived = 0
    entries = load_derived(repo / DERIVED.relative_to(REPO))
    if entries:
        # re-derived from the new outputs where there are some, the expected elsewhere
        print(f"\nderived numbers ({DERIVED.relative_to(REPO).as_posix()}), re-derived from {new}:", file=out)
        n_derived = report_derived({**_outputs(expected), **_outputs(new)}, repo, entries, out)
        if not n_derived:
            print("  every registered quote still holds", file=out)
    print(f"\n{moved} output(s) differ; {n_stale} doc number(s) quote a moved value; "
          f"{n_derived} derived quote(s) no longer hold; {unquoted} changed line(s) found in no doc", file=out)
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
    d = sub.add_parser("derived", help="check every quote in examples/derived.yaml; exit 1 on any that fails")
    d.add_argument("outputs", type=Path, nargs="?", default=EXPECTED,
                   help="outputs to derive from (default: the expected ones)")
    k = sub.add_parser("candidates", help="list stated differences / ratios that no cited example prints "
                                          "and no registered quote covers (to read, not to gate)")
    k.add_argument("docs", nargs="*", help="repo-relative documents (default: all)")
    k.add_argument("--strict", action="store_true",
                   help="a quote counts as printed only digit for digit, not by rounding")
    args = ap.parse_args(argv)
    if args.cmd == "candidates":
        found = candidates(REPO, EXPECTED, args.docs, args.strict)
        for doc, ln, phrases, exs in found:
            print(f"{doc}:{ln}  {' | '.join(phrases)}   (cites {', '.join(exs)})")
        print(f"{len(found)} line(s) to read: register the derived numbers among them in "
              f"{DERIVED.relative_to(REPO).as_posix()}")
        return 0
    if args.cmd == "compare":
        return compare(args.expected, args.new)
    if args.cmd == "derived":
        entries = load_derived()
        n = report_derived({**_outputs(EXPECTED), **_outputs(args.outputs)}, REPO, entries)
        n_quotes = sum(len(e.quotes) for e in entries)
        print(f"{len(entries)} derived number(s), {n_quotes} quote(s): "
              + (f"{n} problem(s)" if n else "all hold"))
        return 1 if n else 0
    names = accept(args.new, args.expected, args.only)
    print(f"wrote {len(names)} expected output(s) to {args.expected}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
