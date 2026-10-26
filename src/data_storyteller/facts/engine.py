"""Building the context (main number, measures, groups, time) and the facts about totals
and time: what the main number is, how it moves, and which periods are left out."""

from __future__ import annotations

import numpy as np
import pandas as pd

from data_storyteller.facts import breakdown, relations
from data_storyteller.facts.base import (
    MIN_N,
    Context,
    FactBook,
    Measure,
    build_time,
    confidence,
    fmt,
    in_list,
    lit,
    measure_for,
    q,
    sentence,
)
from data_storyteller.models import ColumnProfile, FactsReport, StorySetup

MAX_DIMENSION_MEMBERS = 20
MIN_ENTITIES = 20


def _noun(setup: StorySetup) -> str:
    grain = setup.grain
    if grain.startswith("one ") and "(" in grain:
        word = grain[4:].split("(")[0].strip()
        return word if word.endswith("s") else word + "s"
    return "rows"


def context(frame: pd.DataFrame, columns: list[ColumnProfile], setup: StorySetup) -> Context:
    usable = {c.name: c for c in columns if not c.personal}
    focus = set(setup.focus) or set(usable)
    noun = _noun(setup)
    count = Measure(name="", label=noun, aggregation="count")
    measures = [
        m for c in usable.values() if c.name in focus and (m := measure_for(c, setup)) is not None
    ]
    main = next((m for m in measures if m.name == setup.north_star), count)
    dimensions = [
        c.name
        for c in usable.values()
        if c.name in focus and c.role == "category" and 2 <= c.distinct <= MAX_DIMENSION_MEMBERS
    ]
    entities = [
        c.name
        for c in usable.values()
        if c.role == "identifier" and MIN_ENTITIES <= c.distinct < c.count
    ]
    time = None
    if setup.primary_date and setup.primary_date in usable:
        time = build_time(frame, setup.primary_date, usable[setup.primary_date])
    return Context(frame, usable, setup, main, count, measures, dimensions, entities, time, noun)


def compute(frame: pd.DataFrame, columns: list[ColumnProfile], setup: StorySetup) -> FactsReport:
    ctx = context(frame, columns, setup)
    book = FactBook()
    totals(ctx, book)
    if ctx.time is not None:
        over_time(ctx, book)
    breakdown.compositions(ctx, book)
    breakdown.concentration(ctx, book)
    breakdown.member_changes(ctx, book)
    relations.segment_gaps(ctx, book)
    relations.correlations(ctx, book)
    relations.anomalies(ctx, book)
    relations.simpsons(ctx, book)
    report = FactsReport(facts=book.facts)
    if ctx.time is not None:
        report.time = f"{ctx.time.column}, by {ctx.time.grain}"
        report.periods = ctx.time.full
        report.partial = list(ctx.time.partial)
    return report


# ---- totals -----------------------------------------------------------------------------


def totals(ctx: Context, book: FactBook) -> None:
    frame = ctx.frame
    n = len(frame)
    book.add(
        kind="count",
        title=f"Number of {ctx.noun}",
        statement=f"The data has {n:,} {ctx.noun}.",
        value=float(n),
        format="count",
        method="Rows in the cleaned table.",
        n=n,
        confidence="high",
        importance=0.5,
        sql="SELECT CAST(COUNT(*) AS DOUBLE) FROM data",
    )
    for entity in ctx.entities:
        distinct = int(frame[entity].nunique())
        book.add(
            kind="count",
            title=f"Distinct {entity}",
            statement=f"The {n:,} {ctx.noun} involve {distinct:,} different {entity} values.",
            metric=entity,
            value=float(distinct),
            format="count",
            method=f"Different values of {entity}.",
            n=n,
            confidence="high",
            importance=0.4,
            sql=f"SELECT CAST(COUNT(DISTINCT {q(entity)}) AS DOUBLE) FROM data",
        )

    latest = None
    if ctx.time is not None and ctx.time.full:
        latest = ctx.time.full[-1]
    for m in ctx.measures:
        main = m is ctx.main
        rows = frame
        where = "TRUE"
        scope = ""
        if m.aggregation == "last" and latest is not None and ctx.time is not None:
            rows = frame[ctx.time.labels == latest]
            where = f"{ctx.time.sql} = {lit(latest)}"
            scope = f" in {latest}, the latest full period"
        value = m.compute(rows)
        if value is None:
            continue
        kind = "average" if m.aggregation == "average" else "total"
        verb = "averaged" if kind == "average" else "came to"
        book.add(
            kind=kind,
            title=f"{'Average' if kind == 'average' else 'Total'} {m.label}{scope}",
            statement=f"{sentence(m.label)} {verb} {fmt(value, m.format, m.unit)}{scope}.",
            metric=m.name,
            period=latest if scope else None,
            value=value,
            format=m.format,
            unit=m.unit,
            method=f"{sentence(m.describe())} over every row{scope}.",
            n=m.n(rows),
            confidence=confidence(m.n(rows)),
            importance=0.9 if main else 0.4,
            sql=f"SELECT {m.sql(where)} FROM data",
        )


# ---- time -------------------------------------------------------------------------------


def _series(ctx: Context, m: Measure, periods: list[str], frame: pd.DataFrame | None = None):  # type: ignore[no-untyped-def]
    assert ctx.time is not None
    frame = ctx.frame if frame is None else frame
    labels = ctx.time.labels.loc[frame.index]
    values = []
    for p in periods:
        rows = frame[labels == p]
        values.append((p, m.compute(rows), m.n(rows)))
    return values


def _points_sql(ctx: Context, m: Measure, periods: list[str], extra: str = "TRUE") -> str:
    assert ctx.time is not None
    return (
        f"SELECT {ctx.time.sql} AS label, {m.sql(extra)} AS value FROM data "
        f"WHERE {ctx.time.sql} IN {in_list(periods)} GROUP BY 1 ORDER BY 1"
    )


def over_time(ctx: Context, book: FactBook) -> None:
    time = ctx.time
    assert time is not None
    for period, why in time.partial.items():
        rows = int((time.labels == period).sum())
        book.add(
            kind="partial_period",
            title=f"{period} is incomplete",
            statement=f"{period} is left out of comparisons: {why}.",
            period=period,
            value=float(rows),
            format="count",
            method=f"Days with data in {period} against its length, and its rows against the "
            "other periods.",
            n=rows,
            confidence="high",
            caveats=[f"Totals that include {period} cover only part of it."],
            importance=0.6,
            sql=f"SELECT CAST(COUNT(*) AS DOUBLE) FROM data WHERE {time.sql} = {lit(period)}",
        )

    for m in [ctx.main] + ([ctx.count] if ctx.main is not ctx.count else []):
        series = _series(ctx, m, time.full)
        points = [{"label": p, "value": v, "n": n} for p, v, n in series if v is not None]
        if len(points) < 2:
            continue
        main = m is ctx.main
        title = f"{sentence(m.label)} by {time.grain}"
        last, previous = points[-1], points[-2]
        book.add(
            kind="series",
            title=title,
            statement=f"{title}, from {points[0]['label']} to {last['label']}.",
            metric=m.name or None,
            value=last["value"],
            format=m.format,
            unit=m.unit,
            points=points,
            method=f"{sentence(m.describe())} in each full {time.grain}.",
            n=sum(int(p["n"]) for p in points),
            confidence="high",
            importance=0.8 if main else 0.5,
            points_sql=_points_sql(ctx, m, time.full),
        )
        _change(ctx, book, m, [previous["label"]], [last["label"]], main, "the period before")
        if time.grain == "month":
            year_ago = f"{int(last['label'][:4]) - 1}{last['label'][4:]}"
            if year_ago in time.full:
                _change(ctx, book, m, [year_ago], [last["label"]], main, "a year before")
        windows = time.windows()
        if windows and len(time.full) >= 4:
            first, final = windows
            _change(ctx, book, m, first, final, main, "the start of the data")
        if len(points) >= 4 and main:
            _trend(ctx, book, m, points)


def _span(periods: list[str]) -> str:
    return periods[0] if len(periods) == 1 else f"{periods[0]} to {periods[-1]}"


def _change(  # noqa: PLR0913
    ctx: Context,
    book: FactBook,
    m: Measure,
    before: list[str],
    after: list[str],
    main: bool,
    against: str,
) -> None:
    assert ctx.time is not None
    labels = ctx.time.labels
    old_rows = ctx.frame[labels.isin(before)]
    new_rows = ctx.frame[labels.isin(after)]
    old, new = m.compute(old_rows), m.compute(new_rows)
    if old is None or new is None or old == 0:
        return
    change = (new - old) / abs(old)  # both windows have the same number of periods
    n = m.n(old_rows) + m.n(new_rows)
    a, b = _span(before), _span(after)
    direction = "rose" if change > 0 else "fell" if change < 0 else "held"
    old_sql = m.sql(f"{ctx.time.sql} IN {in_list(before)}")
    new_sql = m.sql(f"{ctx.time.sql} IN {in_list(after)}")
    book.add(
        kind="period_change",
        title=f"{sentence(m.label)}: {a} against {b}",
        statement=f"{sentence(m.label)} {direction} {fmt(abs(change), 'percent')} from "
        f"{fmt(old, m.format, m.unit)} in {a} to {fmt(new, m.format, m.unit)} in {b}.",
        metric=m.name or None,
        period=f"{a} vs {b}",
        value=change,
        format="change",
        unit=m.unit,
        components={"before": old, "after": new, "before_period": a, "after_period": b},
        method=f"{sentence(m.describe())} in {b}, compared with {against} ({a}).",
        n=n,
        confidence=confidence(min(m.n(old_rows), m.n(new_rows))),
        importance=(0.85 if main else 0.5) * min(1.0, 0.4 + abs(change)),
        sql=f"SELECT ({new_sql} - {old_sql}) / abs({old_sql}) FROM data",
    )


def _trend(ctx: Context, book: FactBook, m: Measure, points: list[dict]) -> None:  # type: ignore[type-arg]
    assert ctx.time is not None
    y = np.array([p["value"] for p in points], dtype="float64")
    x = np.arange(len(y), dtype="float64")
    slope, intercept = np.polyfit(x, y, 1)
    fitted = slope * x + intercept
    total = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - float(((y - fitted) ** 2).sum()) / total if total else 0.0
    mean = float(y.mean())
    per_period = float(slope) / abs(mean) if mean else 0.0
    steady = r2 >= 0.5 and abs(per_period) >= 0.01
    direction = (
        ("grew steadily" if slope > 0 else "fell steadily") if steady else "had no steady trend"
    )
    grain = ctx.time.grain
    book.add(
        kind="trend",
        title=f"Trend in {m.label}",
        statement=f"Over {len(y)} {grain}s, {m.label} {direction}"
        + (f", by about {fmt(abs(per_period), 'percent')} a {grain}" if steady else "")
        + f" (the line explains {r2:.0%} of the ups and downs).",
        metric=m.name or None,
        period=_span([p["label"] for p in points]),
        value=float(slope),
        format=m.format if m.format != "count" else "number",
        unit=m.unit,
        components={"per_period": per_period, "r2": r2, "periods": float(len(y))},
        method=f"A straight line fitted to {m.describe()} in each full {grain}.",
        n=sum(int(p["n"] or 0) for p in points),
        confidence="high" if len(y) >= 6 and r2 >= 0.5 else "medium" if len(y) >= 4 else "low",
        caveats=[] if steady else [f"The {grain}ly values move without a clear direction."],
        importance=0.75 if steady else 0.3,
        sql=(
            f"WITH s AS ({_points_sql(ctx, m, [p['label'] for p in points])}) "
            "SELECT regr_slope(value, i) FROM "
            "(SELECT value, CAST(row_number() OVER (ORDER BY label) - 1 AS DOUBLE) AS i FROM s)"
        ),
    )


__all__ = ["MIN_N", "compute", "context"]
