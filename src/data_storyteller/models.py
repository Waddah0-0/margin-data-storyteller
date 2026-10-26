"""Input and output models of the Data Storyteller."""

from __future__ import annotations

from typing import Literal

from margin_core import AnswersInput
from pydantic import BaseModel, Field

Role = Literal["time", "identifier", "measure", "category", "free_text", "empty"]
Aggregation = Literal["sum", "average", "last", "count_distinct", "none", "unknown"]
DecidedBy = Literal["rule", "user", "preset", "default"]


class DataStorytellerInput(AnswersInput):
    dataset: str = Field(
        min_length=1,
        description="Path to the data: CSV or TSV, Excel (.xlsx), Parquet, JSON or SQLite.",
        examples=["C:/data/sales.xlsx"],
    )
    table: str | None = Field(
        default=None,
        description="The sheet or table to use when the file has several. Asked when not given.",
    )
    stop_after: Literal["clean", "story"] = Field(
        default="clean",
        description=(
            "clean: clean the data and explain every change. story: also set up the story "
            "(which numbers matter and how they add up); the story itself is not built yet."
        ),
    )
    output_dir: str | None = Field(
        default=None,
        description="Folder for the cleaned data (CSV) and the cleaning report (JSON). "
        "Nothing is written when empty.",
    )


class TableInfo(BaseModel):
    name: str
    rows: int
    columns: int


class SourceInfo(BaseModel):
    file: str
    format: str
    encoding: str | None = None
    delimiter: str | None = None
    tables: list[TableInfo]
    table: str = Field(description="The table that was cleaned.")
    header_row: int | None = Field(description="Row of the column names (1 = first row).")
    title: list[str] = Field(default_factory=list, description="Title rows above the table.")
    notes: list[str] = Field(default_factory=list)


class Change(BaseModel):
    row: int = Field(description="Row in the file (1 = first row).")
    before: str
    after: str | None = Field(description="None: left empty.")


class Step(BaseModel):
    """One change to the data, with the rule or answer behind it."""

    id: str
    kind: str
    column: str | None = None
    summary: str
    decided_by: DecidedBy
    question_ids: list[str] = Field(default_factory=list)
    cells_changed: int = 0
    cells_emptied: int = 0
    rows_removed: int = 0
    examples: list[Change] = Field(default_factory=list)
    unreadable: list[Change] = Field(default_factory=list)


class CleaningReport(BaseModel):
    rows_in: int
    rows_out: int
    columns_in: int
    columns_out: int
    steps: list[Step]
    check: list[str] = Field(description="What the final check verified.")


class ValueCount(BaseModel):
    value: str
    count: int


class ColumnProfile(BaseModel):
    name: str
    source_name: str = Field(description="Its name in the file; empty for added columns.")
    type: Literal["number", "date", "text", "empty"]
    role: Role
    aggregation: Aggregation
    reason: str = Field(description="Why it has this role and aggregation.")
    unit: str | None = Field(default=None, description='A currency sign or code, or "%".')
    count: int
    missing: int
    distinct: int
    min: float | str | None = None
    max: float | str | None = None
    mean: float | None = None
    total: float | None = None
    top: list[ValueCount] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    personal: bool = False
    personal_reason: str | None = None
    notes: list[str] = Field(default_factory=list)


class StorySetup(BaseModel):
    """What the story is built around, as confirmed (or assumed) at the checkpoint."""

    north_star: str | None = Field(description="The main number of the story.")
    grain: str = Field(description="What one row is.")
    primary_date: str | None = None
    aggregations: dict[str, Aggregation] = Field(default_factory=dict)
    weights: dict[str, str] = Field(
        default_factory=dict, description="Rates averaged in proportion to another column."
    )
    focus: list[str] = Field(default_factory=list, description="Columns the story focuses on.")


FactKind = Literal[
    "total",
    "average",
    "count",
    "series",
    "period_change",
    "trend",
    "partial_period",
    "breakdown",
    "concentration",
    "member_change",
    "segment_gap",
    "correlation",
    "anomaly",
    "simpsons",
]


class Point(BaseModel):
    label: str
    value: float
    n: int | None = None


class Fact(BaseModel):
    """One number the story may use, with how it was computed and how sure it is.

    The story never states a number that is not a fact: it refers to ``value`` (or a
    component) by the fact's id, and the report fills the number in.
    """

    id: str = Field(description='"F001", in the order the facts were found.')
    kind: FactKind
    title: str = Field(description='What the number is, e.g. "Sales in 2024-09".')
    statement: str = Field(description="The fact as one plain sentence, number included.")
    metric: str | None = None
    dimension: str | None = None
    member: str | None = None
    period: str | None = None
    value: float
    format: Literal["number", "percent", "change", "count", "ratio"] = Field(
        description="How to show value: percent and change are fractions (0.2 = 20%)."
    )
    unit: str | None = Field(default=None, description="Currency of the metric, if any.")
    metric_format: Literal["number", "percent", "change", "count", "ratio"] = Field(
        default="number", description="How the metric's own values show (components)."
    )
    metric_label: str | None = Field(
        default=None, description='The metric in words: "share damaged".'
    )
    components: dict[str, float | str] = Field(default_factory=dict)
    points: list[Point] = Field(default_factory=list, description="A series or breakdown.")
    method: str = Field(description="How it was computed, in plain words.")
    n: int = Field(description="Rows behind the number.")
    confidence: Literal["high", "medium", "low"]
    caveats: list[str] = Field(default_factory=list)
    importance: float = Field(ge=0, le=1, description="How much it matters to the story.")
    sql: str | None = Field(default=None, description="Recomputes value, independently.")
    points_sql: str | None = Field(default=None, description="Recomputes points (label, value).")
    checked: bool = Field(default=False, description="Recomputed in DuckDB and matched.")


class FactsReport(BaseModel):
    time: str | None = Field(default=None, description="The time axis: column and grain.")
    periods: list[str] = Field(default_factory=list, description="Full periods, in order.")
    partial: list[str] = Field(default_factory=list, description="Periods left out as partial.")
    facts: list[Fact] = Field(default_factory=list)
    check: list[str] = Field(default_factory=list, description="What the recomputation verified.")


# ---- the story ----------------------------------------------------------------------------

StoryShape = Literal["performance", "composition", "trend", "relationship", "profile"]
PageRole = Literal["pulse", "drivers", "nuance", "actions"]


class DraftSentence(BaseModel):
    """A sentence as the model writes it: numbers only as fact tokens like {F012}."""

    text: str = Field(description="Plain words. Every number is a token such as {F012}.")
    fact_ids: list[str] = Field(description="The facts this sentence rests on.")
    uses_context: bool = Field(
        default=False, description="True only if it relies on the context the person gave."
    )


class DraftAction(BaseModel):
    text: str = Field(description="A suggested next step, stated as a hypothesis.")
    check: str = Field(description="How to test the hypothesis before acting on it.")
    fact_ids: list[str]


class DraftPage(BaseModel):
    page: str = Field(description="The page id from the plan.")
    title: str = Field(description="The page's message as a short title, not a topic.")
    headline: DraftSentence
    body: list[DraftSentence] = Field(default_factory=list, max_length=4)
    actions: list[DraftAction] = Field(default_factory=list, max_length=3)


class Draft(BaseModel):
    """What the writer returns. The validator checks it before anything is shown."""

    title: str = Field(description="The story's title: its main message in a few words.")
    pages: list[DraftPage]


class StorySentence(BaseModel):
    text: str
    fact_ids: list[str] = Field(default_factory=list)


class StoryAction(BaseModel):
    text: str
    check: str
    fact_ids: list[str] = Field(default_factory=list)


class StoryPage(BaseModel):
    id: str
    role: PageRole
    title: str
    headline: StorySentence
    body: list[StorySentence] = Field(default_factory=list)
    actions: list[StoryAction] = Field(default_factory=list)
    fact_ids: list[str] = Field(default_factory=list, description="Every fact on the page.")


class Story(BaseModel):
    title: str
    shape: StoryShape
    pages: list[StoryPage]
    written_by: Literal["model", "template"]
    model: str | None = None
    attempts: int = Field(default=0, description="Drafts the model wrote.")
    rejected: list[str] = Field(
        default_factory=list, description="What the validator refused in earlier drafts."
    )
    context: str | None = Field(default=None, description="Context the person gave.")


class DataStorytellerOutput(BaseModel):
    source: SourceInfo
    cleaning: CleaningReport
    columns: list[ColumnProfile]
    setup: StorySetup | None = None
    facts: FactsReport | None = None
    story: Story | None = None
    files: list[str] = Field(default_factory=list, description="Files written.")
