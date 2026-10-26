"""Reading single cell values: empty markers, numbers written in any common way, and dates.

Nothing here decides anything for a whole column. Each reader says what a value could
mean; when a value has two possible readings (``1.234``, ``01/02/2024``) both are kept, and
the column plan decides which convention applies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

# Markers that mean "no value". Text columns use the short list, because "NA" can be a real
# code there (Namibia, sodium); numeric and date columns also accept the longer one.
NULL_TOKENS = frozenset({"", "null", "none", "nan", "n/a", "#n/a", "-", "--", "—", "nil"})
NULL_TOKENS_STRICT = NULL_TOKENS | frozenset(
    {"na", "n.a.", "?", "missing", "#value!", "#div/0!", "#ref!", "#num!", "#name?", "#null!"}
)

_DIGITS = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹０１２３４５６７８９٫٬−   ",
    "012345678901234567890123456789.,-   ",
)

CURRENCY_SYMBOLS = "$€£¥₹₩₺₽₦₱₪₫฿₴₸₼₾﷼¢"
CURRENCY_CODES = frozenset(
    "USD EUR GBP JPY CNY INR SAR AED EGP KWD QAR BHD OMR JOD MAD TND DZD TRY RUB CHF CAD AUD "
    "NZD SEK NOK DKK PLN BRL MXN ZAR KRW SGD HKD IDR MYR PHP THB PKR NGN KES ILS LBP IQD LYD "
    "SDG".split()
)
# Arabic abbreviations written next to amounts (Saudi riyal, dirham, Egyptian pound, ...)
ARABIC_CURRENCIES = ("ر.س", "د.إ", "ج.م", "د.ك", "ر.ق", "د.ب", "ر.ع", "د.أ")

_CODE_RE = re.compile(r"^(?:([A-Za-z]{3})\s*)?(.*?)(?:\s*([A-Za-z]{3}))?$")
_SEP_GROUPS = re.compile(r"^\d{1,3}(?:[ '’]\d{3})+(?:[.,]\d+)?$")
_SCIENTIFIC = re.compile(r"^\d*\.?\d+[eE][+-]?\d{1,3}$")

Style = Literal["plain", "dot", "comma", "ambiguous"]


# [0-9], not \d: \d also matches Arabic-Indic digits, which do need reading
_PLAIN_NUMBER = re.compile(r"^-?[0-9]+(?:\.[0-9]+)?$")
_PLAIN_DATE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}(?:[ T][0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]+)?)?)?$"
)


def plain_number(text: str) -> bool:
    """Written the way the cleaned file writes numbers (-1234.5): reading it changes nothing."""
    return bool(_PLAIN_NUMBER.match(text.strip()))


def plain_date(text: str) -> bool:
    """Written as an ISO date (2024-01-31, optionally with a time)."""
    return bool(_PLAIN_DATE.match(text.strip()))


def normalise(text: str) -> str:
    """Arabic-Indic and full-width digits to ASCII, odd minus signs and spaces to plain ones."""
    return text.translate(_DIGITS).strip()


def is_null(text: str, strict: bool) -> bool:
    return text.strip().lower() in (NULL_TOKENS_STRICT if strict else NULL_TOKENS)


@dataclass(frozen=True)
class Number:
    """What a cell could mean as a number.

    ``style`` is the decimal convention the text shows: ``dot`` (1,234.5), ``comma``
    (1.234,5), ``plain`` (no separators) or ``ambiguous`` (1.234 or 1,234: one separator
    followed by exactly three digits). ``value`` is the reading with a decimal point,
    ``comma_value`` the reading with a decimal comma; they differ only when ambiguous.
    """

    value: float
    comma_value: float
    style: Style
    currency: str | None = None
    percent: bool = False

    def read(self, convention: Literal["dot", "comma"]) -> float:
        return self.comma_value if convention == "comma" else self.value


_FAST_INT = re.compile(r"-?[0-9]{1,15}")
_FAST_DECIMAL = re.compile(r"-?([0-9]{1,15})\.([0-9]{1,15})")


def read_number(raw: str) -> Number | None:
    """Read ``raw`` as a number, or return None when it is not one."""
    # most values are plain numbers: read those without the full parse
    if _FAST_INT.fullmatch(raw):
        v = float(raw)
        return Number(v, v, "plain")
    fast = _FAST_DECIMAL.fullmatch(raw)
    whole = fast.group(1) if fast else ""
    if fast and not (len(fast.group(2)) == 3 and len(whole) <= 3 and whole[0] != "0"):
        v = float(raw)
        return Number(v, v, "dot")

    s = normalise(raw)
    if not s or len(s) > 60:
        return None

    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1].strip()

    percent = False
    if s.endswith("%"):
        percent, s = True, s[:-1].strip()
    elif s.startswith("%"):
        percent, s = True, s[1:].strip()

    currency: str | None = None
    for abbreviation in ARABIC_CURRENCIES:
        if s.startswith(abbreviation) or s.endswith(abbreviation):
            currency = abbreviation
            s = s.removeprefix(abbreviation).removesuffix(abbreviation).strip()
            break

    # signs and currency symbols can come in any order around the number: -$5, $-5, $(5)
    for _ in range(3):
        if s[:1] in "+-" and s[1:2]:
            negative = negative or s[0] == "-"
            s = s[1:].strip()
        if s[:1] and s[0] in CURRENCY_SYMBOLS:
            currency = currency or s[0]
            s = s[1:].strip()
        if s[-1:] and s[-1] in CURRENCY_SYMBOLS:
            currency = currency or s[-1]
            s = s[:-1].strip()
        if s.startswith("(") and s.endswith(")"):
            negative, s = True, s[1:-1].strip()
    if s.endswith("-") and len(s) > 1:  # 100- (accounting exports)
        negative, s = True, s[:-1].strip()

    match = _CODE_RE.match(s)
    if match and (match.group(1) or match.group(3)):
        codes = [c.upper() for c in (match.group(1), match.group(3)) if c]
        if not all(code in CURRENCY_CODES for code in codes):
            return None
        currency = currency or codes[0]
        s = match.group(2).strip()
        if s[:1] in "+-" and s[1:2]:
            negative = negative or s[0] == "-"
            s = s[1:].strip()

    parsed = _read_digits(s)
    if parsed is None:
        return None
    value, comma_value, style = parsed
    if negative:
        value, comma_value = -value, -comma_value
    if percent:
        value, comma_value = value / 100, comma_value / 100
    return Number(value, comma_value, style, currency, percent)


def _read_digits(s: str) -> tuple[float, float, Style] | None:
    if not s or not (s[0].isdigit() or s[0] in ".,"):
        return None
    if s.isdigit():
        v = float(s)
        return v, v, "plain"
    if _SCIENTIFIC.match(s):  # 1.5E+05, as spreadsheets export large numbers
        v = float(s)
        return v, v, "dot"
    if _SEP_GROUPS.match(s):  # 1 234 567,89 or 1'234'567.89: spaces group thousands
        digits = re.sub(r"[ '’]", "", s)
        if "," in digits or "." in digits:
            mark = "," if "," in digits else "."
            v = float(digits.replace(mark, "."))
            return v, v, "comma" if mark == "," else "dot"
        v = float(digits)
        return v, v, "plain"
    if not re.fullmatch(r"[\d.,]+", s) or not re.search(r"\d", s):
        return None

    dots, commas = s.count("."), s.count(",")
    if dots and commas:
        decimal = "." if s.rfind(".") > s.rfind(",") else ","
        thousands = "," if decimal == "." else "."
        whole, frac = s.rsplit(decimal, 1)
        if decimal in whole or not frac.isdigit() or not _grouped(whole, thousands):
            return None
        v = float(whole.replace(thousands, "") + "." + frac)
        return v, v, "dot" if decimal == "." else "comma"

    mark = "." if dots else ","
    count = dots or commas
    if count > 1:  # only thousands separators: 1,234,567 or 1.234.567
        if not _grouped(s, mark):
            return None
        v = float(s.replace(mark, ""))
        return v, v, "dot" if mark == "," else "comma"

    whole, frac = s.split(mark)
    if not frac.isdigit() or (whole and not whole.isdigit()):
        return None
    decimal_reading = float((whole or "0") + "." + frac)
    if len(frac) == 3 and 1 <= len(whole) <= 3 and not whole.startswith("0"):
        thousands_reading = float(whole + frac)
        if mark == ".":
            return decimal_reading, thousands_reading, "ambiguous"
        return thousands_reading, decimal_reading, "ambiguous"
    return decimal_reading, decimal_reading, "dot" if mark == "." else "comma"


def _grouped(whole: str, sep: str) -> bool:
    """Whether ``whole`` is digits grouped in threes by ``sep`` (1,234,567)."""
    parts = whole.split(sep)
    return (
        all(p.isdigit() for p in parts)
        and 1 <= len(parts[0]) <= 3
        and all(len(p) == 3 for p in parts[1:])
    )


# ---- dates ------------------------------------------------------------------------------

MONTHS = {
    name: i
    for i, names in enumerate(
        [
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ],
        start=1,
    )
    for name in names
}

_TIME = r"(?:[ T](\d{1,2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?\s*(am|pm|AM|PM)?)?"
_ZONE = r"(?:Z|[+-]\d{2}:?\d{2})?"
_YMD = re.compile(rf"^(\d{{4}})([-/.])(\d{{1,2}})\2(\d{{1,2}}){_TIME}{_ZONE}$")
_NUMERIC = re.compile(rf"^(\d{{1,2}})([-/.])(\d{{1,2}})\2(\d{{4}}|\d{{2}}){_TIME}$")
_DAY_NAMED = re.compile(r"^(\d{1,2})(?:st|nd|rd|th)?[ -]([A-Za-z]{3,9})\.?,?[ -](\d{4})$")
_NAMED_DAY = re.compile(r"^([A-Za-z]{3,9})\.? (\d{1,2})(?:st|nd|rd|th)?,? (\d{4})$")
_NAMED_MONTH = re.compile(r"^([A-Za-z]{3,9})\.?[ -](\d{4})$")

DateOrder = Literal["ymd", "dmy", "mdy", "named"]


@dataclass(frozen=True)
class DateRead:
    """What a cell could mean as a date: one reading per possible order of its parts.

    ``01/02/2024`` has a ``dmy`` and an ``mdy`` reading; ``31/01/2024`` only ``dmy``;
    ``2024-01-31`` only ``ymd``. ``short_year`` is set for two-digit years.
    """

    readings: dict[str, datetime]
    separator: str = ""
    short_year: bool = False
    has_time: bool = False


def _time(groups: tuple[str | None, ...]) -> tuple[int, int, int] | None:
    hour, minute, second, half = groups
    if hour is None:
        return 0, 0, 0
    h, m, sec = int(hour), int(minute or 0), int(second or 0)
    if half:
        if not 1 <= h <= 12:
            return None
        h = h % 12 + (12 if half.lower() == "pm" else 0)
    return h, m, sec


def _date(year: int, month: int, day: int, t: tuple[int, int, int]) -> datetime | None:
    try:
        return datetime(year, month, day, *t)
    except ValueError:
        return None


def read_date(raw: str) -> DateRead | None:
    s = normalise(raw)
    if not 6 <= len(s) <= 40:
        return None

    m = _YMD.match(s)
    if m:
        t = _time(m.group(5, 6, 7, 8))
        d = _date(int(m.group(1)), int(m.group(3)), int(m.group(4)), t) if t else None
        has_time = m.group(5) is not None
        return DateRead({"ymd": d}, m.group(2), has_time=has_time) if d else None

    m = _NUMERIC.match(s)
    if m:
        a, sep, b, year_text = int(m.group(1)), m.group(2), int(m.group(3)), m.group(4)
        t = _time(m.group(5, 6, 7, 8))
        if t is None:
            return None
        year = int(year_text)
        short = len(year_text) == 2
        if short:
            year += 2000 if year < 69 else 1900
        readings = {}
        if (dmy := _date(year, b, a, t)) is not None:
            readings["dmy"] = dmy
        if (mdy := _date(year, a, b, t)) is not None:
            readings["mdy"] = mdy
        has_time = m.group(5) is not None
        return DateRead(readings, sep, short, has_time) if readings else None

    for pattern, day_group, month_group in ((_DAY_NAMED, 1, 2), (_NAMED_DAY, 2, 1)):
        m = pattern.match(s)
        if m:
            month = MONTHS.get(m.group(month_group).lower())
            if month is None:
                return None
            d = _date(int(m.group(3)), month, int(m.group(day_group)), (0, 0, 0))
            return DateRead({"named": d}) if d else None

    m = _NAMED_MONTH.match(s)
    if m and (month := MONTHS.get(m.group(1).lower())) is not None:
        d = _date(int(m.group(2)), month, 1, (0, 0, 0))
        return DateRead({"named": d}) if d else None
    return None
