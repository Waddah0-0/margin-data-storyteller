"""Words in column names, used to guess what a column holds."""

from __future__ import annotations

import re

ID_TOKENS = frozenset(
    {"id", "key", "code", "uuid", "guid", "sku", "no", "num", "number", "ref", "reference"}
)


def tokens(name: str) -> list[str]:
    """Words in a column name: ``orderDate``, ``order_date`` and ``Order Date`` all give
    ``["order", "date"]``."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return [t for t in re.split(r"[^0-9A-Za-z؀-ۿ]+", spaced.lower()) if t]


def id_like(name: str) -> bool:
    return bool(set(tokens(name)) & ID_TOKENS)
