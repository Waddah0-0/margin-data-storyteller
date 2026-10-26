"""Where the main number comes from: its split by group, how concentrated it is among
customers (or any repeating id), and which groups gained or lost the most."""

from __future__ import annotations

import math

from scipy import stats

from data_storyteller.facts.base import (
    MIN_N,
    Context,
    FactBook,
    Measure,
    confidence,
    fmt,
    in_list,
    lit,
    q,
    readable,
    sentence,
)
from data_storyteller.facts.relations import adjust

TOP_SHARE = 0.2  # concentration: the share of the main number from the top fifth
MIN_CHANGE = 0.10  # a group's change worth a fact
MAX_MOVERS = 3


def compositions(ctx: Context, book: FactBook) -> None:
    m = ctx.main
    if not m.additive:
        return
    total = m.compute(ctx.frame)
    if not total:
        return
    for dim in ctx.dimensions:
        groups = ctx.frame.groupby(dim, dropna=True, observed=True)
        rows = []
        for member, part in groups:
            value = m.compute(part)
            if value is not None:
                rows.append((str(member), value, m.n(part)))
        if len(rows) < 2:
            continue
        rows.sort(key=lambda r: r[1], reverse=True)
        top, value, n = rows[0]
        share = value / total
        even = 1 / len(rows)
        hhi = sum((v / total) ** 2 for _, v, _ in rows)
        book.add(
            kind="breakdown",
            title=f"{sentence(m.label)} by {dim}",
            statement=f"{top} is the largest {dim}, with {fmt(share, 'percent')} of "
            f"{m.label} ({fmt(value, m.format, m.unit)}).",
            metric=m.name or None,
            dimension=dim,
            member=top,
            value=share,
            format="percent",
            unit=m.unit,
            components={"top_value": value, "total": total, "concentration_index": hhi},
            points=[{"label": k, "value": v, "n": c} for k, v, c in rows],
            method=f"{sentence(m.describe())} for each {dim}, as a share of the total.",
            n=sum(c for _, _, c in rows),
            confidence=confidence(n),
            importance=min(1.0, 0.35 + max(0.0, share - even)),
            sql=f"SELECT {m.sql(f'{q(dim)} = {lit(top)}')} / {m.sql()} FROM data",
            points_sql=f"SELECT CAST({q(dim)} AS VARCHAR) AS label, {m.sql()} AS value FROM data "
            f"WHERE {q(dim)} IS NOT NULL GROUP BY 1",
        )


def concentration(ctx: Context, book: FactBook) -> None:
    m = ctx.main
    if not m.additive:
        return
    for entity in ctx.entities:
        sums = ctx.frame.groupby(entity, dropna=True)
        values = sorted((v for _, part in sums if (v := m.compute(part)) is not None), reverse=True)
        total = sum(values)
        if len(values) < 20 or total <= 0:
            continue
        k = math.ceil(len(values) * TOP_SHARE)
        share = sum(values[:k]) / total
        running, needed = 0.0, 0
        for v in values:
            running += v
            needed += 1
            if running >= 0.8 * total:
                break
        concentrated = share >= 0.5
        e = q(entity)
        book.add(
            kind="concentration",
            title=f"{sentence(m.label)} by {entity}: the top {TOP_SHARE:.0%}",
            statement=f"The top {TOP_SHARE:.0%} of {entity} values ({k:,} of {len(values):,}) "
            f"bring {fmt(share, 'percent')} of {m.label}; {needed:,} of them bring 80%.",
            metric=m.name or None,
            dimension=entity,
            value=share,
            format="percent",
            unit=m.unit,
            components={"top": float(k), "entities": float(len(values)), "for_80": float(needed)},
            method=f"{sentence(m.describe())} for each {entity}, largest first; the share "
            f"of the largest {TOP_SHARE:.0%}.",
            n=len(ctx.frame),
            confidence="high" if len(values) >= 50 else "medium",
            importance=0.8 if concentrated else 0.4,
            sql=(
                f"WITH e AS (SELECT {e} AS k, {m.sql()} AS v FROM data WHERE {e} IS NOT NULL "
                f"GROUP BY 1), r AS (SELECT v, row_number() OVER (ORDER BY v DESC) AS i FROM e) "
                f"SELECT SUM(v) FILTER (WHERE i <= {k}) / SUM(v) FROM r"
            ),
        )


def member_changes(ctx: Context, book: FactBook) -> None:
    """For each group, the main number at the start of the data against the end: its total,
    and (for a summed number) its average per row, like the value of an average order. A
    group's total can hold steady while each order shrinks, because there were more orders.
    """
    time = ctx.time
    m = ctx.main
    if time is None or not m.additive:
        return
    windows = time.windows()
    if windows is None:
        return
    first, last = windows
    before = ctx.frame[time.labels.isin(first)]
    after = ctx.frame[time.labels.isin(last)]
    variants = [(m, False)]
    if m.aggregation == "sum":
        per_row = Measure(
            m.name, f"average {m.label} per {singular(ctx.noun)}", "average", unit=m.unit
        )
        variants.append((per_row, True))
    for measure, tested in variants:
        moves = []
        for dim in ctx.dimensions:
            for member in sorted(ctx.frame[dim].dropna().astype(str).unique()):
                old_rows = before[before[dim].astype(str) == member]
                new_rows = after[after[dim].astype(str) == member]
                old, new = measure.compute(old_rows), measure.compute(new_rows)
                n_old, n_new = measure.n(old_rows), measure.n(new_rows)
                if not old or new is None or min(n_old, n_new) < MIN_N:
                    continue
                change = (new - old) / abs(old)
                p = 0.0
                if tested:
                    p = float(stats.ttest_ind(
                        new_rows[m.name].dropna().astype("float64"),
                        old_rows[m.name].dropna().astype("float64"),
                        equal_var=False,
                    ).pvalue)  # fmt: skip
                moves.append((dim, member, change, old, new, n_old + n_new, p))
        q_values = adjust([x[-1] for x in moves]) if tested and moves else [0.0] * len(moves)
        kept = [x for x, qv in zip(moves, q_values, strict=True)
                if abs(x[2]) >= MIN_CHANGE and qv < 0.05]  # fmt: skip
        for dim in ctx.dimensions:
            mine = [x for x in kept if x[0] == dim]
            gainers = sorted((x for x in mine if x[2] > 0), key=lambda x: -x[2])[:MAX_MOVERS]
            losers = sorted((x for x in mine if x[2] < 0), key=lambda x: x[2])[:MAX_MOVERS]
            for move in gainers + losers:
                _member_fact(ctx, book, measure, move, first, last, tested)


def _member_fact(  # noqa: PLR0913
    ctx: Context,
    book: FactBook,
    m: Measure,
    move: tuple[str, str, float, float, float, int, float],
    first: list[str],
    last: list[str],
    tested: bool,
) -> None:
    assert ctx.time is not None
    dim, member, change, old, new, n, _ = move
    a = first[0] if len(first) == 1 else f"{first[0]} to {first[-1]}"
    b = last[0] if len(last) == 1 else f"{last[0]} to {last[-1]}"
    word = ("rose" if change > 0 else "fell") if tested else ("grew" if change > 0 else "shrank")
    where = f"{q(dim)} = {lit(member)} AND {ctx.time.sql} IN "
    old_sql = m.sql(where + in_list(first))
    new_sql = m.sql(where + in_list(last))
    book.add(
        kind="member_change",
        title=f"{sentence(m.label)} for {member} ({readable(dim)}), {a} against {b}",
        statement=f"{sentence(m.label)} for {member} {word} {fmt(abs(change), 'percent')}, "
        f"from {fmt(old, m.format, m.unit)} in {a} to {fmt(new, m.format, m.unit)} in {b}.",
        metric=m.name or None,
        dimension=dim,
        member=member,
        period=f"{a} vs {b}",
        value=change,
        format="change",
        unit=m.unit,
        components={"before": old, "after": new},
        method=f"{sentence(m.describe())} for {member} in {b}, against {a}: the first and last "
        "thirds of the full periods"
        + ("; a Welch t-test, corrected for the number of groups." if tested else "."),
        n=n,
        confidence=confidence(n),
        importance=min(1.0, 0.45 + abs(change)),
        sql=f"SELECT ({new_sql} - {old_sql}) / abs({old_sql}) FROM data",
    )


def singular(noun: str) -> str:
    return noun[:-1] if noun.endswith("s") and noun != "rows" else "row" if noun == "rows" else noun
