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

The assistant's own working notes are not reproduced here; the code and commit
history show the evolution.
