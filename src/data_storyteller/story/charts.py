"""Charts for the story's pages, drawn only from the page's facts.

A chart never reads the data again: its numbers are the facts' own points and components,
each already recomputed in DuckDB. The form follows the fact: a line for a number over
time, bars for a split by group, a dumbbell for groups before and after, a meter for one
share. One thing is emphasised (the group, period or overall figure the sentence is
about); the rest is context.
"""

from __future__ import annotations

from data_storyteller.facts.base import readable, sentence
from data_storyteller.models import Chart, ChartPoint, Fact, FactsReport, Story
from data_storyteller.story.plan import PagePlan, StoryPlan

MAX_CHARTS = 3
MAX_BARS = 8


def attach(story: Story, plan: StoryPlan, report: FactsReport) -> None:
    pages = {p.id: p for p in plan.pages}
    for page in story.pages:
        planned = pages.get(page.id)
        if planned is not None and planned.role != "actions":
            page.charts = build(planned, report)[:MAX_CHARTS]


def build(page: PagePlan, report: FactsReport) -> list[Chart]:
    charts: list[Chart] = []
    if page.role == "pulse":
        line = _pulse(page, report)
        if line is not None:
            charts.append(line)
    changes: dict[tuple[str | None, str | None, str | None], list[Fact]] = {}
    for fact in page.facts:
        if fact.kind == "member_change":
            key = (fact.dimension, fact.metric_label, fact.period)
            changes.setdefault(key, []).append(fact)
    drawn: set[tuple[str | None, str | None, str | None]] = set()
    for fact in page.facts:
        if fact.kind == "member_change":
            key = (fact.dimension, fact.metric_label, fact.period)
            if key not in drawn:
                drawn.add(key)
                charts.append(_changes(changes[key]))
            continue
        chart = BUILDERS.get(fact.kind, _none)(fact)
        if chart is not None:
            charts.append(chart)
    return charts


def _none(fact: Fact) -> Chart | None:
    return None


def _pulse(page: PagePlan, report: FactsReport) -> Chart | None:
    metric = page.headline.metric
    series = next(
        (f for f in report.facts if f.kind == "series" and f.metric == metric and f.points), None
    )
    if series is None:
        return None
    spike = {
        f.period
        for f in report.facts
        if f.kind == "anomaly" and f.metric == metric and f.dimension is None
    }
    left_out = ", ".join(report.partial)
    return Chart(
        kind="line",
        title=series.title,
        fact_id=series.id,
        format=series.metric_format,
        unit=series.unit,
        points=[
            ChartPoint(label=p.label, value=p.value, highlight=p.label in spike)
            for p in series.points
        ],
        note=f"{left_out} left out: incomplete." if left_out else None,
    )


def _breakdown(fact: Fact) -> Chart:
    points = sorted(fact.points, key=lambda p: -p.value)
    shown = points[:MAX_BARS]
    rest = sum(p.value for p in points[MAX_BARS:])
    bars = [
        ChartPoint(label=p.label, value=p.value, highlight=p.label == fact.member) for p in shown
    ]
    if rest:
        bars.append(ChartPoint(label=f"{len(points) - MAX_BARS} others", value=rest))
    return Chart(
        kind="bars",
        title=fact.title,
        fact_id=fact.id,
        format=fact.metric_format,
        unit=fact.unit,
        points=bars,
    )


def _concentration(fact: Fact) -> Chart:
    return Chart(
        kind="meter",
        title=fact.title,
        fact_id=fact.id,
        format="percent",
        points=[
            ChartPoint(
                label=f"Share from the top {fact.components.get('top', 0):,.0f} of "
                f"{fact.components.get('entities', 0):,.0f} "
                f"{readable(fact.dimension or '')} values",
                value=fact.value,
                highlight=True,
            )
        ],
    )


def _gap(fact: Fact) -> Chart:
    group = readable(fact.dimension or "group")
    return Chart(
        kind="bars",
        title=fact.title,
        fact_id=fact.id,
        format=fact.metric_format,
        unit=fact.unit,
        points=[
            ChartPoint(
                label=fact.member or "", value=float(fact.components["member"]), highlight=True
            ),
            ChartPoint(label=f"Other {group} values", value=float(fact.components["rest"])),
        ],
    )


def _windows(period: str | None) -> tuple[str | None, str | None]:
    if period and " vs " in period:
        before, after = period.split(" vs ", 1)
        return before, after
    return None, None


def _changes(facts: list[Fact]) -> Chart:
    first = facts[0]
    before, after = _windows(first.period)
    return Chart(
        kind="dumbbell",
        title=f"{sentence(first.metric_label or 'value')} by "
        f"{readable(first.dimension or 'group')}",
        fact_id=first.id,
        format=first.metric_format,
        unit=first.unit,
        points=[
            ChartPoint(
                label=f.member or "",
                before=float(f.components["before"]),
                value=float(f.components["after"]),
                highlight=f is first,
            )
            for f in facts
        ],
        before_label=before,
        after_label=after,
    )


def _simpsons(fact: Fact) -> Chart:
    before, after = _windows(fact.period)
    rows = [
        ChartPoint(
            label="Overall",
            before=float(fact.components["before"]),
            value=float(fact.components["after"]),
            highlight=True,
        )
    ]
    i = 1
    while f"m{i}_name" in fact.components:
        rows.append(
            ChartPoint(
                label=str(fact.components[f"m{i}_name"]),
                before=float(fact.components[f"m{i}_before"]),
                value=float(fact.components[f"m{i}_after"]),
            )
        )
        i += 1
    return Chart(
        kind="dumbbell",
        title=fact.title,
        fact_id=fact.id,
        format=fact.metric_format,
        unit=fact.unit,
        points=rows,
        before_label=before,
        after_label=after,
    )


def _anomaly(fact: Fact) -> Chart | None:
    if not fact.points:
        return None
    return Chart(
        kind="line",
        title=fact.title,
        fact_id=fact.id,
        format=fact.metric_format,
        unit=fact.unit,
        points=[
            ChartPoint(label=p.label, value=p.value, highlight=p.label == fact.period)
            for p in fact.points
        ],
    )


BUILDERS = {
    "breakdown": _breakdown,
    "concentration": _concentration,
    "segment_gap": _gap,
    "simpsons": _simpsons,
    "anomaly": _anomaly,
}
