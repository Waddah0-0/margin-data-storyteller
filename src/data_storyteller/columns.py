"""Planning and applying the cleaning of one column.

A column is read as numbers, dates or text from its values, not its name. Planning finds
everything that would change and what is ambiguous; ambiguity becomes a question. Applying
uses the answers. Every cell a step empties is recorded by row, so the final check can
account for each value.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from margin_core import Answer, Choice, Evidence, Question

from data_storyteller.layout import is_empty
from data_storyteller.names import id_like
from data_storyteller.values import (
    DateRead,
    Number,
    is_null,
    plain_date,
    plain_number,
    read_date,
    read_number,
)

Kind = Literal["number", "date", "text", "empty"]
DecidedBy = Literal["rule", "user", "preset", "default"]

KIND_SHARE = 0.8  # share of values that must read as numbers (or dates) for the column to be one
SAFE_FAILURES = 0.02  # values that can't be read may be left empty without asking, up to this share
SAFE_FAILURES_MAX = 50
MAX_EXAMPLES = 5
MAX_FAILURES = 10
_ACCOUNTING_NEGATIVE = re.compile(r"\(|\d-$")
_ARABIC_DIGIT = re.compile(r"[٠-٩۰-۹]")


@dataclass
class StepRecord:
    """One change to the data. ``emptied`` holds the data-row indexes it left empty."""

    id: str
    kind: str
    summary: str
    column: str | None = None
    decided_by: DecidedBy = "rule"
    question_ids: list[str] = field(default_factory=list)
    cells_changed: int = 0
    emptied: set[int] = field(default_factory=set)
    removed_rows: set[int] = field(default_factory=set)
    examples: list[tuple[int, str, str | None]] = field(default_factory=list)  # row, before, after
    failures: list[tuple[int, str]] = field(default_factory=list)  # row, value


def decided(answers: dict[str, Answer], ids: list[str]) -> DecidedBy:
    """The weakest source among the answers a step used: an unconfirmed default wins."""
    sources = {answers[i].source for i in ids if i in answers}
    for source in ("default", "preset", "user"):
        if source in sources:
            return source
    return "rule"


def show(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ").removesuffix(" 00:00:00")
    if isinstance(value, float):
        return f"{value:.10g}"
    return str(value)


def _day_first(d: datetime) -> str:
    return f"{d.day} {d:%B %Y}"


def _month_first(d: datetime) -> str:
    return f"{d:%B} {d.day}, {d.year}"


def _count(n: int, word: str) -> str:
    return f"{n:,} {word}" + ("" if n == 1 else "s")


@dataclass
class ColumnResult:
    values: list[Any]
    kind: Kind
    unit: str | None = None
    extra: dict[str, list[Any]] = field(default_factory=dict)  # new columns, by name
    steps: list[StepRecord] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class ColumnPlan:
    name: str
    raw: list[Any]
    rows: list[int]  # file row number of each value, for evidence
    kind: Kind = "text"
    markers: dict[int, str] = field(default_factory=dict)  # cells that say "no value"
    numbers: dict[int, Number] = field(default_factory=dict)
    dates: dict[int, DateRead] = field(default_factory=dict)
    failures: list[int] = field(default_factory=list)  # neither a value of the kind nor a marker
    questions: list[Question] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    _convention: str | None = None  # decided without asking ("dot", "comma", "dmy", ...)

    # ---- planning ---------------------------------------------------------------------

    @classmethod
    def plan(cls, name: str, raw: list[Any], rows: list[int]) -> ColumnPlan:
        col = cls(name, raw, rows)
        present = [i for i, v in enumerate(raw) if not is_empty(v)]
        strict = {i: raw[i] for i in present if isinstance(raw[i], str) and is_null(raw[i], True)}
        candidates = [i for i in present if i not in strict]
        if not candidates:
            col.kind, col.markers = "empty", strict
            return col

        cache_n: dict[str, Number | None] = {}
        cache_d: dict[str, DateRead | None] = {}
        for i in candidates:
            v = raw[i]
            if isinstance(v, bool):
                continue
            if isinstance(v, int | float):
                col.numbers[i] = Number(float(v), float(v), "plain")
            elif isinstance(v, datetime):
                col.dates[i] = DateRead({"native": v})
            elif isinstance(v, str):
                if v not in cache_n:
                    cache_n[v] = read_number(v)
                if (num := cache_n[v]) is not None:
                    col.numbers[i] = num
                    continue
                if v not in cache_d:
                    cache_d[v] = read_date(v)
                if (d := cache_d[v]) is not None:
                    col.dates[i] = d

        total = len(candidates)
        if len(col.numbers) / total >= KIND_SHARE and not col._codes():
            col.kind, col.markers = "number", strict
            col.failures = [i for i in candidates if i not in col.numbers]
            col._plan_numbers()
        elif len(col.dates) / total >= KIND_SHARE:
            col.kind, col.markers = "date", strict
            col.failures = [i for i in candidates if i not in col.dates]
            col._plan_dates()
        else:
            col.kind = "text"
            col.markers = {i: v for i, v in strict.items() if is_null(v, False)}
            col.numbers, col.dates = {}, {}
            col._plan_text()
        return col

    def _codes(self) -> bool:
        """Zero-padded codes (ZIP codes, account numbers) must stay text to keep their zeros."""
        texts = [self.raw[i].strip() for i in self.numbers if isinstance(self.raw[i], str)]
        if any(len(t) > 1 and t[0] == "0" and t[1].isdigit() for t in texts):
            self.notes.append("Values start with 0 (codes such as ZIP codes), so they stay text.")
            return True
        return False

    def _example_values(self, indexes: list[int], k: int = 3) -> str:
        return ", ".join(f"{show(self.raw[i]).strip()} (row {self.rows[i]})" for i in indexes[:k])

    def _failure_question(self, kind: Literal["numbers", "dates"]) -> None:
        total = len(self.numbers if kind == "numbers" else self.dates) + len(self.failures)
        share = len(self.failures) / total
        if not self.failures or (
            len(self.failures) <= SAFE_FAILURES_MAX and share <= SAFE_FAILURES
        ):
            return
        word = "numbers" if kind == "numbers" else "dates"
        self.questions.append(
            Question(
                id=f"{kind}:{self.name}",
                prompt=f'Should "{self.name}" be read as {word}?',
                detail=(
                    f"{_count(len(self.failures), 'value')} ({share:.0%}) can't be read as "
                    f"{word}. Read as {word}, those cells are left empty and listed in the report."
                ),
                section="Values",
                choices=[
                    Choice(value=kind, label=f"Read as {word}, leave the others empty"),
                    Choice(value="text", label="Keep the column as text"),
                ],
                default=kind if share <= 0.05 else "text",
                evidence=[
                    Evidence(
                        kind="values",
                        location=self.name,
                        detail=f"Not {word}: {self._example_values(self.failures, 5)}",
                    )
                ],
            )
        )

    def _plan_numbers(self) -> None:
        styles = Counter(n.style for n in self.numbers.values())
        ambiguous = [i for i, n in self.numbers.items() if n.style == "ambiguous"]
        if ambiguous:
            dot, comma = styles["dot"], styles["comma"]
            if dot and not comma:
                self._convention = "dot"
            elif comma and not dot:
                self._convention = "comma"
            else:
                sample = self.numbers[ambiguous[0]]
                text = show(self.raw[ambiguous[0]]).strip()
                evidence = [
                    Evidence(
                        kind="values",
                        location=self.name,
                        detail=f"Could be either: {self._example_values(ambiguous)}",
                    )
                ]
                clear = [i for i, n in self.numbers.items() if n.style in ("dot", "comma")]
                if clear:
                    evidence.append(
                        Evidence(
                            kind="values",
                            location=self.name,
                            detail=f"Written both ways elsewhere: {self._example_values(clear)}",
                        )
                    )
                self.questions.append(
                    Question(
                        id=f"decimal:{self.name}",
                        prompt=f'In "{self.name}", what does {text} mean?',
                        detail=(
                            f"{_count(len(ambiguous), 'value')} use one separator followed by "
                            "three digits, which reads differently in different countries."
                        ),
                        section="Values",
                        choices=[
                            Choice(
                                value="dot",
                                label=f"{show(sample.value)}",
                                detail="The point is a decimal point; a comma groups thousands",
                            ),
                            Choice(
                                value="comma",
                                label=f"{show(sample.comma_value)}",
                                detail="The comma is the decimal mark; a point groups thousands",
                            ),
                        ],
                        default="comma" if comma > dot else "dot",
                        evidence=evidence,
                    )
                )

        percents = [i for i, n in self.numbers.items() if n.percent]
        plain = [i for i, n in self.numbers.items() if not n.percent]
        if percents and plain:
            small = all(abs(self.numbers[i].value) <= 1 for i in plain)
            self.questions.append(
                Question(
                    id=f"percent:{self.name}",
                    prompt=f'"{self.name}" mixes values with and without a % sign. '
                    "What are the plain ones?",
                    detail="Both are stored as fractions: 12.5% becomes 0.125.",
                    section="Values",
                    choices=[
                        Choice(
                            value="fraction",
                            label="Already fractions",
                            detail="0.125 means 12.5%",
                        ),
                        Choice(
                            value="percent",
                            label="Percentages without the sign",
                            detail="12.5 means 12.5%",
                        ),
                    ],
                    default="fraction" if small else "percent",
                    evidence=[
                        Evidence(
                            kind="values",
                            location=self.name,
                            detail=f"With %: {self._example_values(percents)}; "
                            f"without: {self._example_values(plain)}",
                        )
                    ],
                )
            )
        self._failure_question("numbers")

    def _plan_dates(self) -> None:
        only = Counter(next(iter(d.readings)) for d in self.dates.values() if len(d.readings) == 1)
        ambiguous = [
            i
            for i, d in self.dates.items()
            if len(d.readings) == 2 and d.readings["dmy"] != d.readings["mdy"]
        ]
        if ambiguous:
            dmy, mdy = only["dmy"], only["mdy"]
            if dmy and not mdy:
                self._convention = "dmy"
            elif mdy and not dmy:
                self._convention = "mdy"
            else:
                first = self.dates[ambiguous[0]]
                text = show(self.raw[ambiguous[0]]).strip()
                dots = sum(self.dates[i].separator == "." for i in ambiguous)
                default = (
                    "dmy" if dmy > mdy or (dmy == mdy and dots > len(ambiguous) / 2) else "mdy"
                )
                self.questions.append(
                    Question(
                        id=f"date_order:{self.name}",
                        prompt=f'In "{self.name}", is {text} day first or month first?',
                        detail=(
                            f"In {_count(len(ambiguous), 'date')} both the day and the month are "
                            "12 or less, so both readings are possible."
                        ),
                        section="Values",
                        choices=[
                            Choice(
                                value="dmy",
                                label="Day first",
                                detail=f"{text} is {_day_first(first.readings['dmy'])}",
                            ),
                            Choice(
                                value="mdy",
                                label="Month first",
                                detail=f"{text} is {_month_first(first.readings['mdy'])}",
                            ),
                        ],
                        default=default,
                        evidence=[
                            Evidence(
                                kind="values",
                                location=self.name,
                                detail=f"Could be either: {self._example_values(ambiguous)}",
                            )
                        ],
                    )
                )
        self._failure_question("dates")

    def _plan_text(self) -> None:
        if id_like(self.name):
            return  # capitals can matter in ids and codes
        values = self._text_values(trim=True)
        counts = Counter(values.values())
        if not counts or len(counts) > 500:
            return
        if sum(len(v) for v in values.values()) / len(values) > 40:
            return  # free text, not categories
        groups = self._spelling_groups(counts)
        if not groups:
            return
        shown = "; ".join(
            ", ".join(f"{s} ×{counts[s]}" for s in group) for group in list(groups.values())[:5]
        )
        self.questions.append(
            Question(
                id=f"spellings:{self.name}",
                prompt=f'Merge the different spellings in "{self.name}"?',
                detail=(
                    f"{_count(len(groups), 'value')} written in more than one way (capitals or "
                    "spaces). Merged, each takes its most common spelling."
                ),
                section="Values",
                choices=[
                    Choice(value="merge", label="Merge them"),
                    Choice(value="keep", label="Keep them apart"),
                ],
                default="merge",
                evidence=[Evidence(kind="values", location=self.name, detail=shown)],
            )
        )

    def _text_values(self, trim: bool) -> dict[int, str]:
        return {
            i: (v.strip() if trim else v)
            for i, v in enumerate(self.raw)
            if isinstance(v, str) and not is_empty(v) and i not in self.markers
        }

    @staticmethod
    def _spelling_groups(counts: Counter[str]) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {}
        for spelling, _ in counts.most_common():
            groups.setdefault(re.sub(r"\s+", " ", spelling).casefold(), []).append(spelling)
        return {k: v for k, v in groups.items() if len(v) > 1}

    # ---- applying ---------------------------------------------------------------------

    def apply(self, answers: dict[str, Answer]) -> ColumnResult:
        kind = self.kind
        for q in self.questions:
            if q.id.startswith(("numbers:", "dates:")) and answers[q.id].value == "text":
                kind = "text"
        if kind == "text" and self.kind != "text":
            demoted = ColumnPlan(self.name, self.raw, self.rows, "text")
            demoted.markers = {i: v for i, v in self.markers.items() if is_null(v, False)}
            result = demoted._apply_text(answers)
            result.notes.append(f"Kept as text: most values are {self.kind}s, but you chose text.")
            return result
        steps = [s for s in [self._marker_step()] if s]
        if kind == "number":
            result = self._apply_numbers(answers)
        elif kind == "date":
            result = self._apply_dates(answers)
        elif kind == "empty":
            result = ColumnResult([None] * len(self.raw), "empty")
        else:
            return self._apply_text(answers)
        result.steps[:0] = steps
        result.notes[:0] = self.notes
        return result

    def _marker_step(self) -> StepRecord | None:
        if not self.markers:
            return None
        tokens = Counter(v.strip() for v in self.markers.values())
        listed = ", ".join(f'"{t}" ×{n}' if t else f"blank ×{n}" for t, n in tokens.most_common(5))
        return StepRecord(
            id=f"empty_markers:{self.name}",
            kind="empty_markers",
            column=self.name,
            summary=f"Emptied {_count(len(self.markers), 'cell')} that "
            + ("means" if len(self.markers) == 1 else "mean")
            + f" no value ({listed})",
            emptied=set(self.markers),
            examples=[(self.rows[i], self.raw[i], None) for i in list(self.markers)[:MAX_EXAMPLES]],
        )

    def _failure_list(self) -> list[tuple[int, str]]:
        return [(self.rows[i], show(self.raw[i])) for i in self.failures[:MAX_FAILURES]]

    def _apply_numbers(self, answers: dict[str, Answer]) -> ColumnResult:
        chosen = answers.get(f"decimal:{self.name}")
        picked = chosen.value if chosen else self._convention
        convention: Literal["dot", "comma"] = "comma" if picked == "comma" else "dot"
        plain_are_percent = (
            f"percent:{self.name}" in answers and answers[f"percent:{self.name}"].value == "percent"
        )
        values: list[Any] = [None] * len(self.raw)
        changed: list[int] = []  # values that needed interpreting, not just reading
        for i, n in self.numbers.items():
            v = n.read(convention)
            if plain_are_percent and not n.percent:
                v = v / 100
            values[i] = v
            raw = self.raw[i]
            if isinstance(raw, str) and (
                not plain_number(raw) or n.style == "ambiguous" or plain_are_percent
            ):
                changed.append(i)
        integral = all(
            float(v).is_integer() and abs(v) < 2**53 for v in values if v is not None
        ) and not any(n.percent for n in self.numbers.values())
        if integral:
            values = [None if v is None else int(v) for v in values]

        currencies = Counter(n.currency for n in self.numbers.values() if n.currency)
        unit = "%" if any(n.percent for n in self.numbers.values()) else None
        result = ColumnResult(values, "number", unit)
        if len(currencies) == 1:
            result.unit = next(iter(currencies))
        elif len(currencies) > 1:
            listed = ", ".join(currencies)
            result.extra[f"{self.name}_currency"] = [
                self.numbers[i].currency if i in self.numbers else None
                for i in range(len(self.raw))
            ]
            result.steps.append(
                StepRecord(
                    id=f"currency_split:{self.name}",
                    kind="currency_split",
                    column=self.name,
                    summary=f"The column mixes currencies ({listed}), so adding it up would be "
                    f"wrong; each value's currency is now in a new column, {self.name}_currency",
                )
            )

        if changed or self.failures:
            ids = [q.id for q in self.questions]
            how = []
            styles = {self.numbers[i].style for i in changed}
            if "comma" in styles or (convention == "comma" and "ambiguous" in styles):
                how.append("decimal commas")
            if currencies:
                how.append("currency signs removed")
            if unit == "%":
                how.append("percentages stored as fractions")
            texts = {str(self.raw[i]) for i in changed}
            if any(_ACCOUNTING_NEGATIVE.search(t) for t in texts):
                how.append("(100) and 100- read as negative")
            if any(_ARABIC_DIGIT.search(t) for t in texts):
                how.append("Arabic-Indic digits")
            summary = (
                f"Read {_count(len(changed), 'value')} as numbers" if changed else "Read as numbers"
            )
            if how:
                summary += f" ({', '.join(how)})"
            if self.failures:
                summary += (
                    f"; {_count(len(self.failures), 'value')} could not be read and were left empty"
                )
            shown = sorted(changed, key=lambda i: self.raw[i].strip() == show(values[i]))
            result.steps.insert(
                0,
                StepRecord(
                    id=f"numbers:{self.name}",
                    kind="numbers",
                    column=self.name,
                    summary=summary,
                    decided_by=decided(answers, ids),
                    question_ids=ids,
                    cells_changed=len(changed),
                    emptied=set(self.failures),
                    examples=[
                        (self.rows[i], self.raw[i], show(values[i])) for i in shown[:MAX_EXAMPLES]
                    ],
                    failures=self._failure_list(),
                ),
            )
        return result

    def _apply_dates(self, answers: dict[str, Answer]) -> ColumnResult:
        convention = self._convention or "mdy"
        if f"date_order:{self.name}" in answers:
            convention = answers[f"date_order:{self.name}"].value
        values: list[Any] = [None] * len(self.raw)
        changed: list[int] = []
        short = has_time = False
        for i, d in self.dates.items():
            values[i] = d.readings.get(convention) or next(iter(d.readings.values()))
            short, has_time = short or d.short_year, has_time or d.has_time
            if isinstance(self.raw[i], str) and not plain_date(self.raw[i]):
                changed.append(i)
        result = ColumnResult(values, "date")
        if changed or self.failures:
            ids = [q.id for q in self.questions]
            orders = {k for i in changed for k in self.dates[i].readings}
            how = []
            if orders & {"dmy", "mdy"}:
                how.append("day first" if convention == "dmy" else "month first")
            if short:
                how.append("two-digit years read as 19xx from 69 up, 20xx below")
            summary = (
                f"Read {_count(len(changed), 'value')} as dates" if changed else "Read as dates"
            )
            if how:
                summary += f" ({', '.join(how)})"
            if self.failures:
                summary += (
                    f"; {_count(len(self.failures), 'value')} could not be read and were left empty"
                )
            result.steps.append(
                StepRecord(
                    id=f"dates:{self.name}",
                    kind="dates",
                    column=self.name,
                    summary=summary,
                    decided_by=decided(answers, ids),
                    question_ids=ids,
                    cells_changed=len(changed),
                    emptied=set(self.failures),
                    examples=[
                        (self.rows[i], self.raw[i], show(values[i])) for i in changed[:MAX_EXAMPLES]
                    ],
                    failures=self._failure_list(),
                )
            )
        if has_time:
            result.notes.append("Has times of day as well as dates.")
        return result

    def _apply_text(self, answers: dict[str, Answer]) -> ColumnResult:
        steps = [s for s in [self._marker_step()] if s]
        values: list[Any] = [
            None if i in self.markers or is_empty(v) else v for i, v in enumerate(self.raw)
        ]
        trimmed = [i for i, v in self._text_values(trim=False).items() if v != v.strip()]
        for i in trimmed:
            values[i] = self.raw[i].strip()
        if trimmed:
            steps.append(
                StepRecord(
                    id=f"trim:{self.name}",
                    kind="trim",
                    column=self.name,
                    summary=f"Removed spaces around {_count(len(trimmed), 'value')}",
                    cells_changed=len(trimmed),
                    examples=[
                        (self.rows[i], self.raw[i], values[i]) for i in trimmed[:MAX_EXAMPLES]
                    ],
                )
            )
        qid = f"spellings:{self.name}"
        if qid in answers and answers[qid].value == "merge":
            counts = Counter(v for v in values if isinstance(v, str))
            to = {
                s: group[0] for group in self._spelling_groups(counts).values() for s in group[1:]
            }
            merged = [i for i, v in enumerate(values) if isinstance(v, str) and v in to]
            for i in merged:
                values[i] = to[values[i]]
            if merged:
                steps.append(
                    StepRecord(
                        id=qid,
                        kind="spellings",
                        column=self.name,
                        summary=f"Merged {_count(len(to), 'spelling')} into the most common one "
                        f"({_count(len(merged), 'cell')} changed)",
                        decided_by=decided(answers, [qid]),
                        question_ids=[qid],
                        cells_changed=len(merged),
                        examples=[
                            (self.rows[i], self.raw[i], values[i]) for i in merged[:MAX_EXAMPLES]
                        ],
                    )
                )
        values = [None if isinstance(v, str) and not v else v for v in values]
        return ColumnResult(values, "text", steps=steps, notes=list(self.notes))
