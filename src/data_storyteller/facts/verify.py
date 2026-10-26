"""Recomputing every fact in DuckDB, from SQL written independently of the pandas code.

The two must agree within a relative 0.0001. A disagreement means a bug in one of them,
so the run fails rather than tell a story with a number that might be wrong.
"""

from __future__ import annotations

import math

import duckdb
import pandas as pd

from data_storyteller.models import FactsReport

TOLERANCE = 1e-4


class FactsCheckError(Exception):
    """A fact's number did not match its independent recomputation (a bug)."""


def _close(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if math.isnan(a) or math.isnan(b):
        return math.isnan(a) and math.isnan(b)
    return abs(a - b) <= TOLERANCE * max(abs(a), abs(b)) + 1e-9


def verify(frame: pd.DataFrame, report: FactsReport) -> FactsReport:
    connection = duckdb.connect()
    try:
        connection.register("data", frame)
        problems = []
        checked = values = 0
        for fact in report.facts:
            if fact.sql is None and fact.points_sql is None:
                continue
            ok = True
            if fact.sql is not None:
                row = connection.execute(fact.sql).fetchone()
                again = None if row is None or row[0] is None else float(row[0])
                values += 1
                if not _close(fact.value, again):
                    ok = False
                    problems.append(f"{fact.id} ({fact.title}): {fact.value} against {again}")
            if fact.points_sql is not None:
                rows = dict(connection.execute(fact.points_sql).fetchall())
                for point in fact.points:
                    values += 1
                    again = rows.get(point.label)
                    if not _close(point.value, None if again is None else float(again)):
                        ok = False
                        problems.append(
                            f"{fact.id} ({fact.title}), {point.label}: {point.value} "
                            f"against {again}"
                        )
            fact.checked = ok
            checked += ok
    finally:
        connection.close()
    if problems:
        raise FactsCheckError(
            "Facts did not match their recomputation: " + "; ".join(problems[:10])
        )
    report.check = [
        f"{checked:,} facts ({values:,} numbers) recomputed in DuckDB from separate SQL; "
        f"all agree within {TOLERANCE:g}"
    ]
    return report
