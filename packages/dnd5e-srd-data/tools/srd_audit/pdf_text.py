"""Each SRD record's text, cut from the pinned SRD 5.2.1 PDF.

The PDF's content streams do not keep reading order: pypdf's plain extraction, which
``tools/srd_index.py`` uses to find headings, can put the end of one column after text from
the other (Antilife Shell's last sentence comes after Antipathy/Sympathy's). Record text is
therefore read in layout mode, which keeps each character's position, and each page is read
as two columns, left then right, top to bottom.

A record's text runs from its heading to the next record's heading in the same category,
and stops early at a sidebar stat block (the summon forms printed inside the Spells
chapter), which belongs to no field of the record around it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache

from tools import srd_index

# The chapter after the Spells chapter: the last spell's text ends at its heading.
AFTER_SPELLS = "Rules Glossary"
# The Spells chapter's pages (1-based): the spell descriptions start on page 107.
SPELL_PAGES = range(104, 177)


@dataclass(frozen=True)
class RecordText:
    name: str
    page: int
    lines: tuple[str, ...]

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def flatten(text: str) -> str:
    """Join lines, rejoin words split at a line-end hyphen, and collapse whitespace.
    pypdf reads a soft line-end hyphen as " -" ("a suc -\\ncessful") or as a bare "-"
    ("Ema-\\nnation"), and both are dropped. A real hyphen is kept: after a digit or "foot"
    ("10-\\nfoot", "10-foot-\\nradius"), or before a digit or a capital ("Three-\\nQuarters")."""
    text = re.sub(r"(\w) +-\n\s*(\w)", r"\1\2", text)
    text = re.sub(r"(\d|\bfoot)-\n\s*", r"\1-", text)
    text = re.sub(r"([a-z])-\n\s*([a-z])", r"\1\2", text)
    text = re.sub(r"(\w)-\n\s*(\w)", r"\1-\2", text)
    return re.sub(r"\s+", " ", text).strip()


def gutter(rows: list[str]) -> int:
    """The character column between a page's two text columns: the one, in the middle
    third of the page, that the fewest rows have a character on."""
    width = max((len(row) for row in rows), default=0)
    if width < 3:
        return width

    def crossing(column: int) -> int:
        return sum(1 for row in rows if column < len(row) and row[column] != " ")

    return min(range(width // 3, 2 * width // 3), key=lambda c: (crossing(c), c))


def column_lines(layout: str) -> list[str]:
    """A layout-mode page's lines in reading order: the left column, then the right. The
    running head and page number are dropped."""
    rows = [row.rstrip() for row in srd_index.normalize_text(layout).split("\n")]
    split = gutter(rows)
    left = [row[:split].strip() for row in rows]
    right = [row[split:].strip() for row in rows]
    lines = [line for line in left + right if line]
    return [line for line in lines if srd_index.RUNNING_HEAD not in line and not line.isdigit()]


@cache
def layout_lines(first: int, last: int) -> tuple[tuple[int, str], ...]:
    """Pages ``first`` to ``last`` (1-based, inclusive) read column by column in layout
    mode, with their pages, after verifying the PDF pin."""
    from pypdf import PdfReader

    path, sha256 = srd_index.pinned_pdf()
    srd_index.verify_pdf(path, sha256)
    reader = PdfReader(str(path))
    return tuple(
        (number, line)
        for number in range(first, last + 1)
        for line in column_lines(reader.pages[number - 1].extract_text(extraction_mode="layout"))
    )


def spell_chapter_lines() -> tuple[tuple[int, str], ...]:
    """The Spells chapter's lines with their pages."""
    return layout_lines(SPELL_PAGES.start, SPELL_PAGES.stop - 1)


def spell_texts(
    lines: tuple[tuple[int, str], ...] | None = None,
) -> dict[str, RecordText]:
    """Each spell's text, keyed by its heading: the heading, the level and school line, the
    four header lines, and the description."""
    lines = spell_chapter_lines() if lines is None else lines
    starts: list[tuple[int, str, int]] = []
    for index, (page, line) in enumerate(lines):
        if index and srd_index.SPELL_TYPE.match(line):
            name = srd_index.fix_case(lines[index - 1][1])
            starts.append((index - 1, name, page))
    chapter_end = next(
        (
            index
            for index, (_, line) in enumerate(lines)
            if starts and index > starts[-1][0] and line == AFTER_SPELLS
        ),
        len(lines),
    )
    texts: dict[str, RecordText] = {}
    for position, (start, name, page) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else chapter_end
        body: list[str] = []
        for index in range(start, end):
            nxt = lines[index + 1][1] if index + 1 < end else ""
            if index > start + 2 and srd_index.STAT_BLOCK_TYPE.match(nxt):
                break
            body.append(lines[index][1])
        texts.setdefault(name, RecordText(name=name, page=page, lines=tuple(body)))
    return texts


@cache
def plain_lines(first: int, last: int) -> tuple[tuple[int, str], ...]:
    """Pages ``first`` to ``last`` (1-based, inclusive) in pypdf's plain extraction, the
    reading ``tools/srd_index.py`` uses, with their pages. Tables keep their row order here,
    where layout mode can cut a table's last column off at the gutter. A line keeps its
    leading space, which marks an indented paragraph."""
    from pypdf import PdfReader

    path, sha256 = srd_index.pinned_pdf()
    srd_index.verify_pdf(path, sha256)
    reader = PdfReader(str(path))
    pages = [
        srd_index.strip_running_head(srd_index.normalize_text(page.extract_text() or ""))
        for page in reader.pages[first - 1 : last]
    ]
    return tuple(
        (first + offset, line.rstrip())
        for offset, page in enumerate(pages)
        for line in page.split("\n")
    )
