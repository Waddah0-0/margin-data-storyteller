"""Cleaning a whole table: plan it, ask what is unclear, apply it, and check the result.

Planning reads the layout and every column, then looks for whole-row problems (empty rows,
total rows, duplicates) on a first, provisional clean. Applying repeats the clean with the
answers. The check at the end compares the raw table with the result, independently of
the steps: every value present in the file is either in the result or was left empty by
a step that says so, and every removed row is removed by exactly one step.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from margin_core import Answer, Choice, Evidence, Question

from data_storyteller.columns import (
    MAX_EXAMPLES,
    ColumnPlan,
    ColumnResult,
    Kind,
    StepRecord,
    decided,
    show,
)
from data_storyteller.ingest import RawTable
from data_storyteller.layout import Layout, find_layout, is_empty
from data_storyteller.names import id_like

TOTAL_LABEL = re.compile(
    r"^\s*(grand\s+total|sub-?\s?total|totals?|sum|الإجمالي|إجمالي|المجموع)\b", re.IGNORECASE
)
SUBTOTAL_LABEL = re.compile(r"^\s*sub-?\s?total", re.IGNORECASE)


class CleaningCheckError(Exception):
    """The result does not account for every value in the file (a bug, never expected)."""


@dataclass
class Plan:
    table: RawTable
    layout: Layout
    rows: list[int]  # file row number of each data row
    columns: list[ColumnPlan]
    questions: list[Question] = field(default_factory=list)


@dataclass
class Clean:
    """The cleaned table and everything needed to explain it."""

    frame: pd.DataFrame
    rows: list[int]  # file row number of each remaining row
    kinds: dict[str, Kind]
    units: dict[str, str]
    sources: dict[str, str]  # column -> its name in the file ("" for added columns)
    steps: list[StepRecord]
    notes: dict[str, list[str]]
    rows_in: int
    header_row: int | None
    title_text: list[str]
    check: list[str]  # what the final check verified


def plan(table: RawTable) -> Plan:
    layout = find_layout(table.rows, table.header)
    data = table.rows[layout.first_data_row :]
    offset = 2 if table.header is not None else layout.first_data_row + 1
    rows = [offset + i for i in range(len(data))]
    columns = [
        ColumnPlan.plan(name, [r[j] if j < len(r) else None for r in data], rows)
        for j, name in enumerate(layout.names)
    ]
    result = Plan(table, layout, rows, columns)

    if layout.group_row is not None:
        pairs = list(zip(layout.combined_names, layout.names, strict=True))
        differ = [(c, n) for c, n in pairs if c != n][:3]
        upper, lower = layout.group_row + 1, (layout.header_row or 0) + 1
        result.questions.append(
            Question(
                id="header",
                prompt="The column names are split over two rows. How should they be named?",
                detail=f"Row {upper} groups the names on row {lower}.",
                section="Layout",
                choices=[
                    Choice(
                        value="combined",
                        label="Combine both rows",
                        detail=", ".join(c for c, _ in differ),
                    ),
                    Choice(
                        value="lower",
                        label=f"Use row {lower} only",
                        detail=", ".join(n for _, n in differ),
                    ),
                ],
                default="combined",
            )
        )
    for col in columns:
        result.questions.extend(col.questions)

    # whole-row problems, found on a provisional clean with the suggested answers
    provisional = _apply_columns(result, _defaults(result.questions))
    frame, kinds = provisional
    totals = find_totals(frame, kinds)
    unsure = [i for i, confident in totals if not confident]
    if unsure:
        result.questions.append(
            Question(
                id="total_rows",
                prompt=f"Remove {_rows_text([rows[i] for i in unsure])}? They look like totals.",
                detail="A total row counted as data would double every sum.",
                section="Rows",
                choices=[
                    Choice(value="remove", label="Remove them"),
                    Choice(value="keep", label="Keep them as data"),
                ],
                default="remove",
                evidence=[
                    Evidence(kind="row", location=f"row {rows[i]}", detail=_row_text(frame, i))
                    for i in unsure[:MAX_EXAMPLES]
                ],
            )
        )
    duplicates = find_duplicates(frame.drop(index=[i for i, _ in totals]))
    if duplicates and not has_id(frame):
        copies = [i for i, _ in duplicates]
        result.questions.append(
            Question(
                id="duplicates",
                prompt=f"Remove {len(copies):,} repeated row{'s' if len(copies) != 1 else ''}?",
                detail=(
                    "These rows are exact copies of earlier rows. With no id column, they "
                    "could be two real, identical records."
                ),
                section="Rows",
                choices=[
                    Choice(value="remove", label="Remove the copies"),
                    Choice(value="keep", label="Keep them; they are separate records"),
                ],
                default="remove",
                evidence=[
                    Evidence(
                        kind="row",
                        location=f"row {rows[i]}",
                        detail=f"Same as row {rows[first]}: {_row_text(frame, i)}",
                    )
                    for i, first in duplicates[:MAX_EXAMPLES]
                ],
            )
        )
    return result


def _defaults(questions: list[Question]) -> dict[str, Answer]:
    return {q.id: Answer.of(q, q.default, "default") for q in questions}


def _rows_text(rows: list[int]) -> str:
    if len(rows) == 1:
        return f"row {rows[0]}"
    return f"rows {', '.join(map(str, rows[:-1]))} and {rows[-1]}"


def _row_text(frame: pd.DataFrame, i: int) -> str:
    cells = [f"{c}={show(v)}" for c, v in frame.loc[i].items() if v is not None and not pd.isna(v)]
    text = ", ".join(cells[:8])
    return text + (", …" if len(cells) > 8 else "")


def _apply_columns(p: Plan, answers: dict[str, Answer]) -> tuple[pd.DataFrame, dict[str, Kind]]:
    assembled = _assemble(p, [c.apply(answers) for c in p.columns], answers)
    return assembled.frame, assembled.kinds


@dataclass
class _Assembled:
    frame: pd.DataFrame
    kinds: dict[str, Kind]
    sources: dict[str, str]  # column -> its name in the file ("" for added columns)
    names: list[str]  # the final name of each planned column, in plan order


def _assemble(p: Plan, results: list[ColumnResult], answers: dict[str, Answer]) -> _Assembled:
    names = p.layout.names
    if "header" in answers and answers["header"].value == "combined":
        names = p.layout.combined_names
    data: dict[str, Any] = {}
    kinds: dict[str, Kind] = {}
    sources: dict[str, str] = {}
    for name, written, result in zip(names, p.layout.source_names, results, strict=True):
        data[name] = _series(result.values, result.kind)
        kinds[name], sources[name] = result.kind, written or name
        for extra, values in result.extra.items():
            data[extra] = _series(values, "text")
            kinds[extra], sources[extra] = "text", ""
    frame = pd.DataFrame(data, index=range(len(p.rows)))
    return _Assembled(frame, kinds, sources, list(names))


def _series(values: list[Any], kind: Kind) -> pd.Series:
    if kind == "number":
        if all(v is None or isinstance(v, int) for v in values):
            return pd.Series(values, dtype="Int64")
        return pd.Series([float("nan") if v is None else v for v in values], dtype="float64")
    if kind == "date":
        return pd.to_datetime(pd.Series(values, dtype="object"))
    return pd.Series(values, dtype="object")


def find_totals(frame: pd.DataFrame, kinds: dict[str, Kind]) -> list[tuple[int, bool]]:
    """Rows that look like totals: (index, confident). Confident means labelled as a total
    and every number in it equals the sum of the rows it would total.

    Only the last rows, and rows labelled as subtotals, can be totals, so only those are
    looked at: the rest of a large table is never walked row by row.
    """
    if len(frame) < 3:
        return []
    ids = [c for c in frame.columns if id_like(str(c))]
    numeric = [c for c, k in kinds.items() if k == "number" and c in frame and c not in ids]
    texts = [c for c, k in kinds.items() if k == "text" and c in frame]
    filled = frame.index[frame.notna().any(axis=1)].tolist()
    candidates = set(filled[-3:])
    for c in texts:
        written = frame[c].dropna().astype(str)
        candidates |= set(written.index[written.str.match(SUBTOTAL_LABEL)].tolist())
    found: list[tuple[int, bool]] = []
    for i in sorted(candidates):
        if any(pd.notna(frame.at[i, c]) for c in ids):
            continue  # a row with a record id is a record
        labels = [str(frame.at[i, c]) for c in texts if pd.notna(frame.at[i, c])]
        labelled = any(TOTAL_LABEL.match(t) for t in labels)
        last = i in filled[-3:]
        subtotal = any(SUBTOTAL_LABEL.match(t) for t in labels)
        # unlabelled: only the last row, only where the label cells exist and are blank
        unlabelled = i == filled[-1] and bool(texts) and not labels
        if not (labelled and (last or subtotal)) and not unlabelled:
            continue
        others = frame.drop(index=[i, *[j for j, _ in found]])
        sums = [
            _close(frame.at[i, c], others[c].sum()) for c in numeric if pd.notna(frame.at[i, c])
        ]
        if not sums:
            continue
        if labelled:
            found.append((i, all(sums) and not subtotal))
        elif all(sums) and len(sums) >= 1:
            found.append((i, False))  # an unlabelled last row equal to the sums above
    return found


def _close(a: Any, b: Any) -> bool:
    x, y = float(a), float(b)
    return abs(x - y) <= 1e-9 * max(1.0, abs(x), abs(y)) + 0.005


def find_duplicates(frame: pd.DataFrame) -> list[tuple[int, int]]:
    """(copy, first occurrence) for every row that repeats an earlier row exactly."""
    if frame.empty:
        return []
    columns = [[None if pd.isna(v) else v for v in frame[c].tolist()] for c in frame.columns]
    first: dict[tuple[Any, ...], int] = {}
    pairs = []
    for i, key in zip(frame.index, zip(*columns, strict=True), strict=True):
        if key in first:
            pairs.append((int(i), first[key]))
        else:
            first[key] = int(i)
    return pairs


def has_id(frame: pd.DataFrame) -> bool:
    """Whether a column looks like a record id: named like one, and unique once exact
    copies of rows are left out."""
    frame = frame.drop(index=[i for i, _ in find_duplicates(frame)])
    for column in frame.columns:
        if id_like(str(column)):
            values = frame[column].dropna()
            if len(values) and values.nunique() >= 0.9 * len(values):
                return True
    return False


# ---- applying ---------------------------------------------------------------------------


def apply(p: Plan, answers: dict[str, Answer]) -> Clean:
    results = [c.apply(answers) for c in p.columns]
    assembled = _assemble(p, results, answers)
    frame, kinds, sources = assembled.frame, assembled.kinds, assembled.sources
    final = dict(zip(p.layout.names, assembled.names, strict=True))  # plan name -> final name
    units = {final[c.name]: r.unit for c, r in zip(p.columns, results, strict=True) if r.unit}
    notes = {final[c.name]: r.notes for c, r in zip(p.columns, results, strict=True) if r.notes}
    steps: list[StepRecord] = _layout_steps(p, answers)
    for result in results:
        for step in result.steps:
            step.column = final.get(step.column or "", step.column)
        steps.extend(result.steps)

    # empty columns: nothing left in them
    empty = [c for c in frame.columns if frame[c].isna().all()]
    if empty:
        steps.append(
            StepRecord(
                id="empty_columns",
                kind="empty_columns",
                summary=f"Dropped {len(empty)} empty column{'s' if len(empty) != 1 else ''}: "
                + ", ".join(empty[:10])
                + (", …" if len(empty) > 10 else ""),
            )
        )
        frame = frame.drop(columns=empty)
        for c in empty:
            kinds.pop(c)

    removed: set[int] = set()

    blank = [int(i) for i in frame.index[frame.isna().all(axis=1)]]
    if blank:
        removed |= set(blank)
        steps.append(
            StepRecord(
                id="empty_rows",
                kind="empty_rows",
                summary=f"Removed {len(blank):,} empty row{'s' if len(blank) != 1 else ''}",
                removed_rows=set(blank),
                examples=[(p.rows[i], "", None) for i in blank[:MAX_EXAMPLES]],
            )
        )

    live = frame.drop(index=sorted(removed))
    totals = find_totals(live, kinds)
    remove_totals = "total_rows" not in answers or answers["total_rows"].value == "remove"
    confident = [i for i, sure in totals if sure]
    unsure = [i for i, sure in totals if not sure]
    for chosen, by in ((confident, "rule"), (unsure if remove_totals else [], None)):
        if not chosen:
            continue
        removed |= set(chosen)
        steps.append(
            StepRecord(
                id="total_rows" if by is None else "total_rows:labelled",
                kind="total_rows",
                summary=f"Removed {_rows_text([p.rows[i] for i in chosen])}: "
                + (
                    "labelled as a total, and each number equals the sum of the rows above"
                    if by == "rule"
                    else "rows of totals"
                ),
                decided_by="rule" if by == "rule" else decided(answers, ["total_rows"]),
                question_ids=[] if by == "rule" else ["total_rows"],
                removed_rows=set(chosen),
                examples=[(p.rows[i], _row_text(frame, i), None) for i in chosen[:MAX_EXAMPLES]],
            )
        )

    live = frame.drop(index=sorted(removed))
    duplicates = find_duplicates(live)
    keep_copies = "duplicates" in answers and answers["duplicates"].value == "keep"
    if duplicates and not keep_copies:
        copies = {i for i, _ in duplicates}
        removed |= copies
        asked = "duplicates" in answers
        steps.append(
            StepRecord(
                id="duplicates",
                kind="duplicates",
                summary=f"Removed {len(copies):,} row{'s' if len(copies) != 1 else ''} that "
                + ("repeat" if len(copies) != 1 else "repeats")
                + " an earlier row exactly"
                + ("" if asked else ", id included, so they are copies"),
                decided_by=decided(answers, ["duplicates"]) if asked else "rule",
                question_ids=["duplicates"] if asked else [],
                removed_rows=copies,
                examples=[
                    (p.rows[i], f"same as row {p.rows[j]}", None)
                    for i, j in duplicates[:MAX_EXAMPLES]
                ],
            )
        )

    kept = [i for i in range(len(p.rows)) if i not in removed]
    out = frame.loc[kept].reset_index(drop=True)
    clean = Clean(
        frame=out,
        rows=[p.rows[i] for i in kept],
        kinds=kinds,
        units={c: u for c, u in units.items() if c in out.columns},
        sources={c: sources[c] for c in out.columns},
        steps=steps,
        notes=notes,
        rows_in=len(p.rows),
        header_row=None if p.layout.header_row is None else p.layout.header_row + 1,
        title_text=p.layout.title_text,
        check=[],
    )
    clean.check = verify(p, clean, results, assembled.names, kept, removed)
    return clean


def _layout_steps(p: Plan, answers: dict[str, Answer]) -> list[StepRecord]:
    layout = p.layout
    steps = []
    if layout.title_rows:
        titles = "; ".join(f'"{t}"' for t in layout.title_text)
        steps.append(
            StepRecord(
                id="title_rows",
                kind="header",
                summary=f"Skipped {_rows_text([i + 1 for i in layout.title_rows])} above the "
                f"column names: {titles}",
            )
        )
    if p.table.header is None and layout.header_row is None:
        steps.append(
            StepRecord(
                id="header",
                kind="header",
                summary="The file has no row of column names; the columns were named "
                "column_1, column_2, …",
            )
        )
    elif layout.group_row is not None:
        upper, lower = layout.group_row + 1, (layout.header_row or 0) + 1
        combined = answers["header"].value == "combined"
        steps.append(
            StepRecord(
                id="header",
                kind="header",
                summary=(
                    f"Combined the column names on rows {upper} and {lower}"
                    if combined
                    else f"Used the column names on row {lower}"
                ),
                decided_by=decided(answers, ["header"]),
                question_ids=["header"],
            )
        )
    renamed = layout.renamed
    if "header" in answers and answers["header"].value == "combined":
        renamed = layout.combined_renamed
    if renamed:
        steps.append(
            StepRecord(
                id="names",
                kind="names",
                summary="Renamed "
                + "; ".join(
                    f'"{was}" to "{now}" ({why})' if was else f"an unnamed column to {now}"
                    for was, now, why in renamed[:10]
                ),
                cells_changed=len(renamed),
            )
        )
    return steps


def verify(
    p: Plan,
    clean: Clean,
    results: list[ColumnResult],
    names: list[str],
    kept: list[int],
    removed: set[int],
) -> list[str]:
    """Check the result against the raw table; raise :class:`CleaningCheckError` if any
    value is unaccounted for. Returns what was checked, for the report."""
    problems = []
    counted: set[int] = set()
    for step in clean.steps:
        if counted & step.removed_rows:
            problems.append(f"rows removed twice: {sorted(counted & step.removed_rows)[:5]}")
        counted |= step.removed_rows
    if counted != removed:
        problems.append("a removed row is not explained by any step")
    if len(clean.frame) != clean.rows_in - len(removed):
        problems.append(f"{clean.rows_in} rows in, {len(removed)} removed, {len(clean.frame)} out")

    kept_set = set(kept)
    values_checked = 0
    for col, result, name in zip(p.columns, results, names, strict=True):
        emptied = set().union(*(s.emptied for s in result.steps)) & kept_set
        raw_present = sum(1 for i in kept if not is_empty(col.raw[i]))
        # a column dropped as empty has nothing left in the result
        present = int(clean.frame[name].notna().sum()) if name in clean.frame else 0
        values_checked += raw_present
        if raw_present - present != len(emptied):
            problems.append(
                f"{col.name}: {raw_present} values in the file, {present} in the result, "
                f"but {len(emptied)} explained"
            )
    if problems:
        raise CleaningCheckError("The cleaning check failed: " + "; ".join(problems))
    emptied_total = sum(len(s.emptied & kept_set) for s in clean.steps)
    return [
        f"{_n(clean.rows_in, 'row')} in, {len(removed):,} removed by the steps above, "
        f"{len(clean.frame):,} out",
        f"{_n(values_checked, 'value')} in the file: {values_checked - emptied_total:,} kept, "
        f"{emptied_total:,} left empty by the steps above, none lost",
    ]


def _n(count: int, word: str) -> str:
    return f"{count:,} {word}{'' if count == 1 else 's'}"
