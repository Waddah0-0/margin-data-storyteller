"""Describing the cleaned columns: what each holds, how to add it up, and personal data.

Roles come from the column's type, its values and the words in its name. A number is
only summed when its name says it can be (sales, cost, units); prices, rates and scores
are averaged; balances and stock take the last value over time. A number whose name says
nothing gets the aggregation "unknown", and the story setup asks about it.
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from data_storyteller.cleaning import Clean
from data_storyteller.columns import show
from data_storyteller.models import Aggregation, ColumnProfile, Role, ValueCount
from data_storyteller.names import id_like, tokens

RATIO = {
    "rate",
    "ratio",
    "pct",
    "percent",
    "percentage",
    "conversion",
    "churn",
    "ctr",
    "yield",
    "utilization",
    "utilisation",
    "share",
}
MAYBE_RATIO = {"margin", "discount"}  # a rate when its values are fractions, money otherwise
AVERAGE = {
    "price",
    "rating",
    "score",
    "age",
    "temperature",
    "temp",
    "lat",
    "latitude",
    "lon",
    "lng",
    "longitude",
    "avg",
    "average",
    "mean",
    "median",
    "rank",
    "index",
    "nps",
    "satisfaction",
    "stars",
    "grade",
}
LAST = {
    "balance",
    "inventory",
    "stock",
    "headcount",
    "backlog",
    "outstanding",
    "capacity",
    "employees",
    "level",
}
SUM = {
    "amount",
    "revenue",
    "sales",
    "sale",
    "cost",
    "costs",
    "profit",
    "income",
    "spend",
    "spending",
    "budget",
    "quantity",
    "qty",
    "units",
    "count",
    "total",
    "sum",
    "volume",
    "orders",
    "clicks",
    "impressions",
    "visits",
    "sessions",
    "fee",
    "fees",
    "tax",
    "taxes",
    "paid",
    "payment",
    "payments",
    "gmv",
    "expense",
    "expenses",
    "net",
    "gross",
    "turnover",
    "salary",
    "wage",
    "wages",
    "bonus",
    "refund",
    "refunds",
    "shipping",
    "freight",
    "hours",
    "minutes",
    "calls",
    "tickets",
    "downloads",
    "installs",
    "signups",
    "leads",
    "deals",
    "value",
    "weight",
    "items",
    "pieces",
    "loss",
    "losses",
    "earnings",
    "cash",
    "flow",
    "mrr",
    "arr",
    "bookings",
}
YEAR = {"year", "yr", "fy"}
FREE_TEXT = {
    "comment",
    "comments",
    "feedback",
    "note",
    "notes",
    "description",
    "remarks",
    "message",
    "review",
    "text",
    "body",
    "summary",
}

PERSON = {
    "first",
    "last",
    "full",
    "given",
    "family",
    "surname",
    "customer",
    "client",
    "contact",
    "person",
    "employee",
    "user",
    "patient",
    "student",
    "owner",
    "buyer",
    "seller",
    "author",
    "manager",
    "rep",
    "agent",
    "member",
    "applicant",
    "guest",
    "driver",
}
PERSONAL_WORDS = {
    "email",
    "phone",
    "mobile",
    "tel",
    "telephone",
    "fax",
    "ssn",
    "passport",
    "iban",
    "address",
    "street",
    "dob",
    "birth",
    "birthday",
    "birthdate",
    "ip",
    "password",
    "username",
    "login",
    "nid",
}

PATTERNS = {
    "email addresses": re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$"),
    "phone numbers": re.compile(r"^(\+\d{1,3}[\s.-]?)?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}$"),
    "IBANs": re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$"),
    "IP addresses": re.compile(r"^(\d{1,3}\.){3}\d{1,3}$"),
    "US social security numbers": re.compile(r"^\d{3}-\d{2}-\d{4}$"),
}
PERSON_NAME = re.compile(r"^[A-Z][a-z'’-]+( [A-Z][a-z'’.-]+){1,2}$")


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch) * (2 if i % 2 else 1)
        total += d - 9 if d > 9 else d
    return total % 10 == 0


def personal_data(name: str, values: list[Any]) -> str | None:
    """Why the column holds personal data, or None."""
    words = set(tokens(name))
    if words & PERSONAL_WORDS:
        return f"its name mentions {', '.join(sorted(words & PERSONAL_WORDS))}"
    if "name" in words and words & PERSON:
        return "its name says it holds people's names"
    sample = [str(v).strip() for v in values[:200] if v is not None and str(v).strip()]
    if not sample:
        return None
    for label, pattern in PATTERNS.items():
        if sum(bool(pattern.match(v)) for v in sample) >= 0.6 * len(sample):
            return f"its values look like {label}"
    cards = [re.sub(r"[ -]", "", v) for v in sample]
    if sum(c.isdigit() and 13 <= len(c) <= 19 and _luhn(c) for c in cards) >= 0.6 * len(sample):
        return "its values look like card numbers"
    if words == {"name"} and sum(bool(PERSON_NAME.match(v)) for v in sample) >= 0.6 * len(sample):
        return "its values look like people's names"
    return None


def classify(
    name: str, kind: str, series: pd.Series, unit: str | None
) -> tuple[Role, Aggregation, str]:
    """The role of a column, how its values add up, and why."""
    words = set(tokens(name))
    values = series.dropna()
    if kind == "empty" or values.empty:
        return "empty", "none", "it has no values"
    if kind == "date":
        return "time", "none", "it holds dates"
    unique = values.nunique() >= 0.9 * len(values) and len(values) > 1
    id_reason = (
        "named like an id, and its values are unique"
        if unique
        else "named like an id; its values repeat, so it links rows to something else, "
        "like a customer"
    )
    if kind == "text":
        if id_like(name):
            return "identifier", "count_distinct", id_reason
        lengths = values.astype(str).str.len().mean()
        if words & FREE_TEXT or lengths > 40:
            return "free_text", "none", "it holds sentences, not categories"
        return "category", "count_distinct", "it holds labels that group the rows"

    # numbers
    integral = bool((values.astype(float) % 1 == 0).all())
    named = words & (SUM | AVERAGE | LAST | RATIO)
    if id_like(name) and integral and not words & SUM:
        return "identifier", "count_distinct", id_reason
    years = integral and bool(values.between(1900, 2100).all())
    if years and (words & YEAR or (values.nunique() <= 30 and not named)):
        return "time", "none", "it holds years"
    if integral and set(values.unique().tolist()) == {0, 1} and not words & SUM:
        return (
            "measure",
            "average",
            "a yes/no flag (0 or 1): its average is the share of rows where it is 1",
        )
    fractions = bool(values.between(-1, 1).all())
    if unit == "%" or words & RATIO or (words & MAYBE_RATIO and fractions):
        why = "its values are percentages" if unit == "%" else "its name says it is a rate"
        return "measure", "average", f"{why}; rates are averaged, never added up"
    if words & {"total", "sum", "amount"}:
        return "measure", "sum", "its name says it is an amount"
    if words & AVERAGE:
        return (
            "measure",
            "average",
            f"a {', '.join(sorted(words & AVERAGE))} is averaged, not added up",
        )
    if words & LAST:
        return (
            "measure",
            "last",
            f"a {', '.join(sorted(words & LAST))} is a level: over time, the last value counts",
        )
    if words & SUM or words & MAYBE_RATIO:
        return "measure", "sum", "its name says it adds up"
    return "measure", "unknown", "its name does not say whether it can be added up"


def profile(clean: Clean) -> list[ColumnProfile]:
    profiles = []
    frame = clean.frame
    for name in frame.columns:
        series = frame[name]
        kind = clean.kinds[name]
        unit = clean.units.get(name)
        role, aggregation, reason = classify(str(name), kind, series, unit)
        values = series.dropna()
        personal = personal_data(str(name), values.tolist()) if kind == "text" else None
        if personal is None and kind != "text" and set(tokens(str(name))) & PERSONAL_WORDS:
            personal = personal_data(str(name), [])
        p = ColumnProfile(
            name=str(name),
            source_name=clean.sources.get(name, ""),
            type=kind,
            role=role,
            aggregation=aggregation,
            reason=reason,
            unit=unit,
            count=len(values),
            missing=int(series.isna().sum()),
            distinct=int(values.nunique()),
            personal=personal is not None,
            personal_reason=personal,
            notes=list(clean.notes.get(name, [])),
        )
        if kind == "number" and len(values):
            floats = values.astype(float)
            p.min, p.max = float(floats.min()), float(floats.max())
            p.mean, p.total = float(floats.mean()), float(floats.sum())
        elif kind == "date" and len(values):
            p.min, p.max = show(values.min().to_pydatetime()), show(values.max().to_pydatetime())
        elif kind == "text" and len(values) and personal is None:
            top = values.astype(str).value_counts().head(5)
            p.top = [ValueCount(value=str(v), count=int(c)) for v, c in top.items()]
        if personal is None and len(values):
            p.examples = [
                show(v.to_pydatetime() if hasattr(v, "to_pydatetime") else v)
                for v in values.drop_duplicates().head(3).tolist()
            ]
        p.notes.extend(_quality(p, len(frame)))
        profiles.append(p)
    return profiles


def _quality(p: ColumnProfile, rows: int) -> list[str]:
    notes = []
    if p.personal:
        notes.append(
            f"Looks like personal data ({p.personal_reason}). It stays in the cleaned file, "
            "but its values are not shown in this report or sent to a model."
        )
    if rows > 1 and p.count == rows and p.distinct == 1:
        notes.append("Every row has the same value.")
    if rows and p.missing > rows / 2:
        notes.append(f"Mostly empty: {p.missing:,} of {rows:,} rows have no value.")
    if p.type == "number" and p.aggregation == "unknown":
        notes.append("Not clear whether these numbers can be added up.")
    return notes
