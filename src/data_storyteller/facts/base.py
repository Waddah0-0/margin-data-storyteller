"""The facts engine's shared pieces: the time axis, measures, and the book facts go into.

Every number is computed twice, independently: once with pandas here, and once by a SQL
query written from the same definition and run in DuckDB (``verify``). A measure knows
both ways of computing itself; a time axis knows both ways of labelling a row's period.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
import pandas as pd

from data_storyteller.models import ColumnProfile, Fact, Point, StorySetup
from data_storyteller.names import tokens

Grain = Literal["day", "week", "month", "quarter", "year"]
Format = Literal["number", "percent", "change", "count", "ratio"]

MIN_N = 30  # fewer rows than this and a comparison is not trusted


def q(name: str) -> str:
    """A column name quoted for SQL."""
    return '"' + name.replace('"', '""') + '"'


def lit(value: str) -> str:
    """A text value quoted for SQL."""
    return "'" + value.replace("'", "''") + "'"


def in_list(values: list[str]) -> str:
    return "(" + ", ".join(lit(v) for v in values) + ")"


# ---- time -------------------------------------------------------------------------------


@dataclass
class TimeAxis:
    column: str
    grain: Grain
    numeric: bool  # a column of years, not dates
    labels: pd.Series  # the period of each row (None where the row has no time)
    periods: list[str]  # every period with data, in order
    full: list[str]  # the periods comparisons use
    partial: dict[str, str] = field(default_factory=dict)  # period -> why it is partial
    partial_numbers: dict[str, dict[str, float]] = field(default_factory=dict)

    @property
    def sql(self) -> str:
        return period_sql(self.column, self.grain, self.numeric)

    def windows(self) -> tuple[list[str], list[str]] | None:
        """The first and last thirds of the full periods (at least one period each)."""
        if len(self.full) < 2:
            return None
        k = max(1, len(self.full) // 3)
        return self.full[:k], self.full[-k:]


def choose_grain(dates: pd.Series) -> Grain:
    months = dates.dt.to_period("M").nunique()
    if months >= 25:
        return "quarter"
    if months >= 3:
        return "month"
    if (dates.max() - dates.min()).days >= 21:
        return "week"
    return "day"


def period_labels(series: pd.Series, grain: Grain, numeric: bool) -> pd.Series:
    if numeric:
        return series.map(lambda v: None if pd.isna(v) else str(int(v)))
    if grain == "week":
        iso = series.dt.isocalendar()
        labels = iso["year"].astype("string") + "-W" + iso["week"].astype("string").str.zfill(2)
    elif grain == "quarter":
        labels = series.dt.year.astype("string") + "-Q" + series.dt.quarter.astype("string")
    else:
        fmt = {"day": "%Y-%m-%d", "month": "%Y-%m", "year": "%Y"}[grain]
        labels = series.dt.strftime(fmt).astype("string")
    result: pd.Series = labels.astype(object).where(series.notna(), None)
    return result


def period_sql(column: str, grain: Grain, numeric: bool) -> str:
    c = q(column)
    if numeric:
        return f"CAST(CAST({c} AS BIGINT) AS VARCHAR)"
    if grain == "week":
        return (
            f"CAST(isoyear({c}) AS VARCHAR) || '-W' || "
            f"lpad(CAST(weekofyear({c}) AS VARCHAR), 2, '0')"
        )
    if grain == "quarter":
        return f"strftime({c}, '%Y') || '-Q' || CAST(quarter({c}) AS VARCHAR)"
    fmt = {"day": "%Y-%m-%d", "month": "%Y-%m", "year": "%Y"}[grain]
    return f"strftime({c}, '{fmt}')"


_FREQ = {"day": "D", "week": "W-SUN", "month": "M", "quarter": "Q", "year": "Y"}


def build_time(frame: pd.DataFrame, column: str, profile: ColumnProfile) -> TimeAxis | None:
    series = frame[column]
    if series.notna().sum() == 0:
        return None
    numeric = profile.type == "number"
    grain: Grain = "year" if numeric else choose_grain(series.dropna())
    labels = period_labels(series, grain, numeric)
    counts = labels.value_counts()
    periods = sorted(counts.index.tolist())
    axis = TimeAxis(column, grain, numeric, labels, periods, list(periods))
    if numeric or len(periods) < 3:
        return axis

    # A first or last period with only part of its days is left out of comparisons:
    # a partial month always looks like a drop.
    dates = series.dropna()
    spans = dates.dt.to_period(_FREQ[grain])
    for label, edge in ((periods[-1], "last"), (periods[0], "first")):
        inside = dates[labels.loc[dates.index] == label]
        span = spans.loc[inside.index].iloc[0]
        length = (span.end_time.normalize() - span.start_time.normalize()).days + 1
        if edge == "last":
            covered = (inside.max().normalize() - span.start_time.normalize()).days + 1
        else:
            covered = (span.end_time.normalize() - inside.min().normalize()).days + 1
        others = counts.drop(label)
        ratio = counts[label] / others.median()
        coverage = covered / length
        if coverage < 0.6 or (coverage < 0.9 and ratio < 0.8):
            days = "day" if covered == 1 else "days"
            median = float(others.median())
            axis.partial[label] = (
                f"it has data for only {fmt(covered, 'count')} of its {fmt(length, 'count')} "
                f"{days} ({fmt(counts[label], 'count')} rows, against a median of "
                f"{fmt(median, 'count')})"
            )
            axis.partial_numbers[label] = {
                "covered": float(covered),
                "length": float(length),
                "median": median,
            }
    axis.full = [p for p in periods if p not in axis.partial]
    return axis


# ---- measures ---------------------------------------------------------------------------


@dataclass
class Measure:
    """A number to compute over rows: a column summed or averaged, or a count of rows."""

    name: str  # the column, or "" for a count of rows
    label: str
    aggregation: Literal["sum", "average", "last", "count"]
    weight: str | None = None
    unit: str | None = None
    flag: bool = False  # a 0/1 column: its average is a share

    @property
    def format(self) -> Format:
        if self.aggregation == "count":
            return "count"
        if self.flag or self.unit == "%":
            return "percent"
        return "number"

    @property
    def additive(self) -> bool:
        return self.aggregation in ("sum", "count", "last")

    def compute(self, frame: pd.DataFrame) -> float | None:
        if self.aggregation == "count":
            return float(len(frame))
        values = frame[self.name].astype("float64")
        if self.aggregation in ("sum", "last"):
            return float(values.sum()) if values.notna().any() else None
        if self.weight:
            w = frame[self.weight].astype("float64")
            both = values.notna() & w.notna()
            total = float(w[both].sum())
            return float((values[both] * w[both]).sum() / total) if total else None
        return float(values.mean()) if values.notna().any() else None

    def n(self, frame: pd.DataFrame) -> int:
        if self.aggregation == "count":
            return len(frame)
        return int(frame[self.name].notna().sum())

    def sql(self, where: str = "TRUE") -> str:
        """An SQL aggregate over the rows matching ``where``."""
        if self.aggregation == "count":
            return f"CAST(COUNT(*) FILTER (WHERE {where}) AS DOUBLE)"
        c = q(self.name)
        if self.aggregation in ("sum", "last"):
            return f"CAST(SUM({c}) FILTER (WHERE {where}) AS DOUBLE)"
        if self.weight:
            w = q(self.weight)
            both = f"({where}) AND {c} IS NOT NULL AND {w} IS NOT NULL"
            return (
                f"(SUM(CAST({c} AS DOUBLE) * {w}) FILTER (WHERE {both}) / "
                f"NULLIF(SUM(CAST({w} AS DOUBLE)) FILTER (WHERE {both}), 0))"
            )
        return f"AVG(CAST({c} AS DOUBLE)) FILTER (WHERE {where})"

    def describe(self) -> str:
        if self.aggregation == "count":
            return f"the number of {self.label}"
        if self.weight:
            return f"{self.label}, averaged in proportion to {self.weight}"
        how = {"sum": "the sum of", "average": "the average of", "last": "the sum of"}
        return f"{how[self.aggregation]} {self.label}"


ACRONYMS = {"mrr", "arr", "gmv", "nps", "ctr", "kpi", "roi", "aov", "ltv", "cac", "sku", "id"}
FLAG_WORDS = {"is", "has", "was", "flag"}


def readable(name: str, flag: bool = False) -> str:
    """A column name as words: ``discount_rate`` reads "discount rate", ``mrr`` "MRR",
    and a 0/1 flag such as ``is_damaged`` reads "share damaged"."""
    words = tokens(name)
    if not words:
        return name
    if flag:
        kept = [w for w in words if w not in FLAG_WORDS] or words
        return "share " + " ".join(kept)
    return " ".join(w.upper() if w in ACRONYMS else w for w in words)


def sentence(text: str) -> str:
    """Capitalise the first letter only (a label like "MRR" keeps its capitals)."""
    return text[:1].upper() + text[1:]


def measure_for(profile: ColumnProfile, setup: StorySetup) -> Measure | None:
    aggregation = setup.aggregations.get(profile.name, profile.aggregation)
    if profile.role != "measure" or aggregation not in ("sum", "average", "last"):
        return None
    flag = "yes/no flag" in profile.reason
    return Measure(
        name=profile.name,
        label=readable(profile.name, flag),
        aggregation=aggregation,
        weight=setup.weights.get(profile.name),
        unit=profile.unit,
        flag=flag,
    )


# ---- formatting -------------------------------------------------------------------------


def fmt(value: float | None, format: Format, unit: str | None = None) -> str:
    """A number as the story shows it."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "no value"
    if format == "percent":
        return f"{value * 100:.1f}".removesuffix(".0") + "%"
    if format == "change":
        return f"{value * 100:+.1f}".removesuffix(".0") + "%"
    if format == "ratio":
        return f"{value:.1f} times"
    if format == "count":
        return f"{value:,.0f}"
    size = abs(value)
    if size >= 1e9:
        text = f"{value / 1e9:.2f}B"
    elif size >= 1e6:
        text = f"{value / 1e6:.2f}M"
    elif size >= 1e4:
        text = f"{value:,.0f}"
    elif size >= 1:
        text = f"{value:,.2f}".rstrip("0").rstrip(".")
    else:
        text = f"{value:.3g}"
    if unit and unit != "%":
        return f"{unit}{text}" if len(unit) == 1 else f"{text} {unit}"
    return text


def confidence(n: int, *, low_if: bool = False) -> Literal["high", "medium", "low"]:
    if low_if or n < MIN_N:
        return "low"
    if n < 100:
        return "medium"
    return "high"


# ---- the book ---------------------------------------------------------------------------


@dataclass
class Context:
    frame: pd.DataFrame
    columns: dict[str, ColumnProfile]
    setup: StorySetup
    main: Measure  # the north star, or a count of rows
    count: Measure
    measures: list[Measure]
    dimensions: list[str]
    entities: list[str]
    time: TimeAxis | None
    noun: str  # what one row is, plural: "orders"


@dataclass
class FactBook:
    facts: list[Fact] = field(default_factory=list)

    def add(self, **fields: Any) -> Fact:
        points = fields.pop("points", [])
        fact = Fact(
            id=f"F{len(self.facts) + 1:03d}",
            points=[p if isinstance(p, Point) else Point(**p) for p in points],
            **fields,
        )
        self.facts.append(fact)
        return fact


def clean_number(value: Any) -> float:
    """A plain float for numpy and pandas scalars alike."""
    return float(np.asarray(value, dtype="float64"))
