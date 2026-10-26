"""The facts engine against the golden datasets' planted truths, and its DuckDB check."""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pytest
from data_storyteller import cleaning, facts, setup
from data_storyteller.facts.base import Grain, period_labels, period_sql
from data_storyteller.facts.relations import adjust
from data_storyteller.ingest import read_source
from data_storyteller.models import Fact, FactsReport
from data_storyteller.profile import profile
from margin_core import Answer


@cache
def _run(path: str, **chosen: Any) -> tuple[pd.DataFrame, FactsReport]:
    plan = cleaning.plan(next(iter(read_source(path).tables.values())))
    clean = cleaning.apply(plan, {q.id: Answer.of(q, q.default, "default") for q in plan.questions})
    columns = profile(clean)
    asked = setup.questions(columns)
    answers = {
        q.id: Answer.of(q, chosen.get(q.id, q.default), "user" if q.id in chosen else "default")
        for q in asked
    }
    story, _ = setup.resolve(columns, answers)
    return clean.frame, facts.verify(clean.frame, facts.compute(clean.frame, columns, story))


def run(path: Path, **chosen: Any) -> FactsReport:
    return _run(str(path), **chosen)[1]


def find(report: FactsReport, kind: str, **match: Any) -> list[Fact]:
    return [
        f
        for f in report.facts
        if f.kind == kind and all(getattr(f, k) == v for k, v in match.items())
    ]


# ---- planted truths ---------------------------------------------------------------------


def test_ecommerce_partial_month_is_left_out(golden: dict[str, Path]) -> None:
    report = run(golden["ecommerce"])
    assert report.partial == ["2024-10"]
    assert "2024-10" not in report.periods and report.periods[-1] == "2024-09"
    (partial,) = find(report, "partial_period")
    assert "10 of its 31 days" in partial.statement
    for fact in find(report, "period_change") + find(report, "member_change"):
        assert "2024-10" not in (fact.period or "")


def test_ecommerce_category_growth_and_decline(golden: dict[str, Path]) -> None:
    report = run(golden["ecommerce"])
    technology = find(report, "member_change", member="Technology")
    assert technology and all(f.value > 0.15 for f in technology)
    furniture = find(report, "member_change", member="Furniture")
    per_order = [f for f in furniture if f.title.startswith("Average sales per order")]
    assert per_order and per_order[0].value < -0.10


def test_ecommerce_west_spike_in_late_august(golden: dict[str, Path]) -> None:
    report = run(golden["ecommerce"])
    west = find(report, "anomaly", member="West")
    assert west and west[0].period in {"2024-W33", "2024-W34", "2024-W35", "2024-08"}
    assert float(west[0].components["robust_z"]) > 3.5


def test_ecommerce_customers_follow_pareto(golden: dict[str, Path]) -> None:
    (pareto,) = find(run(golden["ecommerce"]), "concentration", dimension="customer_id")
    assert pareto.value >= 0.75  # the top 20% of customers bring about 80% of sales


def test_saas_discount_and_retention_move_opposite(golden: dict[str, Path]) -> None:
    report = run(golden["saas"])
    pair = [
        f
        for f in find(report, "correlation")
        if {f.metric, f.dimension} == {"discount_rate", "retention_months"}
    ]
    assert pair and pair[0].value < -0.6
    assert "not proof" in pair[0].caveats[0]


def test_saas_company_size_and_churn_are_not_reported(golden: dict[str, Path]) -> None:
    report = run(golden["saas"])
    for fact in find(report, "correlation"):
        assert {fact.metric, fact.dimension} != {"company_size", "churned"}


def test_saas_simpsons_paradox(golden: dict[str, Path]) -> None:
    (paradox,) = find(run(golden["saas"]), "simpsons", metric="churned", dimension="tier")
    assert paradox.value > 0  # overall churn rose...
    assert "fell within every tier" in paradox.statement  # ...while it fell in each tier
    assert paradox.caveats


def test_logistics_carrier_with_more_damage(golden: dict[str, Path]) -> None:
    gaps = find(run(golden["logistics"]), "segment_gap", metric="is_damaged", member="ApexExpress")
    assert gaps and gaps[0].value >= 2


def test_logistics_december_surge(golden: dict[str, Path]) -> None:
    report = run(golden["logistics"])
    december = [
        f
        for f in find(report, "anomaly")
        if f.dimension is None and (f.period or "").startswith(("2024-12", "2024-W5"))
    ]
    assert december and all(f.value > 2 * float(f.components["usual"]) for f in december)


# ---- the check --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["ecommerce", "saas", "logistics"])
def test_every_fact_is_recomputed_and_matches(golden: dict[str, Path], name: str) -> None:
    report = run(golden[name])
    assert len(report.facts) >= 15
    checked = [f for f in report.facts if f.sql or f.points_sql]
    assert checked == report.facts and all(f.checked for f in checked)
    assert "all agree within 0.0001" in report.check[0]


def test_a_wrong_number_fails_the_run(golden: dict[str, Path]) -> None:
    frame, report = _run(str(golden["logistics"]))
    broken = report.model_copy(deep=True)
    broken.facts[2].value *= 1.01
    with pytest.raises(facts.FactsCheckError, match=broken.facts[2].id):
        facts.verify(frame, broken)


def test_small_numbers_are_low_confidence(golden: dict[str, Path]) -> None:
    for fact in run(golden["ecommerce"]).facts:
        if fact.n < 30:
            assert fact.confidence == "low", fact.statement


# ---- pieces -----------------------------------------------------------------------------


GRAINS: list[Grain] = ["day", "week", "month", "quarter", "year"]


@pytest.mark.parametrize("grain", GRAINS)
def test_period_labels_match_in_pandas_and_sql(grain: Grain) -> None:
    dates = pd.Series(pd.date_range("2023-12-25", "2025-01-08", freq="37h"), name="d")
    in_pandas = period_labels(dates, grain, False).tolist()
    connection = duckdb.connect()
    connection.register("data", pd.DataFrame({"d": dates}))
    query = f"SELECT {period_sql('d', grain, False)} FROM data"
    assert in_pandas == [r[0] for r in connection.execute(query).fetchall()]


def test_benjamini_hochberg() -> None:
    # sorted: 0.01*4/1 = 0.04, 0.03*4/2 = 0.06, 0.04*4/3 = 0.0533, 0.2; then made monotone
    assert adjust([0.01, 0.04, 0.03, 0.2]) == pytest.approx([0.04, 0.16 / 3, 0.16 / 3, 0.2])
    assert adjust([]) == []
