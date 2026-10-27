"""Central configuration: scale presets, time window and business-rule thresholds.

Every magic number used by the generator, the recommender and the validator
lives here, so a reviewer can see (and change) the rules in one place.
"""
from dataclasses import dataclass, asdict
from datetime import datetime

DEFAULT_SEED = 20261022
GENERATOR_VERSION = "1.0.0"
ENGINE_VERSION = "rules-v1"
MASTERY_METHOD = "recency-weighted-bayes-v1"

# ---------------------------------------------------------------- time window
WINDOW_START = datetime(2025, 1, 1)
WINDOW_END = datetime(2026, 9, 30, 23, 59)

# Temporal split: evidence strictly before the cutoff is "train",
# attempts on/after the cutoff are "test". Nothing from test is used to
# compute the mastery that is evaluated on test.
TEST_CUTOFF = datetime(2026, 7, 1)
TRAIN_DECISION_DATES = (datetime(2026, 1, 1), datetime(2026, 4, 1))
TEST_DECISION_DATES = (datetime(2026, 7, 1), datetime(2026, 8, 1), datetime(2026, 9, 1))
DECISION_DATES = TRAIN_DECISION_DATES + TEST_DECISION_DATES
OUTCOME_WINDOW_DAYS = 30

# ---------------------------------------------------------------- business rules
PASS_SCORE = 70                # an assessed attempt passes at score >= 70
PREREQ_MIN_MASTERY = 0.60      # a prerequisite counts as "met" at mastery >= 0.60
MASTERED_THRESHOLD = 0.70      # a skill counts as "mastered" at mastery >= 0.70
MASTERY_PRIOR = 0.30           # belief about a skill before any evidence
MASTERY_PRIOR_WEIGHT = 1.0     # how many "virtual attempts" the prior is worth
MASTERY_DECAY = 0.80           # each older piece of evidence counts 0.8x the next one
STALL_DAYS = 60                # no activity for 60 days -> path STALLED
WITHDRAW_AFTER_STALL_DAYS = 60 # stalled and still no activity 60 days later -> WITHDRAWN
DORMANT_DAYS = 90              # learner with no attempt in the last 90 days -> DORMANT
LOOP_WINDOW = 8                # loop = 8 consecutive attempts ...
LOOP_MAX_DISTINCT = 2          # ... on <= 2 distinct items, none passed, no score gain
TOP_K = 3                      # recommendations per decision
COLD_START_SHARE = 0.05        # share of learners who enrol but never attempt anything


@dataclass(frozen=True)
class Scale:
    name: str
    learners: int
    courses: int
    skills_per_course: int
    content_per_course: int
    attempts: int
    anomalies_per_type: int

    @property
    def skills(self) -> int:
        return self.courses * self.skills_per_course

    @property
    def content_items(self) -> int:
        return self.courses * self.content_per_course

    def as_dict(self) -> dict:
        d = asdict(self)
        d.update(skills=self.skills, content_items=self.content_items)
        return d


# The same generator produces every scale; only these numbers change.
SCALES = {
    "tiny": Scale("tiny", 150, 5, 10, 40, 4_000, 5),
    "starter": Scale("starter", 1_000, 10, 10, 40, 40_000, 10),
    "full": Scale("full", 10_000, 50, 10, 40, 500_000, 50),
}
