"""Planning the story by rules: its shape, which insights it uses, and its pages.

A small local model plans unreliably, so the model only writes. Here the facts are ranked
(how much they matter, how sure they are, and variety), the person picks the insights at
the second checkpoint, and the pages are laid out: the pulse (the main number and how it
moves), the drivers (where it comes from and what changed), the nuance (gaps, links,
surprises, traps) and the actions (what to check next).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from margin_core import Answer, Choice, Question

from data_storyteller.models import Fact, FactsReport, PageRole, StoryShape

CONFIDENCE = {"high": 1.0, "medium": 0.8, "low": 0.5}
PAGE_OF: dict[str, PageRole] = {
    "total": "pulse",
    "period_change": "pulse",
    "trend": "pulse",
    "partial_period": "pulse",
    "breakdown": "drivers",
    "concentration": "drivers",
    "member_change": "drivers",
    "segment_gap": "nuance",
    "correlation": "nuance",
    "anomaly": "nuance",
    "simpsons": "nuance",
}
ALWAYS = {"partial_period", "simpsons"}  # traps the story must mention if they exist
ACTIONABLE = {"member_change", "segment_gap", "anomaly", "concentration", "simpsons", "correlation"}
CONTEXT = {"total", "average"}  # the frame of the story, not insights to choose from
CHOICES = 12
CHOSEN = 6
PER_PAGE = 5
TITLES: dict[PageRole, str] = {
    "pulse": "The headline",
    "drivers": "What drives it",
    "nuance": "What else stands out",
    "actions": "What to check next",
}


@dataclass
class PagePlan:
    id: str
    role: PageRole
    facts: list[Fact]
    headline: Fact


@dataclass
class StoryPlan:
    shape: StoryShape
    pages: list[PagePlan] = field(default_factory=list)
    context: str | None = None

    @property
    def facts(self) -> list[Fact]:
        return [f for p in self.pages for f in p.facts]


def ranked(report: FactsReport, main: str | None) -> list[Fact]:
    """Insights, most telling first. Repeats of one kind count for less each time, and a
    group appears once (its strongest fact), so the story is not five changes in a row,
    or the same region twice."""
    candidates = [
        f
        for f in report.facts
        if f.kind in PAGE_OF
        and f.kind not in CONTEXT
        and "both_sums" not in f.components  # sales and profit rise together: no news
    ]
    seen: Counter[str] = Counter()
    members: set[tuple[str, str | None, str]] = set()
    scored = []
    for fact in sorted(candidates, key=lambda f: -f.importance * CONFIDENCE[f.confidence]):
        if fact.member is not None:
            key = (fact.kind, fact.dimension, fact.member)
            if key in members:
                continue
            members.add(key)
        score = fact.importance * CONFIDENCE[fact.confidence] * 0.85 ** seen[fact.kind]
        seen[fact.kind] += 1
        scored.append((score, fact))
    return [f for _, f in sorted(scored, key=lambda s: -s[0])]


def pulse(report: FactsReport, main: str | None) -> list[Fact]:
    """What every story opens with: how the main number moved from the start of the data to
    the end, its steady trend if it has one, and its total. Not a choice: the frame for the
    insights."""
    found: list[Fact] = []
    for fact in report.facts:
        if fact.metric != main or main is None:
            continue
        if fact.kind == "period_change" and fact.components.get("against") == "start":
            found.insert(0, fact)
        elif (
            fact.kind == "period_change"
            and fact.components.get("against") == "previous"
            and len(report.periods) <= 3
        ):
            found.insert(0, fact)  # with two or three periods, this is the whole movement
        elif fact.kind == "trend" and not fact.caveats:
            found.append(fact)
        elif fact.kind in ("total", "average"):
            found.append(fact)
    return found


def defaults(options: list[Fact]) -> set[str]:
    """The suggested picks: taken in turn from what drives the main number and what else
    stands out, so the story has both, best first within each. Other changes over time
    only fill what is left."""
    queues = [[f for f in options if PAGE_OF[f.kind] == role] for role in ("drivers", "nuance")]
    picked: list[str] = []
    while len(picked) < CHOSEN and any(queues):
        for queue in queues:
            if queue and len(picked) < CHOSEN:
                picked.append(queue.pop(0).id)
    rest = [f.id for f in options if PAGE_OF[f.kind] == "pulse"]
    return set(picked + rest[: CHOSEN - len(picked)])


def shape(report: FactsReport) -> StoryShape:
    kinds: Counter[str] = Counter(str(f.kind) for f in report.facts)
    over_time = len(report.periods) >= 2
    groups = kinds["breakdown"] + kinds["member_change"] + kinds["segment_gap"]
    if sum(kinds[k] for k in PAGE_OF) < 3:
        return "profile"
    if over_time and groups:
        return "performance"
    if over_time:
        return "trend"
    if groups:
        return "composition"
    return "relationship"


def questions(report: FactsReport, main: str | None) -> list[Question]:
    """The second checkpoint: which insights the story uses, and context only the person
    has. Context is the only thing that lets the story talk about causes."""
    frame = {f.id for f in pulse(report, main)}
    options = [f for f in ranked(report, main) if f.kind not in ALWAYS and f.id not in frame]
    options = options[:CHOICES]
    picked = defaults(options)
    found = []
    if options:
        found.append(
            Question(
                id="insights",
                prompt="Which of these should the story tell?",
                detail="The most telling come first. Incomplete periods and Simpson's paradox "
                "are always mentioned when they are found.",
                kind="multi_choice",
                section="The story",
                choices=[
                    Choice(
                        value=f.id,
                        label=f.statement,
                        detail=f"{f.confidence.capitalize()} confidence, {f.n:,} rows"
                        + (f". {f.caveats[0]}" if f.caveats else ""),
                    )
                    for f in options
                ],
                default=[f.id for f in options if f.id in picked],
            )
        )
    found.append(
        Question(
            id="context",
            prompt="Is there anything the data can't know?",
            detail="For example a price change, a campaign or a new competitor, with when it "
            "happened. The story says what the data shows; only what you write here can be "
            "given as a reason for it.",
            kind="text",
            required=False,
            section="The story",
        )
    )
    return found


def build(report: FactsReport, main: str | None, answers: dict[str, Answer]) -> StoryPlan:
    ordered = ranked(report, main)
    chosen_ids = answers["insights"].value if "insights" in answers else None
    frame = pulse(report, main)
    if chosen_ids is None:
        options = [f for f in ordered if f.kind not in ALWAYS and f not in frame][:CHOICES]
        chosen_ids = sorted(defaults(options))
    keep = set(chosen_ids) | {f.id for f in ordered if f.kind in ALWAYS}
    context = answers["context"].value if "context" in answers else None

    plan = StoryPlan(shape=shape(report), context=context or None)
    by_role: dict[PageRole, list[Fact]] = {"pulse": list(frame), "drivers": [], "nuance": []}
    for fact in ordered:
        if fact.id in keep and fact not in by_role[PAGE_OF[fact.kind]]:
            by_role[PAGE_OF[fact.kind]].append(fact)
    for role in ("pulse", "drivers", "nuance"):
        facts = by_role[role][:PER_PAGE]
        strong = [f for f in facts if f.confidence == "high"]
        if strong:
            plan.pages.append(PagePlan(id=role, role=role, facts=facts, headline=strong[0]))
        elif facts and plan.pages:
            # no well-founded headline for a page of its own: told on the page before
            plan.pages[-1].facts.extend(facts[: PER_PAGE + 2 - len(plan.pages[-1].facts)])
    told = [f for p in plan.pages if p.role != "pulse" for f in p.facts]
    by_score = sorted(told, key=lambda f: -f.importance * CONFIDENCE[f.confidence])
    kinds: set[str] = set()
    actionable = []
    for fact in by_score + by_score:  # first one of each kind, then the rest
        if fact not in actionable and (fact.kind not in kinds or len(kinds) >= len(by_score)):
            actionable.append(fact)
            kinds.add(fact.kind)
    actionable = [f for f in actionable if f.kind in ACTIONABLE]
    if actionable:
        plan.pages.append(
            PagePlan(id="actions", role="actions", facts=actionable[:3], headline=actionable[0])
        )
    return plan
