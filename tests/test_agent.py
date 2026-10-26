"""The agent end to end: questions in one batch, answers up front, files, and the result."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import openpyxl
from data_storyteller import DataStorytellerAgent
from margin_core import QuestionBatch, RunContext


class Scripted:
    """Answers questions from a script and remembers what it was asked."""

    def __init__(self, **reply: Any) -> None:
        self.reply = reply
        self.batches: list[QuestionBatch] = []

    async def __call__(self, batch: QuestionBatch) -> Mapping[str, Any]:
        self.batches.append(batch)
        return {k: v for k, v in self.reply.items() if any(q.id == k for q in batch.questions)}


async def run(dataset: Path, responder: Scripted | None = None, **fields: Any):  # type: ignore[no-untyped-def]
    ctx = RunContext(responder=responder)
    return await DataStorytellerAgent().execute({"dataset": str(dataset), **fields}, ctx)


async def test_asks_every_question_at_once_and_uses_the_answers(messy: dict[str, Path]) -> None:
    responder = Scripted(**{"decimal:amount": "comma"})
    result = await run(messy["currency_symbols"], responder)
    assert result.status == "ok", result.error
    (batch,) = responder.batches
    assert batch.title == "Check the data" and [q.id for q in batch.questions] == ["decimal:amount"]
    assert batch.questions[0].evidence  # the values that made it ask

    data = result.data
    steps = {s["id"]: s for s in data["cleaning"]["steps"]}
    assert steps["numbers:amount"]["decided_by"] == "user"
    assert [a.source for a in result.metadata.answers] == ["user"]
    assert result.warnings == []
    finding = next(f for f in result.findings if f.title == "amount: read as numbers")
    assert finding.evidence[0].kind == "decision" and "You chose" in finding.evidence[0].detail


async def test_nothing_to_ask_means_no_questions(messy: dict[str, Path]) -> None:
    responder = Scripted()
    result = await run(messy["semicolon_eu"], responder)
    assert result.status == "ok" and responder.batches == []
    assert result.summary.startswith("Cleaned semicolon_eu.csv: 3 rows and 3 columns.")
    assert result.findings[-1].title == "Every value is accounted for"


async def test_answers_given_up_front_are_not_asked(messy: dict[str, Path]) -> None:
    responder = Scripted()
    answers = {"date_order:date_str": "dmy"}
    result = await run(messy["ambiguous_dates"], responder, answers=answers)
    assert responder.batches == []
    assert result.metadata.answers[0].source == "preset"


async def test_without_anyone_to_ask_defaults_are_marked(messy: dict[str, Path]) -> None:
    result = await run(messy["ambiguous_dates"])
    assert result.status == "ok"
    assert "1 decision was assumed, not confirmed" in result.summary
    assert result.warnings and result.warnings[0].startswith("Not confirmed:")


async def test_writes_the_clean_file_and_the_report(messy: dict[str, Path], tmp_path: Path) -> None:
    result = await run(messy["markers"], output_dir=str(tmp_path))
    written = [Path(p).name for p in result.data["files"]]
    assert written == ["markers.clean.csv", "markers.cleaning.json"]
    text = (tmp_path / "markers.clean.csv").read_text(encoding="utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == ["id", "score", "checked_on"]
    assert rows[1] == ["1", "", "2024-01-01"] and rows[3] == ["3", "42", "2024-01-03"]
    report = json.loads((tmp_path / "markers.cleaning.json").read_text(encoding="utf-8"))
    assert report["cleaning"]["rows_out"] == 5 and "answers" in report


async def test_story_mode_sets_up_the_story(messy: dict[str, Path]) -> None:
    responder = Scripted(grain="one department per row")
    result = await run(messy["no_date_data"], responder, stop_after="story")
    assert result.status == "partial"
    assert "not written yet" in result.warnings[-1]
    assert result.data["facts"]["facts"] and "all agree" in result.data["facts"]["check"][0]
    (batch,) = responder.batches
    # headcount is a level, so budget is the only number that adds up: nothing to ask
    assert {q.id for q in batch.questions} == {"grain"}
    setup = result.data["setup"]
    assert setup["north_star"] == "budget" and setup["grain"] == "one department per row"
    assert setup["aggregations"]["headcount"] == "last"


async def test_a_workbook_with_several_sheets_asks_which(tmp_path: Path) -> None:
    workbook = openpyxl.Workbook()
    first = workbook.active
    assert first is not None
    first.title = "Small"
    first.append(["a"])
    first.append([1])
    big = workbook.create_sheet("Big")
    for row in (["b"], [1], [2], [3]):
        big.append(row)
    path = tmp_path / "two.xlsx"
    workbook.save(path)

    responder = Scripted(table="Small")
    result = await run(path, responder)
    assert responder.batches[0].questions[0].default == "Big"
    assert result.data["source"]["table"] == "Small"

    named = await run(path, table="Big")
    assert named.data["cleaning"]["rows_out"] == 3
    missing = await run(path, table="Nope")
    assert missing.status == "error" and "has no table 'Nope'" in (missing.error or "")


async def test_personal_data_never_reaches_the_result(tmp_path: Path) -> None:
    path = tmp_path / "people.csv"
    path.write_text(
        "customer_name,email,city,spend\n"
        "Ann Lee,ann@x.com,Cairo,10\n"
        "Bob Stone,bob@y.org,Riyadh,20\n",
        encoding="utf-8",
    )
    result = await run(path)
    text = json.dumps(result.model_dump(mode="json"))
    for secret in ("Ann Lee", "Bob Stone", "ann@x.com", "bob@y.org"):
        assert secret not in text
    assert "Cairo" in text  # ordinary values still show
    personal = {c["name"] for c in result.data["columns"] if c["personal"]}
    assert personal == {"customer_name", "email"}


async def test_unreadable_files_fail_clearly(tmp_path: Path) -> None:
    result = await run(tmp_path / "nope.csv")
    assert result.status == "error" and "No file" in (result.error or "")
