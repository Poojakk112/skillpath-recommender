# Prompt history

Chronological record of the prompts given to the AI assistant (Claude) for this
project, with a short note on what each produced. Add any further prompts you use
(including ones in other tools) to the end of this file.

---

### 1 — 27 Oct 2026, 10:52 IST

> start yottaflex task yes i want simple and stand out project which should be effective

The assistant read the Round 3 email and the problem statement on the candidate
portal, plus the submission-guidelines and FAQ PDFs, then:

1. Proposed a plain Python (numpy + pandas) design with the schema as the single source of truth.
2. Wrote `config.py`, `ids.py`, `schema.py`, `mastery.py`, `lifecycle.py`, `generate.py`, `recommend.py`
   and smoke-tested them on a tiny scale (commit 1).
3. Recalibrated the simulated score model after seeing unrealistic first results
   (scores ~20–40, almost no completed paths).
4. Wrote `inject.py`, `io.py`, `validate.py`, `pipeline.py`; first validation run
   found 2 false failures caused by a key-building bug, fixed (commit 2).
5. Wrote `evaluate.py`, `report.py`, `cli.py`; re-ordered injection so learner loops
   happen before decisions and are escalated by the engine (commit 3).
6. Wrote the tests (13, all passing), ran the full 10k-learner / 500k-attempt scale
   with the reproducibility check, and wrote the docs (commit 4+).

### 2 — 27 Oct 2026, 11:48–12:12 IST (running it locally)

> now you tell me slowly what i exactly i need to do dont tell me all at once

Step-by-step local setup on Windows: unzip, open a terminal in the folder, install requirements,
run the tests (13 passed), run the starter pipeline and open the report. pandas 3.1 on my machine
printed deprecation warnings, so `validate.py` was changed to use `merge(...)` instead of
`join(..., rsuffix=...)`.

> but is not well designed properly i think so / you see and what exactly is this project is /
> what exactly they ask to do? / but its frontend part looks normal / do both

Asked for a plain-language explanation of the project and the brief, then for a better-looking
frontend. Claude restyled the results report and built the interactive learner explorer
(`explore.py`, `explorer_template.html`). Reviewing the explorer exposed the prerequisite-rule
weakness described in `docs/AI_USAGE.md`; the rule was tightened and a test added.

> i feel this is too heavy if they start ask me question i cant able to tell we follow what they had
> suggested to do we will do that and add some little exactra feature which stand out my project

Decided to keep only what the brief asks for plus one small extra (the HTML results report).
The interactive explorer was removed; the stricter prerequisite rule and its test were kept.

The assistant's own working notes are not reproduced here; the code and commit
history show the evolution.
