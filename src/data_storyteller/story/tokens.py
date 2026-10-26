"""Fact tokens: how the story refers to numbers.

The writer never types a number. It writes ``{F012}`` for a fact's value, or
``{F012.before}`` for one of its components, and :func:`render` puts in the number,
formatted exactly as the facts engine formats it. Both the prompt and the validator use
:func:`values`, so the writer can only use numbers that exist.
"""

from __future__ import annotations

import re

from data_storyteller.facts.base import Format, fmt
from data_storyteller.models import Fact, FactsReport

TOKEN = re.compile(r"\{(F\d{3})(?:\.([a-z_0-9]+))?\}")

# components measured in the metric's own units; the rest have fixed formats
METRIC_UNITS = {"before", "after", "usual", "member", "rest", "top_value", "total", "threshold"}
FIXED: dict[str, Format] = {
    "share": "percent",
    "share_of_total_change": "percent",
    "size": "percent",
    "per_period_size": "percent",
    "top_share": "percent",
    "target_share": "percent",
    "inverse": "ratio",
    "times": "ratio",
    "covered": "count",
    "length": "count",
    "median": "count",
    "overall_change": "change",
    "per_period": "percent",
    "r2": "percent",
    "top": "count",
    "entities": "count",
    "for_80": "count",
    "periods": "count",
}
HIDDEN = {"q_value", "robust_z", "spearman", "concentration_index", "against", "both_sums"}


def fact_values(fact: Fact) -> dict[str, str]:
    """Every token a fact offers, and the text it becomes."""
    found = {
        f"{{{fact.id}}}": fmt(fact.value, fact.format, fact.unit),
        f"{{{fact.id}.n}}": fmt(fact.n, "count"),
    }
    for key, value in fact.components.items():
        if key in HIDDEN:
            continue
        token = f"{{{fact.id}.{key}}}"
        if isinstance(value, str):
            found[token] = value
        elif key in METRIC_UNITS or key.endswith(("_before", "_after")):
            found[token] = fmt(value, fact.metric_format, fact.unit)
        else:
            found[token] = fmt(value, FIXED.get(key, "number"))
    return found


def tokenized(fact: Fact) -> str:
    """The fact's sentence with each number replaced by its token, so a writer copying it
    copies tokens, not numbers."""
    text = fact.statement
    for token, shown in sorted(fact_values(fact).items(), key=lambda kv: -len(kv[1])):
        if re.search(r"\d", shown):
            # never inside another number or a label such as 2024-10
            text = re.sub(rf"(?<![\w.,{{/-]){re.escape(shown)}(?![\w%/-])", token, text)
    return text


def values(report: FactsReport) -> dict[str, str]:
    found: dict[str, str] = {}
    for fact in report.facts:
        found |= fact_values(fact)
    return found


def render(text: str, table: dict[str, str]) -> str:
    """Replace each token with its number. Unknown tokens are left as they are (the
    validator refuses them before anything is rendered)."""
    return TOKEN.sub(lambda m: table.get(m.group(0), m.group(0)), text)
