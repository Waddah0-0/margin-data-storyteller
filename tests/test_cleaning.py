"""The messy-data suite: each file's questions, and the cleaned result for each answer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from data_storyteller import cleaning
from data_storyteller.cleaning import Clean, CleaningCheckError
from data_storyteller.ingest import read_source
from margin_core import Answer


def run(path: Path, **chosen: Any) -> tuple[list[str], Clean]:
    """Plan and clean ``path``; questions take ``chosen`` answers, the rest their defaults."""
    table = next(iter(read_source(path).tables.values()))
    plan = cleaning.plan(table)
    answers = {
        q.id: Answer.of(q, q.check(chosen[q.id]) if q.id in chosen else q.default,
                        "user" if q.id in chosen else "default")
        for q in plan.questions
    }  # fmt: skip
    return [q.id for q in plan.questions], cleaning.apply(plan, answers)


def column(clean: Clean, name: str) -> list[Any]:
    return [None if pd.isna(v) else v for v in clean.frame[name].tolist()]


def step(clean: Clean, kind: str, col: str | None = None) -> Any:
    found = [s for s in clean.steps if s.kind == kind and (col is None or s.column == col)]
    assert found, f"no {kind} step for {col}: {[(s.kind, s.column) for s in clean.steps]}"
    return found[0]


def kinds(clean: Clean) -> list[str]:
    return [s.kind for s in clean.steps]


# ---- StoryBI's matrix -------------------------------------------------------------------


def test_percentages_ask_about_the_plain_numbers(messy: dict[str, Path]) -> None:
    asked, clean = run(messy["percentages"])
    assert asked == ["percent:discount"]
    assert column(clean, "discount") == [0.125, 0.125, 0.15, 0.08]
    assert clean.units["discount"] == "%"
    assert step(clean, "numbers", "discount").decided_by == "default"

    _, other = run(messy["percentages"], **{"percent:discount": "percent"})
    assert column(other, "discount") == pytest.approx([0.125, 0.00125, 0.15, 0.0008])


def test_currencies_and_an_ambiguous_separator(messy: dict[str, Path]) -> None:
    asked, clean = run(messy["currency_symbols"])
    assert asked == ["decimal:amount"]
    assert column(clean, "amount") == [1240.5, 3400.0, 1.234]
    assert column(clean, "amount_currency") == ["$", "€", None]
    assert "currency_split" in kinds(clean)

    _, thousands = run(messy["currency_symbols"], **{"decimal:amount": "comma"})
    assert column(thousands, "amount") == [1240.5, 3400.0, 1234.0]  # clear values never change


@pytest.mark.parametrize(
    ("name", "column_name", "expected"),
    [
        ("utf8_bom", "product", ["Widget", "Gadget"]),
        ("latin1", "city", ["München", "Zürich", "São Paulo"]),
        ("windows1252", "quote", ["“Smart Quotes”", "En–Dash"]),
    ],
)
def test_encodings_are_read_cleanly(
    messy: dict[str, Path], name: str, column_name: str, expected: list[str]
) -> None:
    asked, clean = run(messy[name])
    assert asked == [] and column(clean, column_name) == expected


def test_one_unambiguous_date_settles_the_order(messy: dict[str, Path]) -> None:
    asked, clean = run(messy["mixed_dates"])
    assert asked == []
    dates = clean.frame["date_str"]
    assert dates.dt.month.tolist() == [1, 1, 2, 3] and dates.dt.day.tolist() == [15, 31, 14, 5]
    assert "day first" in step(clean, "dates", "date_str").summary


def test_ambiguous_dates_are_asked(messy: dict[str, Path]) -> None:
    asked, clean = run(messy["ambiguous_dates"])
    assert asked == ["date_order:date_str"]
    assert clean.frame["date_str"].dt.month.tolist() == [1, 3, 5]  # month first, suggested
    _, day_first = run(messy["ambiguous_dates"], **{"date_order:date_str": "dmy"})
    assert day_first.frame["date_str"].dt.month.tolist() == [2, 4, 6]


def test_duplicate_column_names_are_renamed(messy: dict[str, Path]) -> None:
    _, clean = run(messy["duplicate_headers"])
    assert list(clean.frame.columns) == ["id", "Sales", "Sales_2", "Profit"]
    assert "Sales_2" in step(clean, "names").summary


def test_copies_of_rows_with_an_id_are_removed_without_asking(messy: dict[str, Path]) -> None:
    asked, clean = run(messy["duplicate_rows"])
    assert asked == []
    assert column(clean, "id") == [1, 2, 3] and clean.rows == [2, 3, 5]
    removed = step(clean, "duplicates")
    assert removed.removed_rows == {2} and removed.decided_by == "rule"


def test_an_empty_column_is_dropped_and_nothing_else(messy: dict[str, Path]) -> None:
    asked, clean = run(messy["all_null_column"])
    assert asked == []
    assert list(clean.frame.columns) == ["id", "valid_metric"]
    assert len(clean.frame) == 3  # the last row adds up to the others, but it has an id


@pytest.mark.parametrize("name", ["single_row", "no_date_data", "no_numeric_data"])
def test_plain_files_need_nothing(messy: dict[str, Path], name: str) -> None:
    asked, clean = run(messy[name])
    assert asked == [] and clean.steps == []


def test_a_wide_file_keeps_every_column(messy: dict[str, Path]) -> None:
    asked, clean = run(messy["wide_dataset"])
    assert asked == [] and clean.frame.shape == (20, 305)


def test_negative_numbers_and_quarters(messy: dict[str, Path]) -> None:
    _, clean = run(messy["negative_values"])
    assert column(clean, "quarter") == ["2023-Q1", "2023-Q2", "2023-Q3", "2023-Q4"]
    assert clean.frame["net_income"].sum() == 145000
    assert clean.kinds["net_income"] == "number"


def test_title_rows_above_the_names_are_skipped(messy: dict[str, Path]) -> None:
    asked, clean = run(messy["excel_merged_headers"])
    assert asked == []
    assert list(clean.frame.columns) == ["Division", "Q1 Revenue", "Q2 Revenue"]
    assert clean.header_row == 3 and clean.rows == [4, 5, 6]
    assert "Fiscal Year 2024" in step(clean, "header").summary


# ---- what the redesign adds -------------------------------------------------------------


def test_names_over_two_rows(messy: dict[str, Path]) -> None:
    asked, clean = run(messy["two_row_header"])
    assert asked == ["header"]
    assert list(clean.frame.columns) == ["Region", "Revenue Q1", "Revenue Q2", "Cost Q1", "Cost Q2"]
    assert "names" not in kinds(clean)
    _, lower = run(messy["two_row_header"], header="lower")
    assert list(lower.frame.columns) == ["Region", "Q1", "Q2", "Q1_2", "Q2_2"]


def test_a_labelled_total_row_that_adds_up_is_removed(messy: dict[str, Path]) -> None:
    asked, clean = run(messy["total_row"])
    assert asked == [] and column(clean, "region") == ["North", "South", "East"]
    assert step(clean, "total_rows").decided_by == "rule"


def test_an_unlabelled_total_row_is_asked_about(messy: dict[str, Path]) -> None:
    asked, clean = run(messy["unlabelled_total"])
    assert asked == ["total_rows"] and column(clean, "sales") == [100, 250, 150]
    _, kept = run(messy["unlabelled_total"], total_rows="keep")
    assert column(kept, "sales") == [100, 250, 150, 500]


def test_codes_with_leading_zeros_stay_text(messy: dict[str, Path]) -> None:
    _, clean = run(messy["zip_codes"])
    assert column(clean, "zip") == ["02134", "10001", "07030"]
    assert clean.kinds["zip"] == "text"


def test_arabic_indic_digits(messy: dict[str, Path]) -> None:
    _, clean = run(messy["arabic_digits"])
    assert column(clean, "المبيعات") == [1234.0, 567.5, 89.0]
    assert clean.frame["التاريخ"].dt.month.tolist() == [1, 2, 3]
    assert "Arabic-Indic digits" in step(clean, "numbers", "المبيعات").summary


def test_spellings_are_merged_or_kept(messy: dict[str, Path]) -> None:
    asked, clean = run(messy["spellings"])
    assert asked == ["spellings:region"]
    assert column(clean, "region") == [
        "North",
        "North",
        "North",
        "South",
        "South",
        "East",
        "North",
        "South",
    ]
    _, kept = run(messy["spellings"], **{"spellings:region": "keep"})
    assert column(kept, "region")[:3] == ["North", "north", "NORTH"]  # still trimmed


def test_markers_that_mean_no_value(messy: dict[str, Path]) -> None:
    _, clean = run(messy["markers"])
    assert column(clean, "score") == [None, None, 42, None, 7]
    marked = step(clean, "empty_markers", "score")
    assert len(marked.emptied) == 3 and '"#DIV/0!"' in marked.summary
    assert len(step(clean, "empty_markers", "checked_on").emptied) == 2


def test_values_that_are_not_numbers_are_asked_about(messy: dict[str, Path]) -> None:
    asked, clean = run(messy["mostly_numbers"])
    assert asked == ["numbers:amount"]
    assert clean.kinds["amount"] == "text"  # 20% unreadable: keeping text is suggested
    _, numbers = run(messy["mostly_numbers"], **{"numbers:amount": "numbers"})
    assert column(numbers, "amount")[-2:] == [None, None]
    read = step(numbers, "numbers", "amount")
    assert read.failures == [(10, "unknown"), (11, "see note")] and len(read.emptied) == 2


def test_european_file(messy: dict[str, Path]) -> None:
    asked, clean = run(messy["semicolon_eu"])
    assert asked == []
    assert column(clean, "Preis") == [1234.5, 89.9, 12.0]
    assert clean.frame["Datum"].dt.day.tolist() == [31, 15, 1]


def test_a_file_without_column_names(messy: dict[str, Path]) -> None:
    _, clean = run(messy["no_header"])
    assert list(clean.frame.columns) == ["column_1", "column_2", "column_3"]
    assert len(clean.frame) == 3 and clean.header_row is None


def test_copies_without_an_id_are_asked_about(messy: dict[str, Path]) -> None:
    asked, clean = run(messy["duplicates_no_id"])
    assert asked == ["duplicates"] and len(clean.frame) == 2
    _, kept = run(messy["duplicates_no_id"], duplicates="keep")
    assert len(kept.frame) == 3


# ---- the final check --------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["markers", "mostly_numbers", "duplicate_rows", "total_row", "excel_merged_headers",
     "currency_symbols", "spellings", "all_null_column"],
)  # fmt: skip
def test_the_check_accounts_for_every_value(messy: dict[str, Path], name: str) -> None:
    _, clean = run(messy[name])
    assert clean.check[0].endswith(f"{len(clean.frame):,} out")
    assert clean.check[1].endswith("none lost")


def test_the_check_catches_a_value_lost_without_a_step(
    messy: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from data_storyteller.columns import ColumnPlan

    real = ColumnPlan._apply_numbers

    def lossy(self: ColumnPlan, answers: dict[str, Answer]):  # type: ignore[no-untyped-def]
        result = real(self, answers)
        result.values[0] = None  # a bug: a value quietly dropped
        return result

    monkeypatch.setattr(ColumnPlan, "_apply_numbers", lossy)
    with pytest.raises(CleaningCheckError, match="values in the file"):
        run(messy["total_row"])
