"""The story: the plan, the validator's gate, the writer with a (mock) model, and the
template. The gates: bad drafts are refused, every number traces to a fact, and nothing
from a row of data reaches the model."""

from __future__ import annotations

import json
from collections.abc import Sequence
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from data_storyteller import DataStorytellerAgent, cleaning, facts, setup, story
from data_storyteller.ingest import read_source
from data_storyteller.models import Draft, DraftPage, DraftSentence, FactsReport, Story
from data_storyteller.profile import profile
from data_storyteller.story import template, validate
from data_storyteller.story.plan import StoryPlan, pulse
from data_storyteller.story.tokens import values
from margin_core import Answer, RunContext
from margin_core.llm import Message, MockLLM


@cache
def _facts(path: str) -> tuple[FactsReport, str | None, tuple[str, ...]]:
    plan = cleaning.plan(next(iter(read_source(path).tables.values())))
    clean = cleaning.apply(plan, {q.id: Answer.of(q, q.default, "default") for q in plan.questions})
    columns = profile(clean)
    asked = setup.questions(columns)
    story_setup, _ = setup.resolve(
        columns, {q.id: Answer.of(q, q.default, "default") for q in asked}
    )
    report = facts.verify(clean.frame, facts.compute(clean.frame, columns, story_setup))
    return report, story_setup.north_star, tuple(c.name for c in columns)


def planned(path: Path, **chosen: Any) -> tuple[FactsReport, StoryPlan, list[str]]:
    report, main, columns = _facts(str(path))
    asked = story.questions(report, main)
    answers = {q.id: Answer.of(q, chosen.get(q.id, q.default), "default") for q in asked}
    return report, story.build(report, main, answers), list(columns)


def good_draft(plan: StoryPlan, report: FactsReport) -> Draft:
    """What a careful writer would return: tokens for every number, hedges where needed."""
    pages = []
    for page in plan.pages:
        head = page.headline
        hedge = " This may not hold on a small sample." if head.confidence == "low" else ""
        pages.append(
            DraftPage(
                page=page.id,
                title=head.title,
                headline=DraftSentence(
                    text=f"The value here is {{{head.id}}}.{hedge}", fact_ids=[head.id]
                ),
                body=[
                    DraftSentence(
                        text=f"Another fact is {{{f.id}}}."
                        + (" It may not hold." if f.confidence == "low" else ""),
                        fact_ids=[f.id],
                    )
                    for f in page.facts[1:3]
                ],
            )
        )
    return Draft(title="What the data shows", pages=pages)


def sentence(text: str, *ids: str) -> DraftSentence:
    return DraftSentence(text=text, fact_ids=list(ids))


# ---- the plan ---------------------------------------------------------------------------


def test_the_plan_puts_traps_in_and_heads_every_page_with_a_sure_fact(
    golden: dict[str, Path],
) -> None:
    report, plan, _ = planned(golden["ecommerce"])
    assert plan.shape == "performance"
    roles = [p.role for p in plan.pages]
    assert roles[:2] == ["pulse", "drivers"] and roles[-1] == "actions"
    # the pulse opens with how sales moved from the start of the data to the end
    assert plan.pages[0].headline.components["against"] == "start"
    assert any(f.kind == "partial_period" for f in plan.facts)  # always told
    for page in plan.pages:
        if page.role != "actions":
            assert page.headline.confidence == "high"

    _, saas, _ = planned(golden["saas"])
    assert any(f.kind == "simpsons" for f in saas.facts)


def test_the_person_chooses_the_insights(golden: dict[str, Path]) -> None:
    report, main, _ = _facts(str(golden["logistics"]))
    (insights, context) = story.questions(report, main)
    assert insights.kind == "multi_choice" and context.kind == "text" and not context.required
    pick = insights.choices[-1].value
    _, plan, _ = planned(golden["logistics"], insights=[pick])
    frame = {f.id for f in pulse(report, main)}
    always = ("partial_period", "simpsons")
    chosen = {f.id for f in plan.facts if f.kind not in always and f.id not in frame}
    assert chosen == {pick}


# ---- the validator ----------------------------------------------------------------------


@pytest.fixture
def ecommerce(golden: dict[str, Path]) -> tuple[FactsReport, StoryPlan, list[str]]:
    return planned(golden["ecommerce"])


def check(
    draft: Draft, setup_: tuple[FactsReport, StoryPlan, list[str]], context: str | None = None
) -> list[str]:
    report, plan, columns = setup_
    return validate.problems(draft, plan, report, columns, context)


def with_headline(plan: StoryPlan, report: FactsReport, s: DraftSentence) -> Draft:
    draft = good_draft(plan, report)
    draft.pages[0].headline = s
    return draft


def test_a_careful_draft_passes(ecommerce: tuple[FactsReport, StoryPlan, list[str]]) -> None:
    report, plan, _ = ecommerce
    assert check(good_draft(plan, report), ecommerce) == []


def test_an_invented_number_is_refused(ecommerce: tuple[FactsReport, StoryPlan, list[str]]) -> None:
    report, plan, _ = ecommerce
    head = plan.pages[0].headline.id
    bad = with_headline(plan, report, sentence(f"Sales grew 25% to {{{head}}}.", head))
    assert any("number typed in" in p for p in check(bad, ecommerce))
    unknown = with_headline(plan, report, sentence("Sales reached {F999}.", head))
    assert any("{F999} is not a fact" in p for p in check(unknown, ecommerce))


def test_a_name_not_in_the_data_is_refused(
    ecommerce: tuple[FactsReport, StoryPlan, list[str]],
) -> None:
    report, plan, _ = ecommerce
    head = plan.pages[0].headline.id
    bad = with_headline(plan, report, sentence(f"Sales in Antarctica reached {{{head}}}.", head))
    assert any('"Antarctica" is not a name' in p for p in check(bad, ecommerce))
    ok = with_headline(plan, report, sentence(f"Sales in Technology reached {{{head}}}.", head))
    assert not any("is not a name" in p for p in check(ok, ecommerce))


def test_the_wrong_direction_is_refused(
    ecommerce: tuple[FactsReport, StoryPlan, list[str]],
) -> None:
    report, plan, _ = ecommerce
    falling = next(
        f
        for f in report.facts
        if f.kind == "member_change" and f.value < 0 and f.confidence == "high"
    )
    drivers = next(p for p in plan.pages if p.role == "drivers")
    drivers.facts.append(falling)
    draft = good_draft(plan, report)
    page = next(p for p in draft.pages if p.page == "drivers")
    page.body = [sentence(f"{falling.member} grew by {{{falling.id}}}.", falling.id)]
    assert any(f"but {falling.id} fell" in p for p in check(draft, ecommerce))


def test_causes_are_refused_unless_the_person_gave_them(
    ecommerce: tuple[FactsReport, StoryPlan, list[str]],
) -> None:
    report, plan, _ = ecommerce
    head = plan.pages[0].headline.id
    causal = sentence(f"The promotion drove sales to {{{head}}}.", head)
    assert any("gives a cause" in p for p in check(with_headline(plan, report, causal), ecommerce))
    causal.uses_context = True
    allowed = check(with_headline(plan, report, causal), ecommerce, "A promotion ran all year.")
    assert not any("gives a cause" in p for p in allowed)


def test_low_confidence_facts_must_be_hedged(
    ecommerce: tuple[FactsReport, StoryPlan, list[str]],
) -> None:
    report, plan, _ = ecommerce
    low = next(f for f in report.facts if f.confidence == "low")
    last = [p for p in plan.pages if p.role != "actions"][-1]
    last.facts.append(low)
    draft = good_draft(plan, report)
    page = next(p for p in draft.pages if p.page == last.id)
    page.body = [sentence(f"Sales hit {{{low.id}}}.", low.id)]
    assert any("low confidence" in p for p in check(draft, ecommerce))
    page.body = [sentence(f"Sales may have hit {{{low.id}}}.", low.id)]
    assert not any("low confidence" in p for p in check(draft, ecommerce))


def test_the_title_hype_signs_and_repeats_are_checked(
    ecommerce: tuple[FactsReport, StoryPlan, list[str]],
) -> None:
    report, plan, _ = ecommerce
    draft = good_draft(plan, report)
    draft.title = "Sales growth driven by technology"
    assert any("story title" in p and "gives a cause" in p for p in check(draft, ecommerce))

    head = plan.pages[0].headline.id
    hype = with_headline(plan, report, sentence(f"Sales rose significantly, {{{head}}}.", head))
    assert any("overstates" in p for p in check(hype, ecommerce))

    signed = with_headline(plan, report, sentence(f"Sales rose {{{head}}}.", head))
    assert any(f"{{{head}.size}}" in p for p in check(signed, ecommerce))
    sized = with_headline(plan, report, sentence(f"Sales rose {{{head}.size}}.", head))
    assert not any("signed" in p for p in check(sized, ecommerce))

    repeated = good_draft(plan, report)
    repeated.pages[0].body = [repeated.pages[0].headline]
    assert any("repeated" in p for p in check(repeated, ecommerce))


# ---- the writer -------------------------------------------------------------------------


def respond(drafts: Sequence[Draft | str]) -> MockLLM:
    return MockLLM([d if isinstance(d, str) else d.model_dump_json() for d in drafts])


async def write(
    llm: MockLLM | None, setup_: tuple[FactsReport, StoryPlan, list[str]]
) -> tuple[Story, list[str]]:
    report, plan, columns = setup_
    warnings: list[str] = []
    told = await story.write(llm, plan, report, about="Data: test.", title="T", columns=columns,
                             warn=warnings.append)  # fmt: skip
    return told, warnings


async def test_a_good_draft_becomes_the_story(
    ecommerce: tuple[FactsReport, StoryPlan, list[str]],
) -> None:
    report, plan, _ = ecommerce
    told, warnings = await write(respond([good_draft(plan, report)]), ecommerce)
    assert told.written_by == "model" and told.attempts == 1 and warnings == []
    table = values(report)
    head = plan.pages[0].headline
    assert told.pages[0].headline.text == f"The value here is {table['{' + head.id + '}']}."
    assert story.stray_numbers(told, report) == []


async def test_a_refused_draft_is_sent_back_with_the_problems(
    ecommerce: tuple[FactsReport, StoryPlan, list[str]],
) -> None:
    report, plan, _ = ecommerce
    head = plan.pages[0].headline.id
    llm = respond(
        [with_headline(plan, report, sentence("Sales grew 25%.", head)), good_draft(plan, report)]
    )
    told, _ = await write(llm, ecommerce)
    assert told.written_by == "model" and told.attempts == 2
    assert told.rejected and "number typed in" in told.rejected[0]
    second = llm.calls[1].messages
    assert second[-1].role == "user" and "number typed in" in second[-1].content


async def test_drafts_that_keep_failing_fall_back_to_the_template(
    ecommerce: tuple[FactsReport, StoryPlan, list[str]],
) -> None:
    report, plan, _ = ecommerce
    head = plan.pages[0].headline.id
    bad = with_headline(plan, report, sentence("Sales grew 25%.", head))
    told, warnings = await write(respond([bad, bad, bad]), ecommerce)
    assert told.written_by == "template" and told.attempts == 3 and len(told.rejected) >= 3
    assert "failed the checks" in warnings[0]


async def test_a_broken_model_falls_back_to_the_template(
    ecommerce: tuple[FactsReport, StoryPlan, list[str]],
) -> None:
    told, warnings = await write(respond(["not json", "still not json"]), ecommerce)
    assert told.written_by == "template" and "could not write the story" in warnings[0]


@pytest.mark.parametrize("name", ["ecommerce", "saas", "logistics"])
def test_the_template_story_traces_every_number(golden: dict[str, Path], name: str) -> None:
    report, plan, _ = planned(golden[name])
    told = template.write(plan, "T")
    assert told.pages and story.stray_numbers(told, report) == []
    actions = next(p for p in told.pages if p.role == "actions")
    assert actions.actions and all(a.check for a in actions.actions)


# ---- privacy ----------------------------------------------------------------------------


async def test_no_row_and_no_personal_data_reaches_the_model(tmp_path: Path) -> None:
    rows = ["customer_name,email,region,order_id,sales,order_date"]
    for i in range(240):
        rows.append(
            f"Person {chr(65 + i % 26)}{chr(97 + i % 7)} Name,p{i}@mail.com,"
            f"{['North', 'South', 'East'][i % 3]},ORD-{i:05d},{100 + (i * 37) % 900},"
            f"2024-{1 + i % 9:02d}-{1 + i % 27:02d}"
        )
    path = tmp_path / "orders.csv"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    seen: list[str] = []

    def responder(messages: Sequence[Message], system: str | None) -> str:
        seen.append((system or "") + "\n".join(m.content for m in messages))
        return "{}"  # not a draft: the agent falls back to the template

    result = await DataStorytellerAgent(llm=MockLLM(responder)).execute(
        {"dataset": str(path), "stop_after": "story", "assume_defaults": True}, RunContext()
    )
    assert result.status == "ok", result.error
    assert seen, "the model was never asked"
    sent = "\n".join(seen)
    for secret in ("@mail.com", "Person ", "ORD-00"):
        assert secret not in sent
    for i in (17, 101, 202):  # no row's own sales value appears as a row
        assert f"ORD-{i:05d}" not in sent
    assert json.loads(result.model_dump_json())["data"]["story"]["written_by"] == "template"
