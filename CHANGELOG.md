# Changelog

All notable changes to this agent are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [0.1.0] - Unreleased

### Added
- Reads CSV, TSV, Excel, Parquet, JSON and SQLite; detects encodings and delimiters, and
  reads text files as text so nothing is guessed on the way in.
- Finds the table: skips title rows, combines column names split over two rows, names the
  columns of a file without names, and makes every name unique.
- Cleans numbers (currency signs, percentages, separators, decimal commas, negatives in
  brackets, Arabic-Indic digits), dates in any order, markers that mean no value, extra
  spaces, different spellings of one label, empty rows and columns, total rows and
  repeated rows.
- Asks, in one batch, about anything the file can't settle, with the evidence and a
  suggested answer. Answers can be given up front, or every suggestion taken with
  `assume_defaults`.
- A final check against the file: every value is in the result or left empty by a step
  that says so, and every removed row is explained.
- Describes each column: its role, how its values add up and why, its unit, and whether it
  looks like personal data (whose values are then left out of the report).
- `stop_after="story"` sets up the story: the main number, what one row is, the main date,
  how unclear numbers add up, and which columns to focus on in a wide table.
- Writes the cleaned table as CSV and the report as JSON to `output_dir`.
- Finds the facts for the story: totals, changes over time (with incomplete first or last
  periods left out), trends, breakdowns and concentration, groups that grew or shrank
  (in total and per row), gaps between groups, correlations, anomalies (also at a finer
  time grain, per day of each period), outlier rows and Simpson's paradox. p-values are
  corrected for the number of tests.
- Every fact is recomputed in DuckDB from separately written SQL and must match within
  0.0001, or the run fails.
- Link ids that repeat (a customer id in an orders table), columns of years, and 0/1 flags
  are recognised.
- Writes the story: a second checkpoint to choose the insights and give context; pages laid
  out by rules; a model writes with fact tokens instead of numbers; a validator refuses
  typed numbers, unknown names, wrong directions, unsupported causes, unhedged
  low-confidence claims and paradox-blind quotes, and sends the problems back twice at
  most; a template writes the story when the model is missing or keeps failing.
- Charts for each story page, drawn from the facts: a line over time, bars, before-and-after
  dots and a meter. Anomaly facts carry the series they were found in, also checked in
  DuckDB.
- Its own result view in the web UI: the story page by page with its charts, every number
  a button that opens its fact (method, rows, confidence, caveats, the SQL that checked
  it), a table view for every chart, and how the data was cleaned.
