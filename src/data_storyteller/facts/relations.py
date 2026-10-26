"""What moves together, what stands out, and the trap that reverses a trend.

Every test here runs on many pairs or groups at once, so p-values are corrected for the
number of tests (Benjamini-Hochberg): with fifty comparisons, a few look significant by
chance. Small groups (fewer than 30 rows) are never compared. Relationships are stated
as associations, never as causes.
"""

from __future__ import annotations

import calendar
import math
from itertools import combinations
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from data_storyteller.facts.base import (
    MIN_N,
    Context,
    FactBook,
    Grain,
    Measure,
    confidence,
    fmt,
    in_list,
    lit,
    period_labels,
    period_sql,
    q,
    readable,
    sentence,
)

ALPHA = 0.05
MIN_R = 0.3  # weaker correlations are not worth a sentence, however significant
MIN_GAP = 1.5  # a group at least 1.5 times (or at most 1/1.5 of) the rest
ROBUST_Z = 3.5
MAX_ANOMALIES = 6
MAX_SEGMENT_MEMBERS = 10


def adjust(p_values: list[float]) -> list[float]:
    """Benjamini-Hochberg q-values, in the input order."""
    n = len(p_values)
    order = sorted(range(n), key=lambda i: p_values[i])
    adjusted = [0.0] * n
    running = 1.0
    for rank in range(n, 0, -1):
        i = order[rank - 1]
        running = min(running, p_values[i] * n / rank)
        adjusted[i] = running
    return adjusted


def _rates(ctx: Context) -> list[Measure]:
    return [m for m in ctx.measures if m.aggregation == "average"]


# ---- groups that differ -------------------------------------------------------------------


def segment_gaps(ctx: Context, book: FactBook) -> None:
    """A group whose rate (or average) is far from the rest: one carrier damaging four
    times as many parcels."""
    found = []
    for m in _rates(ctx):
        for dim in ctx.dimensions:
            column = ctx.frame[dim].astype("string")
            for member in sorted(column.dropna().unique()):
                inside = ctx.frame[column == member]
                outside = ctx.frame[column.notna() & (column != member)]
                a, b = inside[m.name].dropna(), outside[m.name].dropna()
                if len(a) < MIN_N or len(b) < MIN_N:
                    continue
                va, vb = m.compute(inside), m.compute(outside)
                if va is None or vb is None or vb == 0:
                    continue
                if m.flag:
                    p = _two_proportions(float(a.mean()), len(a), float(b.mean()), len(b))
                else:
                    p = float(stats.ttest_ind(a, b, equal_var=False).pvalue)
                found.append((m, dim, str(member), va, vb, len(a), len(b), p))
    if not found:
        return
    for (m, dim, member, va, vb, na, nb, _), q_value in zip(
        found, adjust([f[-1] for f in found]), strict=True
    ):
        ratio = va / vb
        if q_value >= ALPHA or MIN_GAP > ratio > 1 / MIN_GAP:
            continue
        group = readable(dim)
        mine, rest = fmt(va, m.format, m.unit), fmt(vb, m.format, m.unit)
        statement = (
            f"For {member}, {m.label} is {mine}: {fmt(ratio, 'ratio')} the {rest} of the other "
            f"{group} values."
            if ratio > 1
            else f"For {member}, {m.label} is {mine}, against {rest} for the other {group} "
            f"values: {fmt(1 / ratio, 'ratio')} lower."
        )
        inside, outside = f"{q(dim)} = {lit(member)}", f"{q(dim)} <> {lit(member)}"
        book.add(
            kind="segment_gap",
            title=f"{sentence(m.label)} for {member} against other {group} values",
            statement=statement,
            metric=m.name,
            dimension=dim,
            member=member,
            value=ratio,
            format="ratio",
            unit=m.unit,
            metric_format=m.format,
            metric_label=m.label,
            components={"member": va, "rest": vb, "inverse": 1 / ratio, "q_value": q_value},
            method=f"{sentence(m.describe())} for {member} against all other {dim} values; "
            f"{'a two-proportion test' if m.flag else 'a Welch t-test'}, corrected for the "
            "number of comparisons.",
            n=na + nb,
            confidence=confidence(min(na, nb)),
            caveats=["A difference between groups, not proof of what causes it."],
            importance=min(1.0, 0.5 + abs(math.log(ratio)) / 3),
            sql=f"SELECT {m.sql(inside)} / {m.sql(outside)} FROM data",
        )


def _two_proportions(p1: float, n1: int, p2: float, n2: int) -> float:
    pooled = (p1 * n1 + p2 * n2) / (n1 + n2)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if se == 0:
        return 1.0
    return float(2 * stats.norm.sf(abs(p1 - p2) / se))


# ---- numbers that move together -----------------------------------------------------------


def correlations(ctx: Context, book: FactBook) -> None:
    usable = [m for m in ctx.measures if m.name and m.name != (ctx.time and ctx.time.column)]
    tests = []
    for x, y in combinations(usable, 2):
        if y.weight == x.name or x.weight == y.name:
            continue
        pair = ctx.frame[[x.name, y.name]].dropna().astype("float64")
        if len(pair) < MIN_N or pair[x.name].nunique() < 3 and pair[y.name].nunique() < 3:
            continue
        if pair[x.name].std() == 0 or pair[y.name].std() == 0:
            continue
        r, p = stats.pearsonr(pair[x.name], pair[y.name])
        rho = stats.spearmanr(pair[x.name], pair[y.name]).statistic
        tests.append((x, y, float(r), float(p), float(rho), len(pair)))
    if not tests:
        return
    for (x, y, r, _, rho, n), q_value in zip(tests, adjust([t[3] for t in tests]), strict=True):
        if q_value >= ALPHA or abs(r) < MIN_R:
            continue
        strength = "strongly" if abs(r) >= 0.6 else "moderately"
        together = "rise together" if r > 0 else "move in opposite directions"
        if x.flag and not y.flag:
            x, y = y, x
        if y.flag:
            often = "more" if r > 0 else "less"
            tendency = f"are {often} often {y.label.removeprefix('share ')}"
        else:
            tendency = f"tend to have {'higher' if r > 0 else 'lower'} {y.label}"
        book.add(
            kind="correlation",
            title=f"{x.label} and {y.label}",
            statement=f"{sentence(x.label)} and {y.label} {strength} {together} "
            f"(r = {fmt(r, 'number')}, {fmt(n, 'count')} rows): rows with higher {x.label} "
            f"{tendency}.",
            metric=x.name,
            dimension=y.name,
            value=r,
            format="number",
            components={"spearman": rho, "q_value": q_value}
            | ({"both_sums": 1.0} if x.additive and y.additive else {}),
            method="Pearson correlation over rows with both values, checked with Spearman's "
            "rank correlation, corrected for the number of pairs tested.",
            n=n,
            confidence=confidence(n),
            caveats=["An association, not proof that one causes the other."],
            # two summed numbers (sales and profit) move together by construction: less telling
            importance=min(1.0, 0.3 + abs(r) * 0.6) * (0.5 if x.additive and y.additive else 1),
            sql=f"SELECT corr(CAST({q(x.name)} AS DOUBLE), CAST({q(y.name)} AS DOUBLE)) FROM data "
            f"WHERE {q(x.name)} IS NOT NULL AND {q(y.name)} IS NOT NULL",
        )


# ---- what stands out ----------------------------------------------------------------------


def _robust_z(values: np.ndarray) -> np.ndarray:
    median = np.median(values)
    mad = np.median(np.abs(values - median))
    if mad == 0:
        return np.zeros_like(values)
    return np.asarray(0.6745 * (values - median) / mad, dtype="float64")


FINER: dict[str, Grain] = {"year": "month", "quarter": "month", "month": "week", "week": "day"}


def _scales(ctx: Context) -> list[tuple[pd.Series, list[str], str, Grain]]:
    """The time scales to scan: the axis itself, and the next finer grain (a nine-day spike
    disappears in a month). Edge periods of the finer grain, and those inside a partial
    period, are left out: they are partial too."""
    time = ctx.time
    assert time is not None
    scales = [(time.labels, time.full, time.sql, time.grain)]
    fine = FINER.get(time.grain)
    if fine is not None and not time.numeric:
        labels = period_labels(ctx.frame[time.column], fine, False)
        inside = labels[time.labels.isin(time.full)]
        outside = set(labels[~time.labels.isin(time.full)].dropna())
        periods = sorted(p for p in set(inside.dropna()) if p not in outside)[1:-1]
        if len(periods) >= 8:
            scales.append((labels, periods, period_sql(time.column, fine, False), fine))
    return scales


def _days(label: str, grain: Grain) -> int:
    if grain == "month":
        return calendar.monthrange(int(label[:4]), int(label[5:7]))[1]
    if grain == "quarter":
        year, quarter = int(label[:4]), int(label[-1])
        months = range(3 * quarter - 2, 3 * quarter + 1)
        return sum(calendar.monthrange(year, m)[1] for m in months)
    if grain == "year":
        return 366 if calendar.isleap(int(label[:4])) else 365
    return 7 if grain == "week" else 1


def anomalies(ctx: Context, book: FactBook) -> None:
    """Periods far from the usual, for the main number, the row count, and each group.
    Sums are compared per day of each period, so February is not "low" for being short;
    each series keeps only its most striking period."""
    best: dict[tuple[str, str | None, str | None], tuple[float, dict[str, Any]]] = {}
    time = ctx.time
    if time is not None and len(time.full) >= 6:
        series_sets: list[tuple[Measure, str | None, str | None]] = [(ctx.main, None, None)]
        if ctx.count is not ctx.main:
            series_sets.append((ctx.count, None, None))
        for name in ctx.dimensions:
            if ctx.frame[name].nunique() <= 12 and ctx.main.additive:
                for value in sorted(ctx.frame[name].dropna().astype(str).unique()):
                    series_sets.append((ctx.main, name, value))
        for labels_all, periods, period_expr, grain in _scales(ctx):
            for m, dim, member in series_sets:
                rows = ctx.frame
                if dim is not None:
                    rows = ctx.frame[ctx.frame[dim].astype(str) == member]
                labels = labels_all.loc[rows.index]
                values = np.array(
                    [m.compute(rows[labels == p]) or 0.0 for p in periods], dtype="float64"
                )
                days = np.array(
                    [_days(p, grain) if m.additive else 1 for p in periods], dtype="float64"
                )
                z = _robust_z(values / days)
                i = int(np.argmax(np.abs(z)))
                if abs(z[i]) < ROBUST_Z:
                    continue
                key = (m.name or "rows", dim, member)
                if key in best and best[key][0] >= abs(z[i]):
                    continue
                period = periods[i]
                where = f"{period_expr} = {lit(period)}"
                if dim is not None:
                    where += f" AND {q(dim)} = {lit(member or '')}"
                usual = float(np.median(values / days) * days[i])
                best[key] = (float(abs(z[i])), {
                    "m": m, "dim": dim, "member": member, "period": period, "grain": grain,
                    "value": float(values[i]), "usual": usual, "z": float(z[i]),
                    "where": where, "n": int((labels == period).sum()),
                })  # fmt: skip
    for _, f in sorted(best.values(), key=lambda b: -b[0])[:MAX_ANOMALIES]:
        m = f["m"]
        who = f" for {f['member']} ({readable(f['dim'])})" if f["dim"] else ""
        high = f["z"] > 0
        times = f["value"] / f["usual"] if f["usual"] else None
        size = f" ({fmt(times, 'ratio')} the usual)" if times and high else ""
        per_day = " per day" if m.additive and f["grain"] != "day" else ""
        book.add(
            kind="anomaly",
            title=f"{sentence(m.label)}{who} in {f['period']}",
            statement=(
                f"{f['period']} had {fmt(f['value'], m.format, m.unit)} {m.label}{who}, "
                if m.aggregation == "count"
                else f"{sentence(m.label)}{who} in {f['period']} was "
                f"{fmt(f['value'], m.format, m.unit)}, "
            )
            + f"{'far above' if high else 'far below'} the usual "
            f"{fmt(f['usual'], m.format, m.unit)}{size}.",
            metric=m.name or None,
            dimension=f["dim"],
            member=f["member"],
            period=f["period"],
            value=f["value"],
            format=m.format,
            unit=m.unit,
            metric_format=m.format,
            metric_label=m.label,
            components={"usual": f["usual"], "robust_z": f["z"]}
            | ({"times": times} if times and high else {}),
            method=f"{sentence(m.describe())}{per_day} in each full {f['grain']}{who}, against "
            f"the median; a robust z-score of {f['z']:.1f}, where {ROBUST_Z} or more stands out.",
            n=f["n"],
            confidence=confidence(f["n"]),
            importance=min(1.0, 0.4 + abs(f["z"]) / 20),
            sql=f"SELECT {m.sql(f['where'])} FROM data",
        )
    _outlier_rows(ctx, book)


def _outlier_rows(ctx: Context, book: FactBook) -> None:
    m = ctx.main
    if m.aggregation != "sum":
        return
    values = ctx.frame[m.name].dropna().astype("float64")
    if len(values) < MIN_N:
        return
    q1, q3 = values.quantile(0.25), values.quantile(0.75)
    fence = q3 + 3 * (q3 - q1)
    above = values[values > fence]
    if above.empty or q3 == q1:
        return
    c = f"CAST({q(m.name)} AS DOUBLE)"
    book.add(
        kind="anomaly",
        title=f"Unusually large {m.label}",
        statement=f"{len(above):,} {ctx.noun} have unusually large {m.label} (over "
        f"{fmt(float(fence), m.format, m.unit)}); together they make "
        f"{fmt(float(above.sum() / values.sum()), 'percent')} of the total.",
        metric=m.name,
        value=float(len(above)),
        format="count",
        unit=m.unit,
        metric_format=m.format,
        metric_label=m.label,
        components={"threshold": float(fence), "share": float(above.sum() / values.sum())},
        method="Values above the third quartile plus three times the interquartile range.",
        n=len(values),
        confidence="high",
        importance=min(1.0, 0.35 + len(above) / len(values)),
        sql=(
            f"WITH f AS (SELECT quantile_cont({c}, 0.25) AS a, quantile_cont({c}, 0.75) AS b "
            f"FROM data) SELECT CAST(COUNT(*) AS DOUBLE) FROM data, f WHERE {c} > b + 3 * (b - a)"
        ),
    )


# ---- Simpson's paradox ------------------------------------------------------------------


def simpsons(ctx: Context, book: FactBook) -> None:
    """A rate that rises overall while falling in every group (or the reverse), because
    the mix of groups changed. The overall change is then misleading on its own."""
    time = ctx.time
    if time is None:
        return
    windows = time.windows()
    if windows is None:
        return
    first, last = windows
    early = ctx.frame[time.labels.isin(first)]
    late = ctx.frame[time.labels.isin(last)]
    a = first[0] if len(first) == 1 else f"{first[0]} to {first[-1]}"
    b = last[0] if len(last) == 1 else f"{last[0]} to {last[-1]}"
    for m in _rates(ctx):
        overall_before, overall_after = m.compute(early), m.compute(late)
        if overall_before is None or overall_after is None:
            continue
        overall = overall_after - overall_before
        if overall == 0 or abs(overall) < 0.02 * max(abs(overall_before), 1e-12):
            continue
        for dim in ctx.dimensions:
            if ctx.frame[dim].nunique() > MAX_SEGMENT_MEMBERS:
                continue
            moves = []
            for member in sorted(ctx.frame[dim].dropna().astype(str).unique()):
                before = early[early[dim].astype(str) == member]
                after = late[late[dim].astype(str) == member]
                if min(m.n(before), m.n(after)) < MIN_N:
                    continue
                vb, va = m.compute(before), m.compute(after)
                if vb is not None and va is not None:
                    moves.append((member, vb, va))
            if len(moves) < 2 or not all((va - vb) * overall < 0 for _, vb, va in moves):
                continue
            up = overall > 0
            groups = "; ".join(
                f"{member} {fmt(vb, m.format, m.unit)} to {fmt(va, m.format, m.unit)}"
                for member, vb, va in moves
            )
            in_window = f"{time.sql} IN "
            book.add(
                kind="simpsons",
                title=f"{sentence(m.label)} by {dim}: the overall change is misleading",
                statement=f"Overall, {m.label} {'rose' if up else 'fell'} from "
                f"{fmt(overall_before, m.format, m.unit)} in {a} to "
                f"{fmt(overall_after, m.format, m.unit)} in {b}, yet it "
                f"{'fell' if up else 'rose'} within every {dim} ({groups}). The mix of "
                f"{dim} values changed.",
                metric=m.name,
                dimension=dim,
                period=f"{a} vs {b}",
                value=overall,
                format="number",
                unit=m.unit,
                metric_format=m.format,
                metric_label=m.label,
                components={"before": overall_before, "after": overall_after}
                | {
                    f"m{i}_{part}": value
                    for i, (member, vb, va) in enumerate(moves, 1)
                    for part, value in (("name", member), ("before", vb), ("after", va))
                },
                method=f"{sentence(m.describe())} overall and within each {dim}, in the "
                "first and last thirds of the full periods (Simpson's paradox).",
                n=m.n(early) + m.n(late),
                confidence="high",
                caveats=[
                    f"Quote the overall change in {m.label} only together with the change "
                    f"within each {dim}."
                ],
                importance=0.95,
                sql=f"SELECT {m.sql(in_window + in_list(last))} - "
                f"{m.sql(in_window + in_list(first))} FROM data",
            )
