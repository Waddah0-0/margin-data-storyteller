"""Turning a cleaning into the agent's result: the summary, findings with evidence, files."""

from __future__ import annotations

import json
from pathlib import Path

from margin_core import Answer, Evidence, Finding

from data_storyteller.cleaning import Clean
from data_storyteller.columns import StepRecord
from data_storyteller.ingest import Source
from data_storyteller.models import (
    Change,
    CleaningReport,
    ColumnProfile,
    DataStorytellerOutput,
    SourceInfo,
    Step,
    TableInfo,
)

ANSWERED = {
    "user": "You chose",
    "preset": "Given with the run",
    "default": "Assumed, not confirmed",
}
READ_AS = {"number": "numbers", "date": "dates", "text": "text", "empty": "empty"}
TOP_FACTS = 10

TITLES = {
    "header": "Where the data starts",
    "names": "Column names",
    "empty_markers": "{column}: values that mean empty",
    "numbers": "{column}: read as numbers",
    "dates": "{column}: read as dates",
    "trim": "{column}: extra spaces",
    "spellings": "{column}: one spelling per value",
    "currency_split": "{column}: currencies kept apart",
    "empty_columns": "Empty columns",
    "empty_rows": "Empty rows",
    "total_rows": "Total rows",
    "duplicates": "Repeated rows",
}


def step_model(step: StepRecord) -> Step:
    return Step(
        id=step.id,
        kind=step.kind,
        column=step.column,
        summary=step.summary,
        decided_by=step.decided_by,
        question_ids=step.question_ids,
        cells_changed=step.cells_changed,
        cells_emptied=len(step.emptied),
        rows_removed=len(step.removed_rows),
        examples=[Change(row=r, before=str(b), after=a) for r, b, a in step.examples],
        unreadable=[Change(row=r, before=v, after=None) for r, v in step.failures],
    )


def output(
    source: Source, table: str, clean: Clean, columns: list[ColumnProfile]
) -> DataStorytellerOutput:
    raw = source.tables[table]
    return DataStorytellerOutput(
        source=SourceInfo(
            file=source.path.name,
            format=source.format,
            encoding=source.encoding,
            delimiter=source.delimiter,
            tables=[
                TableInfo(name=t.name, rows=len(t.rows), columns=t.width)
                for t in source.tables.values()
            ],
            table=table,
            header_row=clean.header_row,
            title=clean.title_text,
            notes=source.notes,
        ),
        cleaning=CleaningReport(
            rows_in=clean.rows_in,
            rows_out=len(clean.frame),
            columns_in=raw.width,
            columns_out=len(clean.frame.columns),
            steps=[step_model(s) for s in clean.steps],
            check=clean.check,
        ),
        columns=columns,
    )


def findings(data: DataStorytellerOutput, answers: dict[str, Answer]) -> list[Finding]:
    found = []
    for step in data.cleaning.steps:
        evidence = [
            Evidence(
                kind="decision",
                location=answers[q].prompt,
                detail=f"{ANSWERED.get(answers[q].source, answers[q].source)}: {answers[q].label}",
            )
            for q in step.question_ids
            if q in answers
        ]
        evidence += [
            Evidence(
                kind="change",
                location=f"row {c.row}",
                detail=f"{c.before} → {c.after}" if c.after is not None else f"{c.before} → empty",
            )
            for c in step.examples
            if c.before
        ]
        evidence += [
            Evidence(kind="unreadable", location=f"row {c.row}", detail=f"{c.before} → left empty")
            for c in step.unreadable
        ]
        title = TITLES.get(step.kind, step.kind).format(column=step.column)
        found.append(Finding(title=title, summary=step.summary + ".", evidence=evidence))

    for column in data.columns:
        for note in column.notes:
            found.append(
                Finding(
                    title=f"{column.name}: worth knowing",
                    summary=note,
                    evidence=[
                        Evidence(
                            kind="column",
                            location=column.name,
                            detail=f"Read as {READ_AS[column.type]}: "
                            f"{column.role.replace('_', ' ')}, because {column.reason}",
                        )
                    ],
                )
            )

    found.append(
        Finding(
            title="Every value is accounted for",
            summary="The result was checked against the file, independently of the steps: "
            "nothing was lost or changed without a step saying so.",
            evidence=[Evidence(kind="check", detail=line) for line in data.cleaning.check],
        )
    )

    if data.facts is not None and data.facts.facts:
        top = sorted(data.facts.facts, key=lambda f: -f.importance)[:TOP_FACTS]
        found.append(
            Finding(
                title="What the data says",
                summary=f"The {len(top)} most telling of {len(data.facts.facts)} facts, each "
                "computed twice and matched. "
                + (data.facts.check[0] + "." if data.facts.check else ""),
                evidence=[
                    Evidence(
                        kind=f"{f.confidence} confidence",
                        location=f.id,
                        detail=f.statement
                        + (f" Caveat: {' '.join(f.caveats)}" if f.caveats else ""),
                    )
                    for f in top
                ],
            )
        )
    return found


def summary(data: DataStorytellerOutput, assumed: int) -> str:
    c = data.cleaning
    where = data.source.file + (f" ({data.source.table})" if len(data.source.tables) > 1 else "")
    changes = [s for s in c.steps if s.kind not in ("header",)]
    removed = sum(s.rows_removed for s in c.steps)
    emptied = sum(s.cells_emptied for s in c.steps if s.kind in ("numbers", "dates"))
    parts = [
        f"Cleaned {where}: {c.rows_out:,} rows and {c.columns_out:,} columns.",
        f"{len(changes)} change{'s' if len(changes) != 1 else ''}"
        + (f", {removed:,} row{'s' if removed != 1 else ''} removed" if removed else "")
        + (
            f", {emptied:,} unreadable value{'s' if emptied != 1 else ''} left empty"
            if emptied
            else ""
        )
        + "; every value is accounted for."
        if changes
        else "Nothing needed changing; every value is accounted for.",
    ]
    if assumed:
        parts.append(
            f"{assumed} decision{'s were' if assumed != 1 else ' was'} assumed, not confirmed."
        )
    return " ".join(parts)


def write(
    folder: str, data: DataStorytellerOutput, clean: Clean, answers: dict[str, Answer]
) -> list[str]:
    """Write the cleaned table (CSV) and the report (JSON). Returns the paths written."""
    out = Path(folder).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    stem = Path(data.source.file).stem
    if len(data.source.tables) > 1:
        stem += f".{data.source.table}"
    csv_path = out / f"{stem}.clean.csv"
    report_path = out / f"{stem}.cleaning.json"
    frame = clean.frame.copy()
    for column, kind in clean.kinds.items():
        if kind == "date" and column in frame:
            dates = frame[column]
            midnight = (dates.dropna() == dates.dropna().dt.normalize()).all()
            frame[column] = dates.dt.strftime("%Y-%m-%d" if midnight else "%Y-%m-%d %H:%M:%S")
    # utf-8 with a BOM, so Excel shows accents and Arabic text correctly
    frame.to_csv(csv_path, index=False, encoding="utf-8-sig", float_format="%.15g")
    report = data.model_dump(mode="json", exclude={"files"})
    report["answers"] = [a.model_dump(mode="json") for a in answers.values()]
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return [str(csv_path), str(report_path)]
