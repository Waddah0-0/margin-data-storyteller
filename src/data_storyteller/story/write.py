"""Writing the story with a model, under the validator's eye.

The model gets the plan, the page's facts with their statements (so it can judge size)
and the tokens it may use. It never sees a row of data: only facts, which are
aggregates, and column names. Its draft is checked; problems go back to it, at most
twice. If it still fails, or there is no model, the template writes the story.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from margin_core.llm import LLMClient, Message

from data_storyteller.models import Draft, FactsReport, Story, StoryAction, StoryPage, StorySentence
from data_storyteller.story import template, validate
from data_storyteller.story.plan import TITLES, StoryPlan
from data_storyteller.story.tokens import TOKEN, fact_values, render, values

MAX_DRAFTS = 3

SYSTEM = """You write the text of a short data story for a busy reader.

Rules:
1. Never type a digit. Every number is a token from the page's list, copied exactly,
   such as {F012} or {F012.before}.
2. Use only the facts listed for a page, and put every fact a sentence relies on in its
   fact_ids.
3. Name only groups, periods and columns that appear in the facts.
4. Describe links between numbers as associations: "is associated with", "coincides
   with", "moves with". Never give causes (because, due to, drove, led to, caused,
   thanks to) unless the sentence relies on the context the person gave; then set
   uses_context to true.
5. When a fact has low confidence, hedge it: "may", "on a small sample".
6. A title states the message ("Technology carried the growth"), not the topic
   ("Sales by category").
7. One message per page. The headline is one sentence; at most four short body
   sentences.
8. On the actions page, each action is a hypothesis with a concrete check, not an instruction.
9. Plain English: no jargon, no hype, no exclamation marks, no questions."""

PURPOSE = {
    "pulse": "the main number, and how it moves over time",
    "drivers": "where the main number comes from, and which groups changed it",
    "nuance": "gaps between groups, numbers that move together, surprises and traps",
    "actions": "two or three hypotheses worth testing next, each with a concrete check",
}


def prompt(plan: StoryPlan, report: FactsReport, about: str) -> str:
    lines = [about]
    lines.append(f"Context from the person: {plan.context}" if plan.context else
                 "Context from the person: none. So no sentence may give a cause.")  # fmt: skip
    lines.append(f"Story shape: {plan.shape}.")
    lines.append("")
    lines.append("Pages, in order:")
    for page in plan.pages:
        lines.append(f'- page "{page.id}": {PURPOSE[page.role]}.')
        if page.role != "actions":
            lines.append(f"  Its headline must rest on {page.headline.id}.")
        for fact in page.facts:
            tokens = ", ".join(f"{k}={v}" for k, v in fact_values(fact).items())
            lines.append(f"  {fact.id} ({fact.confidence} confidence): {fact.statement}")
            lines.append(f"    Tokens: {tokens}")
            for caveat in fact.caveats:
                lines.append(f"    Caveat: {caveat}")
    lines.append("")
    lines.append("Return the draft: a title, and one entry in pages for each page above.")
    return "\n".join(lines)


def to_story(draft: Draft, plan: StoryPlan, report: FactsReport) -> Story:
    table = values(report)
    by_id = {p.id: p for p in plan.pages}
    pages = []
    for page in draft.pages:
        planned = by_id[page.page]

        def sentence(text: str, fact_ids: list[str]) -> StorySentence:
            return StorySentence(text=render(text, table), fact_ids=fact_ids)

        pages.append(
            StoryPage(
                id=page.page,
                role=planned.role,
                title=render(page.title, table) or TITLES[planned.role],
                headline=sentence(page.headline.text, page.headline.fact_ids),
                body=[sentence(s.text, s.fact_ids) for s in page.body],
                actions=[
                    StoryAction(
                        text=render(a.text, table),
                        check=render(a.check, table),
                        fact_ids=a.fact_ids,
                    )  # fmt: skip
                    for a in page.actions
                ],
                fact_ids=[f.id for f in planned.facts],
            )
        )
    return Story(title=render(draft.title, table), shape=plan.shape, pages=pages,
                 written_by="model", context=plan.context)  # fmt: skip


async def write(
    llm: LLMClient | None,
    plan: StoryPlan,
    report: FactsReport,
    *,
    about: str,
    title: str,
    columns: list[str],
    warn: Callable[[str], None],
) -> Story:
    """The story, by the model if it can pass the checks, by the template otherwise."""
    if llm is None:
        warn("No model is set up, so the story was written from a template.")
        return template.write(plan, title)
    messages = [Message(role="user", content=prompt(plan, report, about))]
    rejected: list[str] = []
    for attempt in range(1, MAX_DRAFTS + 1):
        try:
            draft = await llm.complete_structured(
                Draft, messages, system=SYSTEM, max_tokens=3000, temperature=0.2
            )
        except Exception as exc:  # noqa: BLE001 - any model failure: fall back, and say why
            warn(f"The model could not write the story ({exc}); it was written from a template.")
            story = template.write(plan, title)
            story.attempts, story.rejected = attempt - 1, rejected
            return story
        found = validate.problems(draft, plan, report, columns, plan.context)
        missing = [p.id for p in plan.pages if p.id not in {d.page for d in draft.pages}]
        found += [f'Page "{p}" is missing.' for p in missing]
        if not found:
            story = to_story(draft, plan, report)
            story.model, story.attempts, story.rejected = llm.model, attempt, rejected
            return story
        rejected += [f"Draft {attempt}: {p}" for p in found]
        messages += [
            Message(role="assistant", content=draft.model_dump_json()),
            Message(
                role="user",
                content="The draft was refused for these reasons:\n"
                + "\n".join(f"- {p}" for p in found)
                + "\nReturn the whole draft again, corrected.",
            ),
        ]
    warn(
        f"The model's drafts failed the checks {MAX_DRAFTS} times, so the story was written "
        "from a template. The refused problems are listed in the story."
    )
    story = template.write(plan, title)
    story.attempts, story.rejected, story.model = MAX_DRAFTS, rejected, llm.model
    return story


def stray_numbers(story: Story, report: FactsReport) -> list[str]:
    """Numbers in the finished story that no fact accounts for. Always empty, by
    construction; kept as a final, independent check."""
    table = values(report)
    statements = {f.id: f.statement for f in report.facts}
    labels = {p.label for f in report.facts for p in f.points} | {
        x for f in report.facts for x in (f.period, f.member) if x
    }
    found = []
    for page in story.pages:
        parts: list[tuple[str, list[str]]] = [(page.title, page.fact_ids)]
        parts += [(s.text, s.fact_ids) for s in [page.headline, *page.body]]
        parts += [(f"{a.text} {a.check}", a.fact_ids) for a in page.actions]
        for text, cited in parts:
            allowed = sorted(
                set(table.values()) | labels | {statements[i] for i in cited if i in statements},
                key=len,
                reverse=True,
            )
            rest = text
            for piece in allowed:
                rest = rest.replace(piece, " ")
            if re.search(r"\d", rest) and not TOKEN.search(text):
                found.append(f'{page.id}: "{text}"')
    return found
