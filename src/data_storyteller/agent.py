"""Data Storyteller: cleans a table, explains every change, and sets up the story in it."""

from __future__ import annotations

import asyncio
from pathlib import Path

from margin_core import AgentResult, Answer, BaseAgent, Choice, Question, RunContext
from margin_core.models import ResultStatus

from data_storyteller import cleaning, facts, report, setup, story
from data_storyteller.ingest import Source, read_source
from data_storyteller.models import DataStorytellerInput, DataStorytellerOutput
from data_storyteller.profile import profile


class DataStorytellerAgent(BaseAgent):
    id = "data-storyteller"
    name = "Data Storyteller"
    description = (
        "Cleans a table of data, shows every change it made, and tells the story in it with charts."
    )
    version = "0.1.0"
    InputModel = DataStorytellerInput
    OutputModel = DataStorytellerOutput
    primary_input = "dataset"
    uses_llm = True  # to write the story; cleaning and facts never call a model

    async def run(self, input: DataStorytellerInput, ctx: RunContext) -> AgentResult:
        ctx.report_progress("read", f"Reading {Path(input.dataset).name}", 0.05)
        source = await asyncio.to_thread(read_source, input.dataset)
        table = await self._choose_table(source, input, ctx)

        ctx.check_cancelled()
        ctx.report_progress("inspect", "Checking every column", 0.2)
        plan = await asyncio.to_thread(cleaning.plan, source.tables[table])
        questions = list(plan.questions)
        if input.stop_after == "story":
            provisional = cleaning.apply(
                plan, {q.id: Answer.of(q, q.default, "default") for q in questions}
            )
            questions += setup.questions(profile(provisional))

        answers: dict[str, Answer] = {}
        if questions:
            answers = await ctx.ask(
                questions,
                title="Check the data",
                detail="Nothing has changed yet. These parts of the file can be read more than "
                "one way; the suggested answer is marked.",
                preset=input.answers,
                assume_defaults=input.assume_defaults,
            )

        ctx.check_cancelled()
        ctx.report_progress("clean", "Cleaning", 0.6)
        clean = await asyncio.to_thread(cleaning.apply, plan, answers)
        ctx.report_progress("profile", "Describing the columns", 0.8)
        columns = profile(clean)
        data = report.output(source, table, clean, columns)

        status: ResultStatus = "ok"
        warnings: list[str] = []
        if input.stop_after == "story":
            data.setup, problems = setup.resolve(columns, answers)
            warnings += problems
            ctx.check_cancelled()
            ctx.report_progress("facts", "Computing the facts, then checking each one", 0.75)
            found = await asyncio.to_thread(facts.compute, clean.frame, columns, data.setup)
            data.facts = await asyncio.to_thread(facts.verify, clean.frame, found)

            # checkpoint 2: what the story tells, and context only the person has
            chosen = await ctx.ask(
                story.questions(data.facts, data.setup.north_star),
                title="Choose the story",
                detail="These are the facts worth telling, each checked twice. Pick what the "
                "story should cover.",
                preset=input.answers,
                assume_defaults=input.assume_defaults,
            )
            answers |= chosen
            plan_ = story.build(data.facts, data.setup.north_star, chosen)
            ctx.check_cancelled()
            ctx.report_progress("story", "Writing the story", 0.85)
            rows = f"{len(clean.frame):,} rows, one {data.setup.grain.removeprefix('one ')} each"
            data.story = await story.write(
                ctx.llm,
                plan_,
                data.facts,
                about=f"Data: {source.path.name}, {rows}. Main number: "
                f"{data.setup.north_star or 'the number of rows'}.",
                title=f"What {source.path.name} shows",
                columns=[c.name for c in columns],
                warn=ctx.warn,
            )

        if input.output_dir:
            ctx.report_progress("write", f"Writing to {input.output_dir}", 0.9)
            data.files = await asyncio.to_thread(
                report.write, input.output_dir, data, clean, answers
            )

        ctx.report_progress("done", "Finished", 1.0)
        assumed = sum(a.source == "default" for a in answers.values())
        return self.make_result(
            summary=report.summary(data, assumed),
            output=data,
            findings=report.findings(data, answers),
            warnings=warnings,
            status=status,
        )

    async def _choose_table(
        self, source: Source, input: DataStorytellerInput, ctx: RunContext
    ) -> str:
        names = list(source.tables)
        if input.table is not None:
            if input.table not in source.tables:
                raise ValueError(
                    f"{source.path.name} has no table {input.table!r}; it has {', '.join(names)}"
                )
            return input.table
        if len(names) == 1:
            return names[0]
        largest = max(names, key=lambda n: len(source.tables[n].rows))
        answers = await ctx.ask(
            [
                Question(
                    id="table",
                    prompt=f"{source.path.name} has {len(names)} tables. "
                    "Which one should be cleaned?",
                    choices=[
                        Choice(value=n, label=n, detail=f"{len(source.tables[n].rows):,} rows")
                        for n in names
                    ],
                    default=largest,
                )
            ],
            title="Choose the table",
            preset=input.answers,
            assume_defaults=input.assume_defaults,
        )
        return str(answers["table"].value)
