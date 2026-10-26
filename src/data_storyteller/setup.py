"""Setting up the story: the questions only the person can answer, and their answers.

Asked at the same checkpoint as the cleaning questions, and only when needed: the main
number when there is more than one candidate, the main date when there is more than one
date column, how a number adds up when its name does not say, and which columns to focus
on in a wide file. What one row is (the grain) is always asked: everything after depends
on it.
"""

from __future__ import annotations

from margin_core import Answer, Choice, Evidence, Question

from data_storyteller.models import Aggregation, ColumnProfile, StorySetup
from data_storyteller.names import tokens

MAIN_NUMBERS = (
    "revenue",
    "sales",
    "profit",
    "income",
    "amount",
    "spend",
    "cost",
    "orders",
    "quantity",
    "units",
    "volume",
    "value",
)
WIDE = 25  # more columns than this: ask which to focus on
FOCUS_CHOICES = 20
MAX_AGGREGATION_QUESTIONS = 8

AGGREGATION_CHOICES = [
    Choice(value="sum", label="Add them up", detail="Like sales or units"),
    Choice(value="average", label="Average them", detail="Like prices, rates or scores"),
    Choice(value="last", label="Take the latest value", detail="Like balances or stock levels"),
    Choice(value="none", label="None of these: it's a code or label", detail="Like an id"),
]


def _measures(columns: list[ColumnProfile]) -> list[ColumnProfile]:
    return [c for c in columns if c.role == "measure"]


def _main_number_default(candidates: list[ColumnProfile]) -> str:
    for word in MAIN_NUMBERS:
        for c in candidates:
            if word in tokens(c.name):
                return c.name
    return max(candidates, key=lambda c: abs(c.total or 0)).name


def questions(columns: list[ColumnProfile]) -> list[Question]:
    found: list[Question] = []
    measures = _measures(columns)

    unclear = [c for c in measures if c.aggregation == "unknown"][:MAX_AGGREGATION_QUESTIONS]
    for c in unclear:
        found.append(
            Question(
                id=f"aggregate:{c.name}",
                prompt=f'How should "{c.name}" be combined across rows?',
                detail=c.reason[:1].upper() + c.reason[1:] + ".",
                section="Story setup",
                choices=AGGREGATION_CHOICES,
                default="sum",
                evidence=[
                    Evidence(
                        kind="values",
                        location=c.name,
                        detail=f"From {c.min:g} to {c.max:g}, averaging {c.mean:g}"
                        if c.min is not None and isinstance(c.min, float)
                        else ", ".join(c.examples),
                    )
                ],
            )
        )

    summable = [c for c in measures if c.aggregation in ("sum", "unknown")]
    rates = [c for c in measures if c.aggregation == "average" and c.unit == "%"]
    weights = [c for c in summable if c.aggregation == "sum"][:4]
    for c in rates:
        if not weights:
            break
        found.append(
            Question(
                id=f"weight:{c.name}",
                prompt=f'When "{c.name}" is averaged, should some rows count more?',
                detail="A rate is usually averaged in proportion to what it applies to: a "
                "discount by revenue, a conversion rate by visits.",
                section="Story setup",
                choices=[Choice(value="plain", label="No, a plain average")]
                + [Choice(value=w.name, label=f"In proportion to {w.name}") for w in weights],
                default=weights[0].name,
            )
        )

    if len(summable) > 1:
        found.append(
            Question(
                id="north_star",
                prompt="Which number should the story be about?",
                detail="The story leads with it; the others explain it.",
                section="Story setup",
                choices=[
                    Choice(value=c.name, label=c.name, detail=_range(c)) for c in summable[:12]
                ],
                default=_main_number_default(summable[:12]),
            )
        )

    dates = [c for c in columns if c.role == "time" and c.type == "date"]
    if len(dates) > 1:
        found.append(
            Question(
                id="primary_date",
                prompt="Which date should the story follow?",
                section="Story setup",
                choices=[Choice(value=c.name, label=c.name, detail=_range(c)) for c in dates],
                default=dates[0].name,
            )
        )

    ids = [c for c in columns if c.role == "identifier"]
    grains = [
        Choice(value=c.name, label=f"One {_thing(c.name)}", detail=f"Each {c.name} once")
        for c in ids
    ]
    grains.append(Choice(value="record", label="One record of something else"))
    found.append(
        Question(
            id="grain",
            prompt="What is one row of this table?",
            detail="For example one order, one customer, or one day per store. Type your own "
            "if none fits.",
            section="Story setup",
            choices=grains,
            allow_other=True,
            default=grains[0].value,
        )
    )

    if len(columns) > WIDE:
        ranked = _rank_for_focus(columns)
        found.append(
            Question(
                id="focus",
                prompt=f"This table has {len(columns)} columns. Which should the story focus on?",
                detail="The cleaned file keeps every column; this only narrows the story.",
                section="Story setup",
                kind="multi_choice",
                choices=[Choice(value=c.name, label=c.name, detail=_range(c)) for c in ranked],
                default=[c.name for c in ranked[:10]],
            )
        )
    return found


def _thing(name: str) -> str:
    words = [w for w in tokens(name) if w not in {"id", "key", "code", "no", "num", "number"}]
    return " ".join(words) or name


def _range(c: ColumnProfile) -> str:
    if c.type == "number" and c.min is not None:
        return f"{c.min:g} to {c.max:g}"
    if c.type == "date" and c.min is not None:
        return f"{c.min} to {c.max}"
    return ", ".join(c.examples)


def _rank_for_focus(columns: list[ColumnProfile]) -> list[ColumnProfile]:
    """Columns most likely to matter: named measures, then dates and categories, never ids,
    empty or constant columns."""

    def score(c: ColumnProfile) -> tuple[int, float]:
        named = c.role == "measure" and c.aggregation != "unknown"
        kind = {"measure": 3 if named else 1, "time": 2, "category": 2}.get(c.role, 0)
        filled = c.count / max(c.count + c.missing, 1)
        return kind, filled

    useful = [
        c for c in columns if c.role not in ("identifier", "empty", "free_text") and c.distinct > 1
    ]
    return sorted(useful, key=score, reverse=True)[:FOCUS_CHOICES]


def resolve(
    columns: list[ColumnProfile], answers: dict[str, Answer]
) -> tuple[StorySetup, list[str]]:
    """The setup from the answers, checked against the final columns (an answer to a
    cleaning question can change a column's type). Returns the setup and any warnings."""
    by_name = {c.name: c for c in columns}
    warnings: list[str] = []
    aggregations: dict[str, Aggregation] = {}
    for c in _measures(columns):
        chosen = answers.get(f"aggregate:{c.name}")
        aggregations[c.name] = chosen.value if chosen else c.aggregation
        c.aggregation = aggregations[c.name]

    summable = [c.name for c in _measures(columns) if c.aggregation == "sum"]
    north = answers["north_star"].value if "north_star" in answers else None
    if north is None and len(summable) == 1:
        north = summable[0]
    if north is not None and north not in summable:
        warnings.append(
            f'"{north}" is not a number that adds up any more, so the story has no main number yet.'
        )
        north = None

    dates = [c.name for c in columns if c.role == "time" and c.type == "date"]
    primary = (
        answers["primary_date"].value
        if "primary_date" in answers
        else (dates[0] if dates else None)
    )
    if primary is not None and primary not in dates:
        primary = dates[0] if dates else None

    weights = {
        name: answer.value
        for key, answer in answers.items()
        if key.startswith("weight:")
        and answer.value != "plain"
        and (name := key.removeprefix("weight:")) in by_name
    }
    focus = answers["focus"].value if "focus" in answers else []
    grain = answers["grain"].value if "grain" in answers else "record"
    if grain in by_name:
        grain = f"one {_thing(grain)} ({grain})"
    elif grain == "record":
        grain = "one record"
    return (
        StorySetup(
            north_star=north,
            grain=grain,
            primary_date=primary,
            aggregations=aggregations,
            weights=weights,
            focus=[f for f in focus if f in by_name],
        ),
        warnings,
    )
