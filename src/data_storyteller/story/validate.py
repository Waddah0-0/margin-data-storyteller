"""The gate every draft passes before anyone sees it.

A draft is refused when it:

1. uses a token that is not a fact (or not one of the page's facts);
2. types a number instead of using a token;
3. names something that is not in the data;
4. says a number rose when its fact fell, or the reverse;
5. gives a cause ("because of", "drove") the person did not supply as context;
6. states a low-confidence fact without hedging it;
7. heads a page with a claim no high-confidence fact supports;
8. quotes an overall change that Simpson's paradox makes misleading, without the paradox.

Each problem is a sentence the writer can act on; the writer gets them back and tries again.
"""

from __future__ import annotations

import re

from data_storyteller.models import Draft, DraftSentence, Fact, FactsReport
from data_storyteller.story.plan import StoryPlan
from data_storyteller.story.tokens import TOKEN, values

CAUSAL = re.compile(
    r"\b(because( of)?|due to|caused|causes|causing|drove|drives|driven|driving|led to|leads to|"
    r"resulted in|results in|as a result|thanks to|owing to|attributable to|responsible for|"
    r"triggered|fuel(?:l)?ed|boosted by|hurt by)\b",
    re.IGNORECASE,
)
UP = re.compile(
    r"\b(rose|rise|rising|grew|grow|growing|increased?|increasing|up|higher|climbed|gained|"
    r"jumped|surged|improved)\b",
    re.IGNORECASE,
)
DOWN = re.compile(
    r"\b(fell|fall|falling|dropped|drop|declined?|declining|decreased?|decreasing|down|lower|"
    r"shrank|shrinking|slipped|slid|plunged|worsened)\b",
    re.IGNORECASE,
)
HEDGE = re.compile(
    r"\b(may|might|could|suggests?|appears?|seems?|possibly|tentative|small sample|few|"
    r"limited|caution|not certain|uncertain|only a few|treat with care)\b",
    re.IGNORECASE,
)
NUMBER = re.compile(r"\d")
WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*")
SIGNED = {"period_change", "member_change", "trend", "simpsons"}
CALENDAR = {
    "January", "February", "March", "April", "May", "June", "July", "August", "September",
    "October", "November", "December", "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday", "Q", "I", "Simpson", "Simpson's",
}  # fmt: skip


def vocabulary(report: FactsReport, extra: list[str]) -> tuple[set[str], set[str]]:
    """Words and number-bearing names the story may use: everything the facts say, plus
    column names and the calendar. Returns (words, names that contain digits)."""
    words = {w.lower() for w in CALENDAR}
    named: set[str] = set()
    texts = list(extra)
    for f in report.facts:
        texts += [f.title, f.statement, f.metric or "", f.dimension or "", f.member or ""]
        texts += [f.period or "", *(p.label for p in f.points)]
        for name in (f.member, f.period, *(p.label for p in f.points)):
            if name and NUMBER.search(name):
                named.add(name)
    for text in texts:
        words |= {w.lower().strip("'’") for w in WORD.findall(text)}
        words |= {w.lower() for w in re.split(r"[\s_]+", text) if w}
    return words, named


def problems(
    draft: Draft,
    plan: StoryPlan,
    report: FactsReport,
    columns: list[str],
    context: str | None,
) -> list[str]:
    facts = {f.id: f for f in report.facts}
    table = values(report)
    words, named = vocabulary(report, columns + ([context] if context else []))
    pages = {p.id: p for p in plan.pages}
    found: list[str] = []

    simpsons = {f.metric for f in report.facts if f.kind == "simpsons"}
    for page in draft.pages:
        if page.page not in pages:
            found.append(f'Page "{page.page}" is not in the plan; use {", ".join(pages)}.')
            continue
        allowed = {f.id for f in pages[page.page].facts}
        where = f'On page "{page.page}"'
        sentences = [("title", DraftSentence(text=page.title, fact_ids=page.headline.fact_ids))]
        sentences += [("headline", page.headline)]
        sentences += [(f"sentence {i + 1}", s) for i, s in enumerate(page.body)]
        sentences += [
            (f"action {i + 1}", DraftSentence(text=f"{a.text} {a.check}", fact_ids=a.fact_ids))
            for i, a in enumerate(page.actions)
        ]
        cited_on_page: set[str] = set()
        for label, s in sentences:
            found += _sentence(f"{where}, {label}", s, facts, allowed, table, words, named, context)
            cited_on_page |= set(s.fact_ids) | {m.group(1) for m in TOKEN.finditer(s.text)}
        head = [facts[i] for i in page.headline.fact_ids if i in facts]
        if page.page != "actions" and not any(f.confidence == "high" for f in head):
            found.append(f"{where}, the headline must rest on at least one high-confidence fact.")
        for fact_id in cited_on_page:
            fact = facts.get(fact_id)
            if fact and fact.kind == "period_change" and fact.metric in simpsons:
                paradox = next(
                    f for f in report.facts if f.kind == "simpsons" and f.metric == fact.metric
                )
                if paradox.id not in cited_on_page:
                    found.append(
                        f"{where}, {fact_id} is an overall change that Simpson's paradox makes "
                        f"misleading; cite {paradox.id} on the same page."
                    )
    return found


def _sentence(  # noqa: PLR0913
    where: str,
    s: DraftSentence,
    facts: dict[str, Fact],
    allowed: set[str],
    table: dict[str, str],
    words: set[str],
    named: set[str],
    context: str | None,
) -> list[str]:
    found = []
    tokens = [m.group(0) for m in TOKEN.finditer(s.text)]
    for token in tokens:
        if token not in table:
            found.append(f"{where}: {token} is not a fact or a part of one.")
        elif token[1:5] not in allowed:
            found.append(f"{where}: {token} is not one of this page's facts.")
    for fact_id in s.fact_ids:
        if fact_id not in facts:
            found.append(f"{where}: fact {fact_id} does not exist.")

    plain = TOKEN.sub(" ", s.text)
    for name in sorted(named, key=len, reverse=True):
        plain = plain.replace(name, " ")
    if NUMBER.search(plain):
        found.append(f'{where}: "{s.text}" has a number typed in; use a fact token instead.')

    # a capitalised word inside a sentence is a name, and names must come from the data
    for part in re.split(r"(?<=[.!?:;])\s+", plain.strip()):
        for word in WORD.findall(part)[1:]:
            if word[0].isupper() and word.lower().strip("'’") not in words:
                found.append(f'{where}: "{word}" is not a name in the data.')

    if CAUSAL.search(plain) and not (context and s.uses_context):
        found.append(
            f'{where}: "{CAUSAL.search(plain).group(0)}" gives a cause; the data only shows '  # type: ignore[union-attr]
            'association. Use words like "coincides with" or "is associated with", or rely '
            "on the person's context (uses_context)."
        )

    cited = [facts[i] for i in set(s.fact_ids) | {t[1:5] for t in tokens} if i in facts]
    signed = [f for f in cited if f.kind in SIGNED]
    if len(signed) == 1:
        fact = signed[0]
        up, down = bool(UP.search(plain)), bool(DOWN.search(plain))
        if fact.value < 0 and up and not down:
            found.append(f"{where}: says it rose, but {fact.id} fell ({table[f'{{{fact.id}}}']}).")
        if fact.value > 0 and down and not up:
            found.append(f"{where}: says it fell, but {fact.id} rose ({table[f'{{{fact.id}}}']}).")
    low = [f for f in cited if f.confidence == "low"]
    if low and not HEDGE.search(plain):
        found.append(
            f"{where}: {low[0].id} is low confidence ({low[0].n} rows); hedge it, for example "
            '"may" or "on a small sample".'
        )
    return found
