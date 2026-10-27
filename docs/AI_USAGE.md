# AI usage disclosure

The submission guidelines allow LLM tools and ask for transparency. This file
says where AI was used, how heavily, and what I reviewed. The raw prompts are in
[`prompts/PROMPT_HISTORY.md`](../prompts/PROMPT_HISTORY.md).

## Tool

Claude (Anthropic), used as an agentic coding assistant that could read the
problem statement, write files, run Python and tests in a sandbox, and make git commits.

## Where AI accelerated the work

| Area | AI involvement |
|---|---|
| Reading the brief and planning the architecture | heavy — Claude proposed the module split and the "schema as single source of truth" idea |
| Data model (`schema.py`) | heavy — drafted by Claude from the brief's entity list |
| Generator / simulation (`generate.py`) | heavy — drafted by Claude; score model was tuned after looking at the first outputs (initial pass rate ~35 % was unrealistic) |
| Recommender rules (`recommend.py`) | heavy — drafted by Claude |
| Validator, evaluation, report, CLI | heavy — drafted by Claude |
| Tests | heavy — drafted by Claude |
| Docs (`README.md`, `DESIGN.md`) | heavy — drafted by Claude |
| Results report page | heavy — redesigned by Claude after I said the first report looked too plain |

Bugs that were found by running the code (not by reading it) and then fixed:

* a helper that built composite keys returned column names on empty input and
  produced 2 false "failures" — found because the validator reported unexpected issues on a clean baseline;
* the future-mastery injector sometimes injected fewer anomalies than requested;
* loops were injected after decisions, so the engine's exception path never fired —
  the pipeline was re-ordered so the engine sees (and escalates) them;
* an interactive learner view (later removed to keep the project simple) made a rule weakness visible: an item that also covered a prerequisite as a
  secondary skill skipped that prerequisite. The rule was made stricter (every prerequisite of every
  covered skill must be met) and an independent test was added that checks this from the raw tables.

## What I reviewed, changed or validated myself

> Fill this in honestly before submitting — reviewers may ask you to explain any line.

- [x] Ran `python -m pytest -q` (14 passed) and `python -m skillpath run --scale starter` on my own Windows laptop; my results matched exactly
- [ ] Read `schema.py`, `mastery.py` and `recommend.py` end to end and can explain every rule
- [x] Opened `reports/starter/report.html` and checked the numbers make sense
- [ ] Opened a few rows of `data/starter/tables/recommendation.csv` and traced one recommendation back to its mastery snapshot and attempts
- [x] Changes I made myself: asked for a clearer results page, then asked to remove a heavy interactive page so the project stays simple and focused on the brief
- [x] Decisions I disagreed with / would change: the first report looked too plain, so I asked for a better design
