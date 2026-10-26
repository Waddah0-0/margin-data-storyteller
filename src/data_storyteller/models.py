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


class DataStorytellerOutput(BaseModel):
    source: SourceInfo
    cleaning: CleaningReport
    columns: list[ColumnProfile]
    setup: StorySetup | None = None
    files: list[str] = Field(default_factory=list, description="Files written.")
