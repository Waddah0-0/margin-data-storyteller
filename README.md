# Data Storyteller

Cleans a table of data, shows every change it made, and tells the story in it with charts.

> **Status: planned.** The cleaning works and is tested. The story and the charts are being
> built; until then, `stop_after="story"` only sets the story up.

## What it does

Give it a spreadsheet or data file and it gives you back a clean table, with a report of
every change and the reason for it. Nothing changes silently: a value it can't read is
left empty and listed by row, a removed row is listed with the rule that removed it, and a
final check proves that every value in the file is either in the result or accounted for.

When the file can be read more than one way, it asks instead of guessing. Is `1.234` one
thousand two hundred and thirty-four, or one point two three four? Is `01/02/2024` the
first of February or the second of January? Is the row that says "Total" data? You answer
once, all questions together, and every answer is kept with the result.

It is for anyone who gets data from exports, other teams or other countries, and wants to
trust the numbers before working with them.

## How it works

1. **Read.** CSV, TSV, Excel, Parquet, JSON or SQLite. Text files are read as text, so
   nothing is guessed on the way in (`007` stays `007`, `NA` stays `NA` until a step
   decides what it means). The encoding and the delimiter are detected.
2. **Find the table.** Title rows above the data are skipped, column names split over two
   rows are combined, and a file without column names gets `column_1`, `column_2`, ...
3. **Plan.** Each column is read as numbers, dates or text from its values, not its name.
   The plan covers numbers written with currency signs, percent signs, thousands
   separators, decimal commas, `(100)` for negatives, or Arabic-Indic digits; dates in any
   order; markers like `N/A` or `#DIV/0!`; extra spaces; the same label spelled
   differently; empty rows and columns; total rows; and repeated rows.
4. **Ask.** Only what the file can't answer, with the values that made it ask and a
   suggested answer.
5. **Clean, then check** the result against the file.
6. **Describe** each column: what it holds, how its values add up (summed, averaged, or the
   latest value), and whether it looks like personal data.

## Input

| Field | Default | Meaning |
|---|---|---|
| `dataset` | required | Path to the file. The primary input. |
| `table` | asked | The sheet or table, when the file has more than one. |
| `stop_after` | `clean` | `clean`, or `story` to also set up the story (the main number, what one row is, how numbers add up). The story itself is not built yet. |
| `output_dir` | none | A folder for the cleaned table (`<name>.clean.csv`) and the report (`<name>.cleaning.json`). Nothing is written without it. |
| `answers` | `{}` | Answers given up front, by question id, for example `{"date_order:order_date": "dmy"}`. These are not asked. |
| `assume_defaults` | `false` | Ask nothing and take every suggested answer. Each one is marked as not confirmed. |

Question ids are stable, so a run can be repeated with the answers of an earlier one. They
are listed in the report under `answers`.

## Output

`data` holds:

- `source`: the file, its format, encoding, delimiter, tables, and where the column names
  were found.
- `cleaning`: rows and columns in and out, every step (what it did, to which column, who
  decided, examples before and after, values it could not read), and what the final check
  verified.
- `columns`: for each column its type, role, how it adds up and why, its unit (a currency
  or %), counts, range, most common values, and notes.
- `setup`: with `stop_after="story"`, the main number, what one row is, the main date, how
  each number adds up, and the columns to focus on.
- `files`: the files written to `output_dir`.

Findings: one per change, each with its evidence; one per column note (personal data, a
column that is mostly empty, an id that repeats); and the final check.

## Limits

- One table per run. Tables are not joined.
- Files up to 500 MB, read into memory.
- Personal data is detected from column names and value patterns, so it can miss some. It
  stays in the cleaned file; only this report leaves it out.
- How a number adds up is guessed from its name (sales add up, prices are averaged). A
  name that says nothing is asked about only in story mode.
- Old `.xls` workbooks are not supported; save them as `.xlsx` or CSV. Excel formulas are
  never run: the values Excel last saved are used.

## Usage

```bash
margin run data-storyteller sales.xlsx --set output_dir=clean
margin run data-storyteller sales.csv --set 'answers={"decimal:amount": "comma"}'
```

In a terminal the questions are asked there; in the web UI they appear on the run page.

```python
import asyncio

from margin_core import run_agent


async def main() -> None:
    result = await run_agent("data-storyteller", {"dataset": "sales.csv", "assume_defaults": True})
    print(result.summary)


asyncio.run(main())
```

## Development

This agent lives in the [margin](https://github.com/Waddah0-0/margin) monorepo, in
[`agents/data_storyteller`](https://github.com/Waddah0-0/margin/tree/main/agents/data_storyteller). If you are reading this in its mirror
repository, that copy is read-only: open issues and pull requests in the main repository.

Run its tests with `uv run pytest agents/data_storyteller`. The messy files the tests use
are built by `tests/conftest.py`, byte for byte, so encodings and line endings stay exact.
