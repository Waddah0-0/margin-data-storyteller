"""Reading single values: numbers written every common way, dates, and empty markers."""

from __future__ import annotations

import pytest
from data_storyteller.values import is_null, plain_number, read_date, read_number


@pytest.mark.parametrize(
    ("text", "value", "comma_value", "style"),
    [
        ("$1,240.50", 1240.5, 1240.5, "dot"),
        ("€ 3.400,00", 3400.0, 3400.0, "comma"),
        ("1.234", 1.234, 1234.0, "ambiguous"),
        ("1,234", 1234.0, 1.234, "ambiguous"),
        ("1,234,567", 1234567.0, 1234567.0, "dot"),
        ("1.000.000", 1000000.0, 1000000.0, "comma"),
        ("1 234 567,89", 1234567.89, 1234567.89, "comma"),
        ("CHF 1'234.50", 1234.5, 1234.5, "dot"),
        ("0.123", 0.123, 0.123, "dot"),
        ("12,5", 12.5, 12.5, "comma"),
        (".5", 0.5, 0.5, "dot"),
        ("1.5E+05", 150000.0, 150000.0, "dot"),
        ("00123", 123.0, 123.0, "plain"),
    ],
)
def test_number_formats(text: str, value: float, comma_value: float, style: str) -> None:
    n = read_number(text)
    assert n is not None and n.style == style
    assert n.value == pytest.approx(value) and n.comma_value == pytest.approx(comma_value)


@pytest.mark.parametrize(
    "text",
    ["-100", "$-100", "-$100", "$(100)", "(100)", "100-", "−100", "-€ 100", "€ (100)"],
)
def test_every_way_of_writing_a_negative(text: str) -> None:
    n = read_number(text)
    assert n is not None and n.value == -100


def test_currencies_and_percentages() -> None:
    assert read_number("SAR 1,500").currency == "SAR"  # type: ignore[union-attr]
    assert read_number("1,500 ر.س").currency == "ر.س"  # type: ignore[union-attr]
    pct = read_number("12.5%")
    assert pct is not None and pct.percent and pct.value == 0.125
    assert read_number("15 %").value == 0.15  # type: ignore[union-attr]


def test_arabic_indic_digits() -> None:
    assert read_number("١٢٣٤").value == 1234  # type: ignore[union-attr]
    assert read_number("٥٦٧٫٥").value == 567.5  # type: ignore[union-attr]
    assert not plain_number("١٢٣٤")  # needed reading, so it is reported


@pytest.mark.parametrize(
    "text", ["abc", "USD", "1.2.3", "+44 20 7946 0958", "2024-01-15", "12 apples", "1-2"]
)
def test_not_numbers(text: str) -> None:
    assert read_number(text) is None


def test_dates_keep_every_possible_reading() -> None:
    both = read_date("01/02/2024")
    assert both is not None and set(both.readings) == {"dmy", "mdy"}
    assert set(read_date("31/01/2024").readings) == {"dmy"}  # type: ignore[union-attr]
    assert set(read_date("12/31/2024 3:45 PM").readings) == {"mdy"}  # type: ignore[union-attr]
    assert read_date("12/31/2024 3:45 PM").readings["mdy"].hour == 15  # type: ignore[union-attr]
    assert read_date("05.03.2024").separator == "."  # type: ignore[union-attr]
    short = read_date("01-02-24")
    assert short is not None and short.short_year and short.readings["dmy"].year == 2024


@pytest.mark.parametrize(
    ("text", "iso"),
    [
        ("2024-01-15", "2024-01-15"),
        ("2024-01-15T10:30:00Z", "2024-01-15T10:30:00"),
        ("Jan 5, 2024", "2024-01-05"),
        ("5 January 2024", "2024-01-05"),
        ("March 2024", "2024-03-01"),
    ],
)
def test_unambiguous_dates(text: str, iso: str) -> None:
    d = read_date(text)
    assert d is not None and len(d.readings) == 1
    assert next(iter(d.readings.values())).isoformat().startswith(iso)


@pytest.mark.parametrize("text", ["2023-Q1", "Q1", "31/02/2024", "13/13/2024", "hello"])
def test_not_dates(text: str) -> None:
    assert read_date(text) is None


def test_na_is_a_value_in_text_but_empty_in_numbers() -> None:
    assert is_null("N/A", strict=False) and is_null(" - ", strict=False)
    assert not is_null("NA", strict=False)  # could be Namibia, or sodium
    assert is_null("NA", strict=True) and is_null("#DIV/0!", strict=True)
