"""The story without a model: written from the plan by rules.

Used when no model is set up, when it fails, or when its drafts keep failing validation.
Plain, but never wrong: every sentence is a fact's own statement, so every number in it
comes from the facts engine.
"""

from __future__ import annotations

from data_storyteller.facts.base import readable, sentence
from data_storyteller.models import Fact, Story, StoryAction, StoryPage, StorySentence
from data_storyteller.story.plan import TITLES, StoryPlan
from data_storyteller.story.tokens import fact_values, parts, tokenized


def _sentence(fact: Fact) -> StorySentence:
    text = fact.statement
    with_tokens = tokenized(fact)
    extra = ""
    if fact.confidence == "low":
        extra += " This rests on a small sample, so treat it with care."
    if fact.caveats and fact.kind in ("partial_period", "simpsons"):
        extra += " " + fact.caveats[0]
    pieces = parts(with_tokens + extra, fact_values(fact))
    return StorySentence(text=text + extra, fact_ids=[fact.id], parts=pieces)


def action(fact: Fact) -> StoryAction | None:
    """A next step the fact suggests, framed as a hypothesis with a way to test it."""
    metric = fact.metric_label or readable(fact.metric or "the main number")
    group = readable(fact.dimension or "group")
    member = fact.member or ""
    if fact.kind == "member_change" and fact.value < 0:
        text = f"Find out why {metric} for {member} fell."
        check = (
            f"Compare {member} with the other {group} values period by period, and look for "
            "changes in price, product mix or availability at the time the fall began."
        )
    elif fact.kind == "member_change":
        text = f"Find out what is working for {member}, and whether it can be repeated."
        check = "Check that the growth holds in each period and is not one large order or period."
    elif fact.kind == "segment_gap":
        direction = "high" if fact.value > 1 else "low"
        text = f"Look into why {metric} is so {direction} for {member}."
        check = f"Compare a sample of {member}'s records with those of the other {group} values."
    elif fact.kind == "anomaly" and fact.period:
        text = f"Find out what happened in {fact.period}."
        check = (
            f"Read the records behind {fact.period}: a one-off event, a data error or a change "
            "in process would each explain it differently."
        )
    elif fact.kind == "concentration":
        text = f"Check how much {metric} depends on the largest {group} values."
        check = f"List the top {group} values and follow each one's {metric} over time."
    elif fact.kind == "simpsons":
        text = f"Report {metric} within each {group}, not only overall."
        check = f"Track {metric} for each {group} separately before drawing conclusions."
    elif fact.kind == "correlation":
        other = readable(fact.dimension or "")
        text = f"Test whether changing {metric} changes {other}."
        check = (
            "Run a small, controlled test first: the data shows that they move together, not why."
        )
    else:
        return None
    return StoryAction(text=text, check=check, fact_ids=[fact.id])


def write(plan: StoryPlan, title: str) -> Story:
    pages = []
    for page in plan.pages:
        if page.role == "actions":
            actions = [a for f in page.facts if (a := action(f)) is not None]
            if not actions:
                continue
            pages.append(
                StoryPage(
                    id=page.id,
                    role=page.role,
                    title=TITLES[page.role],
                    headline=StorySentence(
                        text="These are hypotheses to test, not conclusions.",
                        fact_ids=[],
                    ),
                    actions=actions,
                    fact_ids=[f.id for f in page.facts],
                )
            )
            continue
        rest = [f for f in page.facts if f is not page.headline]
        pages.append(
            StoryPage(
                id=page.id,
                role=page.role,
                title=sentence(page.headline.title),
                headline=_sentence(page.headline),
                body=[_sentence(f) for f in rest],
                fact_ids=[f.id for f in page.facts],
            )
        )
    return Story(title=title, shape=plan.shape, pages=pages, written_by="template",
                 context=plan.context)  # fmt: skip
