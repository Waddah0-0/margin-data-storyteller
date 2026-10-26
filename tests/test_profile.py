"""Column roles, how numbers add up, personal data, and the story setup questions."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from data_storyteller import cleaning, setup
from data_storyteller.ingest import RawTable
from data_storyteller.models import ColumnProfile
from data_storyteller.profile import classify, personal_data, profile
from margin_core import Answer


def profiled(header: list[str], rows: list[list[object]]) -> dict[str, ColumnProfile]:
    plan = cleaning.plan(RawTable("t", [header, *rows]))
    clean = cleaning.apply(plan, {q.id: Answer.of(q, q.default, "default") for q in plan.questions})
    return {c.name: c for c in profile(clean)}


ORDERS = profiled(
    ["order_id", "order_date", "category", "revenue", "profit_margin", "unit_price",
     "rating", "stock_level", "customer_email", "widgets"],
    [
        ["ORD-1", "2024-01-10", "Tech", "1200.50", "0.24", "12.00", "4.5", "40", "a@x.com", "3"],
        ["ORD-2", "2024-01-11", "Home", "450.00", "0.15", "9.50", "3.8", "38", "b@x.com", "1"],
        ["ORD-3", "2024-01-12", "Tech", "890.25", "0.28", "15.00", "4.9", "35", "c@x.com", "7"],
        ["ORD-4", "2024-01-13", "Office", "120.00", "0.10", "3.25", "4.0", "30", "d@x.com", "2"],
    ],
)  # fmt: skip


@pytest.mark.parametrize(
    ("name", "role", "aggregation"),
    [
        ("order_id", "identifier", "count_distinct"),
        ("order_date", "time", "none"),
        ("category", "category", "count_distinct"),
        ("revenue", "measure", "sum"),
        ("profit_margin", "measure", "average"),  # a rate: never added up
        ("unit_price", "measure", "average"),  # prices are averaged
        ("rating", "measure", "average"),
        ("stock_level", "measure", "last"),
        ("widgets", "measure", "unknown"),  # the name says nothing: asked in the setup
    ],
)
def test_roles_and_aggregations(name: str, role: str, aggregation: str) -> None:
    c = ORDERS[name]
    assert (c.role, c.aggregation) == (role, aggregation), c.reason


def test_names_tell_rates_from_amounts_by_their_values() -> None:
    fractions = pd.Series([0.1, 0.25])
    money = pd.Series([120.0, 300.0])
    assert classify("discount", "number", fractions, None)[1] == "average"
    assert classify("discount", "number", money, None)[1] == "sum"
    assert classify("score", "number", money, "%")[1] == "average"
    assert classify("fiscal_year", "number", pd.Series([2023, 2024]), None)[0] == "time"


@pytest.mark.parametrize(
    ("name", "values", "found"),
    [
        ("customer_email", ["x"], "email"),
        ("contact", ["a@b.com", "c@d.org"], "email addresses"),
        ("mobile_no", [], "mobile"),
        ("customer_name", ["Ann Lee"], "people's names"),
        ("name", ["Ann Lee", "Bob Stone", "Cy Twombly"], "people's names"),
        ("iban", [], "iban"),
        ("acct", ["DE89370400440532013000", "GB29NWBK60161331926819"], "IBANs"),
        ("card", ["4111 1111 1111 1111", "5500-0000-0000-0004"], "card numbers"),
        ("host", ["10.0.0.1", "192.168.1.20"], "IP addresses"),
    ],
)
def test_personal_data(name: str, values: list[str], found: str) -> None:
    reason = personal_data(name, values)
    assert reason is not None and found in reason


@pytest.mark.parametrize(
    ("name", "values"),
    [
        ("product_name", ["Desk Lamp", "Office Chair"]),
        ("company_name", ["Acme Corp"]),
        ("city", ["Riyadh", "Cairo"]),
        ("zip", ["02134"]),
        ("amount", ["4111111111111111"]),  # one card-like value among a column is not enough
    ],
)
def test_not_personal_data(name: str, values: list[str]) -> None:
    if name == "amount":
        values = values + ["12", "40", "7"]
    assert personal_data(name, values) is None


def test_personal_values_are_never_shown() -> None:
    email = ORDERS["customer_email"]
    assert email.personal and email.examples == [] and email.top == []
    assert "not shown" in email.notes[0]
    assert ORDERS["category"].top[0].value == "Tech"


def test_setup_asks_only_what_it_cannot_tell() -> None:
    asked = {q.id: q for q in setup.questions(list(ORDERS.values()))}
    assert set(asked) == {"aggregate:widgets", "north_star", "grain"}
    assert asked["north_star"].default == "revenue"
    assert asked["grain"].default == "order_id"


def test_setup_resolves_answers_against_the_final_columns() -> None:
    columns = [c.model_copy() for c in ORDERS.values()]
    qs = {q.id: q for q in setup.questions(columns)}
    answers = {
        "aggregate:widgets": Answer.of(qs["aggregate:widgets"], "sum", "user"),
        "north_star": Answer.of(qs["north_star"], "widgets", "user"),
        "grain": Answer.of(qs["grain"], "order line", "user"),
    }
    result, warnings = setup.resolve(columns, answers)
    assert result.north_star == "widgets" and result.aggregations["widgets"] == "sum"
    assert result.grain == "order line" and result.primary_date == "order_date"
    assert warnings == []


def test_a_wide_table_asks_where_to_focus(messy: dict[str, Path]) -> None:
    from data_storyteller.ingest import read_source

    plan = cleaning.plan(read_source(messy["wide_dataset"]).tables["wide_dataset"])
    clean = cleaning.apply(plan, {})
    focus = next(q for q in setup.questions(profile(clean)) if q.id == "focus")
    assert focus.kind == "multi_choice" and len(focus.choices) == setup.FOCUS_CHOICES
    assert "entity_id" not in [c.value for c in focus.choices]
