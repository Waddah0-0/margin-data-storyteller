"""Test fixtures: the messy-data suite, small files with one problem each, built per session.

The first sixteen come from StoryBI's behaviour matrix; the rest cover cases the redesigned
cleaner handles. Files are written byte for byte here (encodings, line endings, BOMs)
rather than committed, so git's line-ending rules can never change them.
"""

from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest


def _write(path: Path, text: str, encoding: str = "utf-8") -> Path:
    path.write_bytes(text.replace("\n", "\r\n").encode(encoding))
    return path


def _rows(*rows: str) -> str:
    return "\n".join(rows) + "\n"


def build(folder: Path) -> dict[str, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    files: dict[str, Path] = {}

    def add(name: str, text: str, encoding: str = "utf-8") -> None:
        files[name.rsplit(".", 1)[0]] = _write(folder / name, text, encoding)

    # ---- StoryBI's matrix ----
    add(
        "percentages.csv",
        _rows(
            "item,discount,revenue",
            "Alpha,12.5%,1000",
            "Beta,0.125,1500",
            "Gamma,15.0%,2000",
            "Delta,0.08,2500",
        ),
    )
    add(
        "currency_symbols.csv",
        _rows("region,amount,units", 'US,"$1,240.50",10', 'EU,"€ 3.400,00",20', "Unknown,1.234,30"),
    )
    files["utf8_bom"] = folder / "utf8_bom.csv"
    files["utf8_bom"].write_bytes(
        b"\xef\xbb\xbf" + _rows("id,product,price", "1,Widget,19.99", "2,Gadget,29.99").encode()
    )
    add(
        "latin1.csv",
        _rows("id,city,metric", "1,München,100", "2,Zürich,200", "3,São Paulo,300"),
        "latin-1",
    )
    add("windows1252.csv", _rows("id,quote,score", "1,“Smart Quotes”,95", "2,En–Dash,88"), "cp1252")
    add(
        "mixed_dates.csv",
        _rows(
            "order_id,date_str,sales",
            "1,2024-01-15,100",
            "2,31/01/2024,200",
            "3,14/02/2024,150",
            "4,05/03/2024,300",
        ),
    )
    add(
        "ambiguous_dates.csv",
        _rows(
            "order_id,date_str,sales", "1,01/02/2024,120", "2,03/04/2024,220", "3,05/06/2024,320"
        ),
    )
    add("duplicate_headers.csv", _rows("id,Sales,Sales,Profit", "1,100,200,50", "2,150,250,75"))
    add(
        "duplicate_rows.csv",
        _rows("id,category,revenue", "1,A,100", "2,B,200", "2,B,200", "3,C,300"),
    )
    add(
        "all_null_column.csv",
        _rows("id,valid_metric,empty_column", "1,10.5,", "2,20.0,", "3,30.5,"),
    )
    add("single_row.csv", _rows("metric_a,category,status", "5000,SoleEntity,Active"))
    add(
        "no_date_data.csv",
        _rows(
            "department,headcount,budget",
            "Engineering,120,2400000",
            "Sales,85,1700000",
            "Support,45,900000",
            "Marketing,30,600000",
        ),
    )
    add(
        "no_numeric_data.csv",
        _rows(
            "respondent_id,feedback_category,sentiment",
            "R1,UI,Positive",
            "R2,Performance,Negative",
            "R3,UI,Neutral",
            "R4,Pricing,Negative",
            "R5,Support,Positive",
        ),
    )
    header = ["entity_id"] + [f"feature_{i:03d}" for i in range(1, 305)]
    wide = [",".join(header)]
    for r in range(20):
        wide.append(",".join([f"E{r:03d}"] + [str((r * 7 + c * 13) % 97 + c) for c in range(304)]))
    add("wide_dataset.csv", _rows(*wide))
    add(
        "negative_values.csv",
        _rows(
            "quarter,net_income,cash_flow",
            "2023-Q1,120000.0,90000.0",
            "2023-Q2,-45000.0,-20000.0",
            "2023-Q3,-15000.0,5000.0",
            "2023-Q4,85000.0,60000.0",
        ),
    )
    files["excel_merged_headers"] = _workbook(
        folder / "excel_merged_headers.xlsx",
        "Summary",
        [
            ["Financial Report - Confidential", None, None],
            ["Fiscal Year 2024", None, None],
            ["Division", "Q1 Revenue", "Q2 Revenue"],
            ["North America", 100000, 120000],
            ["Europe", 85000, 92000],
            ["Asia Pacific", 64000, 78000],
        ],
    )

    # ---- cases the redesign adds ----
    files["two_row_header"] = _workbook(
        folder / "two_row_header.xlsx",
        "Sales",
        [
            [None, "Revenue", None, "Cost", None],
            ["Region", "Q1", "Q2", "Q1", "Q2"],
            ["North", 100, 110, 60, 65],
            ["South", 90, 95, 50, 52],
            ["East", 120, 130, 70, 72],
        ],
    )
    add(
        "total_row.csv",
        _rows("region,sales,units", "North,100,10", "South,250,25", "East,150,15", "Total,500,50"),
    )
    add("unlabelled_total.csv", _rows("region,sales", "North,100", "South,250", "East,150", ",500"))
    add("zip_codes.csv", _rows("customer,zip,spend", "A,02134,10", "B,10001,20", "C,07030,30"))
    add(
        "arabic_digits.csv",
        _rows(
            "المدينة,المبيعات,التاريخ",
            "الرياض,١٢٣٤,٢٠٢٤-٠١-١٥",
            "جدة,٥٦٧٫٥,٢٠٢٤-٠٢-٢٠",
            "الدمام,٨٩,٢٠٢٤-٠٣-١٠",
        ),
    )
    add(
        "spellings.csv",
        _rows(
            "id,region,sales",
            "1,North,10",
            "2,north,20",
            "3,NORTH ,30",
            "4,South,40",
            "5,South,50",
            "6,East,60",
            "7,North,70",
            "8,South,80",
        ),
    )
    add(
        "markers.csv",
        _rows(
            "id,score,checked_on",
            "1,N/A,2024-01-01",
            "2,#DIV/0!,n/a",
            "3,42,2024-01-03",
            "4,-,2024-01-04",
            "5,7,NA",
        ),
    )
    add(
        "mostly_numbers.csv",
        _rows("id,amount", *[f"{i},{i * 10}" for i in range(1, 9)], "9,unknown", "10,see note"),
    )
    add(
        "semicolon_eu.csv",
        _rows(
            "Produkt;Preis;Datum",
            "Tisch;1.234,50;31.01.2024",
            "Stuhl;89,90;15.02.2024",
            "Lampe;12,00;01.03.2024",
        ),
    )
    add("no_header.csv", _rows("100,2024-01-01,4.5", "200,2024-01-02,3.5", "150,2024-01-03,4.0"))
    add("duplicates_no_id.csv", _rows("product,qty", "Pen,2", "Pen,2", "Book,1"))
    return files


def _workbook(path: Path, sheet: str, rows: list[list[object]]) -> Path:
    workbook = openpyxl.Workbook()
    ws = workbook.active
    assert ws is not None
    ws.title = sheet
    for row in rows:
        ws.append(row)
    workbook.save(path)
    return path


@pytest.fixture(scope="session")
def messy(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    return build(tmp_path_factory.mktemp("messy"))
