"""Finding where the data starts: title rows, the row of column names, and the names.

Spreadsheet exports often have a title above the table, column names split over two rows
(a group name over several columns), or no column names at all. This module works out
which row holds the names by comparing it with the rows below: a name sits above numbers,
a data row does not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from data_storyteller.values import read_date, read_number

WINDOW = 50  # rows below a candidate header that are compared with it


def is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def is_label(value: Any) -> bool:
    """Text that reads as neither a number nor a date: what a column name looks like."""
    if not isinstance(value, str) or not value.strip():
        return False
    return read_number(value) is None and read_date(value) is None


def looks_numeric(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int | float | datetime):
        return True
    return isinstance(value, str) and (
        read_number(value) is not None or read_date(value) is not None
    )


def _year_like(value: Any) -> bool:
    text = str(value).strip()
    return text.isdigit() and len(text) == 4 and 1900 <= int(text) <= 2100


@dataclass
class Layout:
    """Where the data is in a raw table, and what its columns are called."""

    names: list[str]
    source_names: list[str]  # as written in the file; "" where there was none
    first_data_row: int  # index into RawTable.rows
    header_row: int | None  # index of the row of names, None when the file has none
    title_rows: list[int] = field(default_factory=list)
    title_text: list[str] = field(default_factory=list)
    group_row: int | None = None  # names split over two rows: the upper row
    combined_names: list[str] = field(default_factory=list)  # names when the rows are combined
    renamed: list[tuple[str, str, str]] = field(default_factory=list)  # (was, now, why)
    combined_renamed: list[tuple[str, str, str]] = field(default_factory=list)


def header_votes(row: list[Any], below: list[list[Any]]) -> int:
    """Positive when ``row`` looks like column names for the rows below it."""
    votes = 0
    for j, cell in enumerate(row):
        values = [r[j] for r in below if j < len(r) and not is_empty(r[j])]
        if not values or is_empty(cell):
            continue
        numeric = sum(looks_numeric(v) for v in values) / len(values)
        if is_label(cell):
            if numeric >= 0.8:
                votes += 1
            elif str(cell).strip() in {str(v).strip() for v in values}:
                votes -= 1  # repeats in the column: a value, not a name
        elif looks_numeric(cell) and numeric >= 0.5 and not _year_like(cell):
            votes -= 1
    return votes


def find_layout(rows: list[list[Any]], known_header: list[str] | None = None) -> Layout:
    """Work out the layout of ``rows`` (see :class:`Layout`)."""
    if known_header is not None:
        return _named(list(known_header), [str(h) for h in known_header], 0, None)

    width = max((len(r) for r in rows), default=0)

    def filled(i: int) -> int:
        return sum(not is_empty(v) for v in rows[i]) if i < len(rows) else 0

    # Title rows: at most one filled cell (a report title, a date) above a wider table.
    start = 0
    while (
        start < min(10, len(rows) - 1)
        and filled(start) <= 1
        and (width >= 3 or filled(start) == 0)
        and any(filled(k) > 1 for k in range(start + 1, min(len(rows), start + 12)))
    ):
        start += 1
    titles = [i for i in range(start) if filled(i)]
    title_text = [" ".join(str(v).strip() for v in rows[i] if not is_empty(v)) for i in titles]

    def below(i: int) -> list[list[Any]]:
        return rows[i + 1 : i + 1 + WINDOW]

    header = start
    group: int | None = None
    # Names over two rows: a sparse row of group names above a full row of names.
    nxt = start + 1
    if (
        nxt < len(rows) - 1
        and 2 <= filled(start) < filled(nxt)
        and all(is_label(v) for v in rows[start] if not is_empty(v))
        and all(is_label(v) for v in rows[nxt] if not is_empty(v))
        and header_votes(rows[nxt], below(nxt)) > 0
    ):
        group, header = start, nxt

    row = rows[header] if header < len(rows) else []
    votes = header_votes(row, below(header))
    all_labels = bool(row) and all(is_label(v) for v in row if not is_empty(v)) and filled(header)
    if group is None and not (votes > 0 or (votes == 0 and all_labels)):
        # no column names in the file: the data starts at `start`
        names = [f"column_{j + 1}" for j in range(width)]
        layout = _named(names, [""] * width, start, None)
        layout.title_rows, layout.title_text = titles, title_text
        return layout

    written = [_text(row[j]) if j < len(row) else "" for j in range(width)]
    layout = _named(list(written), written, header + 1, header)
    layout.title_rows, layout.title_text = titles, title_text
    if group is not None:
        layout.group_row = group
        upper, current = rows[group], ""
        combined = []
        for j in range(width):
            if j < len(upper) and not is_empty(upper[j]):
                current = _text(upper[j])
            name = written[j]
            combined.append(f"{current} {name}".strip() if current and current != name else name)
        layout.combined_names, layout.combined_renamed = unique_names(combined)
    return layout


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value)).strip() if not is_empty(value) else ""


def _named(names: list[str], written: list[str], first: int, header: int | None) -> Layout:
    final, renamed = unique_names(names)
    return Layout(final, written, first, header, renamed=renamed)


def unique_names(names: list[str]) -> tuple[list[str], list[tuple[str, str, str]]]:
    """Give every column a name, and every name once (case-insensitively)."""
    taken: set[str] = set()
    final: list[str] = []
    renamed: list[tuple[str, str, str]] = []
    for j, raw in enumerate(names):
        name = _text(raw)
        why = ""
        if not name:
            name, why = f"column_{j + 1}", "had no name"
        elif name != raw:
            why = "extra spaces removed"
        base, n = name, 1
        while name.casefold() in taken:
            n += 1
            name, why = f"{base}_{n}", f"same name as an earlier column ({base})"
        taken.add(name.casefold())
        final.append(name)
        if why:
            renamed.append((raw, name, why))
    return final, renamed
