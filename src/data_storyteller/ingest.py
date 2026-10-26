"""Reading a data file into raw tables, without interpreting any value.

Delimited text is read as text, cell by cell: pandas' usual guessing (``"01"`` becoming 1,
``"NA"`` becoming empty) would be cleaning nobody asked for. Formats that store real types
(Parquet, SQLite, JSON, numbers and dates in Excel) keep them.
"""

from __future__ import annotations

import codecs
import csv
import io
import json
import math
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import pandas as pd

MAX_FILE_BYTES = 500 * 1024 * 1024
DELIMITERS = (",", ";", "\t", "|")
TEXT_FORMATS = {".csv": "csv", ".tsv": "tsv", ".txt": "csv"}
EXCEL_FORMATS = {".xlsx", ".xlsm"}
SQLITE_FORMATS = {".db", ".sqlite", ".sqlite3"}


class IngestError(Exception):
    """The file cannot be read as a table."""


@dataclass
class RawTable:
    """One table as it is in the file.

    ``rows`` holds every row, including any title or header rows, unless ``header`` is
    known from the format itself (Parquet, SQLite, JSON): then ``rows`` holds only data.
    Values are text for delimited files, and Python values (str, int, float, bool,
    datetime, None) for typed formats.
    """

    name: str
    rows: list[list[Any]]
    header: list[str] | None = None

    @property
    def width(self) -> int:
        return max((len(r) for r in self.rows), default=len(self.header or []))


@dataclass
class Source:
    path: Path
    format: str
    tables: dict[str, RawTable]
    encoding: str | None = None
    delimiter: str | None = None
    bom: bool = False
    notes: list[str] = field(default_factory=list)


def read_source(path: str | Path) -> Source:
    """Read every table in the file at ``path``."""
    p = Path(path).expanduser()
    if not p.exists():
        raise IngestError(f"No file at {p}")
    if p.is_dir():
        raise IngestError(f"{p} is a folder; give the path of one data file")
    size = p.stat().st_size
    if size > MAX_FILE_BYTES:
        raise IngestError(
            f"{p.name} is {size / 1024**2:.0f} MB; the limit is {MAX_FILE_BYTES // 1024**2} MB"
        )
    suffix = p.suffix.lower()
    try:
        if suffix in TEXT_FORMATS:
            return _read_delimited(p, suffix)
        if suffix in EXCEL_FORMATS:
            return _read_excel(p)
        if suffix == ".xls":
            raise IngestError("Old .xls workbooks are not supported; save it as .xlsx or CSV")
        if suffix in {".parquet", ".pq"}:
            return _single(p, "parquet", _typed_table(p.stem, pd.read_parquet(p)))
        if suffix == ".json":
            return _read_json(p)
        if suffix in SQLITE_FORMATS:
            return _read_sqlite(p)
    except IngestError:
        raise
    except Exception as exc:  # noqa: BLE001 - a broken file of a supported kind
        raise IngestError(f"Cannot read {p.name}: {exc}") from exc
    raise IngestError(
        f"Unsupported file type {suffix or '(none)'}: use CSV, TSV, Excel (.xlsx), Parquet, "
        "JSON or SQLite"
    )


def _single(path: Path, fmt: str, table: RawTable, **kw: Any) -> Source:
    return Source(path=path, format=fmt, tables={table.name: table}, **kw)


# ---- delimited text ---------------------------------------------------------------------

BOMS = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


def decode(data: bytes) -> tuple[str, str, bool]:
    """Return the text, the encoding used and whether a byte-order mark was found."""
    for bom, encoding in BOMS:
        if data.startswith(bom):
            return data.decode(encoding), encoding, True
    for encoding in ("utf-8", "cp1252"):
        try:
            return data.decode(encoding), encoding, False
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1"), "latin-1", False  # never fails: every byte is a character


def sniff_delimiter(text: str) -> str:
    """The delimiter that splits the first lines into the most consistent number of fields."""
    lines = [line for line in text.splitlines()[:30] if line.strip()]
    best, best_score = ",", (0.0, 0)
    for delimiter in DELIMITERS:
        counts = [len(row) for row in csv.reader(lines, delimiter=delimiter)]
        if not counts or max(counts) < 2:
            continue
        common, times = Counter(counts).most_common(1)[0]
        score = (times / len(counts), common)
        if score > best_score:
            best, best_score = delimiter, score
    return best


def _read_delimited(path: Path, suffix: str) -> Source:
    text, encoding, bom = decode(path.read_bytes())
    delimiter = "\t" if suffix == ".tsv" else sniff_delimiter(text)
    rows = [row for row in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)]
    while rows and not any(cell.strip() for cell in rows[-1]):
        rows.pop()
    if not rows:
        raise IngestError(f"{path.name} is empty")
    notes = []
    if encoding not in ("utf-8", "utf-8-sig"):
        label = {"cp1252": "Windows-1252", "latin-1": "Latin-1", "utf-16": "UTF-16"}[encoding]
        notes.append(f"The file is not UTF-8; it was read as {label} text.")
    fmt = "tsv" if delimiter == "\t" else "csv"
    table = RawTable(path.stem, rows)
    return _single(path, fmt, table, encoding=encoding, delimiter=delimiter, bom=bom, notes=notes)


# ---- typed formats ----------------------------------------------------------------------


def plain(value: Any) -> Any:
    """A cell as a plain Python value: None for every kind of missing value."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if value is pd.NaT:
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if hasattr(value, "item") and not isinstance(value, str):  # numpy scalars
        return plain(value.item())
    return value


def _typed_table(name: str, frame: pd.DataFrame) -> RawTable:
    header = [str(c) for c in frame.columns]
    columns = [[plain(v) for v in frame[c].tolist()] for c in frame.columns]
    rows = [list(r) for r in zip(*columns, strict=True)] if columns else []
    return RawTable(name, rows, header)


def _read_excel(path: Path) -> Source:
    import openpyxl

    # read_only + data_only: cached values only; formulas and macros are never evaluated
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    tables: dict[str, RawTable] = {}
    try:
        for sheet in workbook.worksheets:
            rows = [[plain(v) for v in row] for row in sheet.iter_rows(values_only=True)]
            while rows and all(v is None or str(v).strip() == "" for v in rows[-1]):
                rows.pop()
            if rows:
                tables[sheet.title] = RawTable(sheet.title, rows)
    finally:
        workbook.close()
    if not tables:
        raise IngestError(f"{path.name} has no data in any sheet")
    return Source(path=path, format="xlsx", tables=tables)


def _read_json(path: Path) -> Source:
    text, encoding, bom = decode(path.read_bytes())
    data = json.loads(text)
    if isinstance(data, dict):
        lists = [v for v in data.values() if isinstance(v, list)]
        if len(lists) == 1 and all(isinstance(x, dict) for x in lists[0]):
            data = lists[0]  # {"data": [{...}, ...]}
    if isinstance(data, list) and all(isinstance(x, dict) for x in data):
        frame = pd.json_normalize(data)
    elif isinstance(data, dict) and all(isinstance(v, list) for v in data.values()):
        frame = pd.DataFrame(data)
    else:
        raise IngestError(f"{path.name} is JSON but not a table: expected a list of records")
    table = _typed_table(path.stem, frame)
    return _single(path, "json", table, encoding=encoding, bom=bom)


def _read_sqlite(path: Path) -> Source:
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        names = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                " ORDER BY name"
            )
        ]
        tables = {}
        for name in names:
            quoted = '"' + name.replace('"', '""') + '"'
            frame = pd.read_sql_query(f"SELECT * FROM {quoted}", connection)  # noqa: S608
            tables[name] = _typed_table(name, frame)
    finally:
        connection.close()
    if not tables:
        raise IngestError(f"{path.name} has no tables")
    return Source(path=path, format="sqlite", tables=tables)
