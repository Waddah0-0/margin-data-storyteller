"""The facts engine: every number the story may use, each computed twice.

``compute`` finds the facts (totals, how the main number moves over time, where it comes
from, what moves with what, what stands out, and the traps: partial periods and Simpson's
paradox); ``verify`` recomputes them in DuckDB and fails loudly on any disagreement.
"""

from data_storyteller.facts.engine import compute
from data_storyteller.facts.verify import FactsCheckError, verify

__all__ = ["FactsCheckError", "compute", "verify"]
