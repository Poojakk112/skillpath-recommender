# SkillPath — prerequisite-aware next-activity recommender

Recommends each learner's next learning activities from their skills, the
prerequisite graph, past assessment results and content difficulty — and proves
it with a reproducible synthetic dataset, ~200 integrity checks and a temporal evaluation.

**Plain Python, two dependencies (numpy, pandas), one command, no network.**

```bash
pip install -r requirements.txt
python -m skillpath run --scale starter     # ~10 s: generate -> validate -> evaluate -> report
python -m pytest -q                         # 14 tests
```

Then open two pages in your browser:

* `reports/starter/report.html`: the results (does it work, is the data sound, is it reproducible)
* `reports/starter/explorer.html`: the **learner explorer**. Pick any learner and see their skill map
  (mastery per skill, prerequisite lines, unmet prerequisites in red), the top 3 recommendations with
  their reasons, and which lessons are locked by which prerequisite. One-click examples show a new
  learner, a learner blocked by a prerequisite, one recovering from a failed test and one stuck in a loop.

## Results

Full scale (10,000 learners · 500 skills · 2,000 items · 500,400 attempts · seed 20261022).
Starter-scale numbers are in `reports/starter/report.html` and tell the same story.

| Question | Result |
|---|---|
| Does the engine ever recommend an item whose prerequisites are unmet? | **No.** 0 of 52k engine recommendations; only the 50 injected bad ones are flagged |
| Do recommended items work out better? | Items learners took **after we recommended them passed 77 %** of the time, vs **72 %** for other eligible items and **23 %** for items our gate blocked |
| Is mastery predictive on unseen (future) data? | **AUC 0.74** on 9,961 attempts after the 2026-07-01 cutoff, vs 0.66 for difficulty-only and 0.66 for a learner's average score |
| Does mastery track the hidden truth? | Spearman **0.85** with the simulator's hidden knowledge (84k learner-skill pairs) |
| Are injected bad records caught? | **50/50** for each of the 5 edge-case types — precision 1.0, recall 1.0 |
| Any false alarms on the valid baseline? | **0** unexpected issue types across 202 checks |
| Reproducible? | Same seed → every file byte-identical (sha256); seed + 1 → different |

Every recommendation carries a reason code and a rationale such as:

> `REC-00000035` (NEXT_FRONTIER_SKILL): prerequisites met and skill not yet learned; SKL-000065 mastery 0.30 (n=0); difficulty 2; prerequisites met: SKL-000061 0.64>=0.60
>
> `REC-00000009` (REMEDIATE_PREREQUISITE): all 22 remaining course items are blocked by prerequisites; SKL-000031 is the nearest prerequisite the learner can work on now; SKL-000031 mastery 0.56 (n=7); difficulty 1; no prerequisites

(real rows from `data/starter/tables/recommendation.csv`)

When nothing in a course is eligible, the engine walks the prerequisite graph and recommends the
blocking prerequisite (`REMEDIATE_PREREQUISITE`, 25 % of recommendations at full scale);
learners stuck in a loop are routed to an instructor (`ESCALATE_LOOP_NO_PROGRESS`).

## What it does

```
generate  ->  valid baseline: courses, skills, prerequisite graph, content, learners,
              learning paths, simulated attempts; derived results & path history
inject    ->  5 labelled edge cases as NEW rows (labels only in ground_truth/)
recommend ->  decisions on 5 dates: top-3 items with reason code + rationale,
              remediation through the prerequisite graph, exceptions for instructors
export    ->  CSV (+ JSONL + SQL seed for small scales), schema.sql, manifest with sha256
validate  ->  reads the files back: schema, PK/FK, enums, dates, lifecycle transitions,
              re-derivation of every derived value, prerequisite compliance, loops
evaluate  ->  temporal split (2026-07-01): mastery AUC/Brier/calibration vs baselines,
              offline outcome comparison, precision/recall vs ground truth
report    ->  reports/<scale>/report.html and explorer.html
```

How the acceptance criteria are met:

| Criterion | Where |
|---|---|
| Prerequisites are respected | hard eligibility gate in `recommend.py`; every recommendation re-checked by `validate.py`; test `test_engine_recommendations_respect_prerequisites` |
| Mastery calculation is reproducible | deterministic formula in `mastery.py`; every stored snapshot recomputed by the validator (tolerance 1e-6); test `test_mastery_is_reproducible` |
| Temporal test split is used | `evaluate.mastery_eval` uses only evidence before the cutoff, with a leakage assertion; recommendations evaluated on test-period dates only |
| Recommendations have reason codes | 9 reason codes incl. 3 exception codes routed to an instructor; validator check + test |

## Commands

| Command | What it does |
|---|---|
| `python -m skillpath run --scale tiny\|starter\|full [--seed N] [--with-reproduce]` | whole pipeline into `data/<scale>/` and `reports/<scale>/` |
| `python -m skillpath reproduce --scale starter` | runs twice with the same seed (byte-identical) and once with seed+1 (differs) |
| `python -m skillpath validate --data data/starter` | validates an existing dataset folder |
| `python -m skillpath load-sqlite --data data/starter` | loads the CSVs into SQLite with foreign keys ON |
| `python -m skillpath explore --data data/starter` | rebuilds the learner explorer page |
| `python -m skillpath docs` | regenerates `docs/DATA_DICTIONARY.md` from `schema.py` |

Scales (same generator, only the numbers change — `skillpath/config.py`):

| Scale | Learners | Skills | Content | Attempts | Anomalies per type | Runtime |
|---|---|---|---|---|---|---|
| tiny | 150 | 50 | 200 | 4,000 | 5 | ~2 s |
| starter | 1,000 | 100 | 400 | 40,000 | 10 | ~10 s |
| full | 10,000 | 500 | 2,000 | 500,000 | 50 | ~2 min |

Fixed seed: `20261022`.

## Repository layout

```
skillpath/            source code
  config.py           every threshold and business rule in one place
  schema.py           12 tables: types, enums, FKs, rules  (single source of truth)
  generate.py         seeded synthetic data + derived records
  mastery.py          mastery estimate (strictly before as_of)
  lifecycle.py        path status history, account status (derived)
  recommend.py        the recommender
  inject.py           labelled edge cases
  validate.py         integrity checks + precision/recall
  evaluate.py         metrics
  explore.py          learner explorer data (+ explorer_template.html)
  io.py / report.py / pipeline.py / cli.py
tests/                pytest suite
data/sample/          tiny scale, every format (CSV, JSONL, seed.sql, schema.sql, ground truth)
data/starter/         starter scale CSVs + ground truth + manifest
reports/<scale>/      report.html, explorer.html, metrics.json, validation_summary.json,
                      issues_detected.csv, detection_vs_ground_truth.csv, reproducibility.json
docs/DESIGN.md        decisions, trade-offs, limitations
docs/DATA_DICTIONARY.md  generated field-level dictionary
docs/AI_USAGE.md      how AI was used
prompts/PROMPT_HISTORY.md
```

The full-scale dataset (~150 MB) is not committed; regenerate it with
`python -m skillpath run --scale full`. Its reports are committed in `reports/full/`.

## Data provenance

All data is synthetic, generated by this repository from a fixed seed. No external,
personal or confidential data was used (`data/*/manifest.json` records this, together
with the generator command, version, row counts and sha256 of every file).

## Requirements

Python 3.10+, numpy, pandas (tested with Python 3.13, numpy 2.5, pandas 3.0), pytest for tests.

## Design and limitations

See [docs/DESIGN.md](docs/DESIGN.md). In short: a transparent rules engine was chosen
over a trained model because the acceptance criteria are about guarantees,
traceability and reproducibility; the main limitation is that recommendation impact
is measured offline (learners in the history never saw the recommendations).
