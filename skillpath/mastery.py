"""Mastery estimate: a recency-weighted Bayesian average of assessment scores.

    mastery(learner, skill, as_of) =
        (PRIOR * PRIOR_WEIGHT + sum_i w_i * score_i / 100) / (PRIOR_WEIGHT + sum_i w_i)

i runs over the learner's assessment results for that skill with
assessed_at < as_of (strictly earlier: a decision may never see evidence from
its own moment or later), newest first, and w_i = skill_weight_i * DECAY ** i.

Why this formula:
* deterministic and explainable (no trained model to justify),
* the prior keeps one lucky attempt from looking like full mastery,
* recency weighting lets a learner recover from early failures.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import config as C

KEYS = ["learner_id", "skill_id"]
COLUMNS = ["learner_id", "skill_id", "mastery", "evidence_count", "last_evidence_at"]


def compute_mastery(results: pd.DataFrame, as_of) -> pd.DataFrame:
    """Mastery for every (learner, skill) pair with evidence strictly before `as_of`."""
    as_of = pd.Timestamp(as_of)
    ev = results.loc[results["assessed_at"] < as_of,
                     ["learner_id", "skill_id", "assessed_at", "result_id", "skill_weight", "score"]]
    if ev.empty:
        return pd.DataFrame(columns=COLUMNS)
    ev = ev.sort_values(["learner_id", "skill_id", "assessed_at", "result_id"],
                        ascending=[True, True, False, False], kind="mergesort")
    rank = ev.groupby(KEYS, sort=False).cumcount().to_numpy()
    w = ev["skill_weight"].to_numpy(dtype=float) * np.power(C.MASTERY_DECAY, rank)
    tmp = pd.DataFrame({
        "learner_id": ev["learner_id"].to_numpy(),
        "skill_id": ev["skill_id"].to_numpy(),
        "w": w,
        "ws": w * ev["score"].to_numpy(dtype=float) / 100.0,
        "t": ev["assessed_at"].to_numpy(),
    })
    g = tmp.groupby(KEYS, sort=True).agg(
        w=("w", "sum"), ws=("ws", "sum"), evidence_count=("w", "size"), last_evidence_at=("t", "max"),
    ).reset_index()
    prior = C.MASTERY_PRIOR * C.MASTERY_PRIOR_WEIGHT
    g["mastery"] = ((prior + g["ws"]) / (C.MASTERY_PRIOR_WEIGHT + g["w"])).round(6)
    return g[COLUMNS]


def to_lookup(mastery: pd.DataFrame) -> dict:
    """{(learner_id, skill_id): (mastery, evidence_count, last_evidence_at)}"""
    return {
        (l, s): (m, n, t)
        for l, s, m, n, t in zip(mastery["learner_id"], mastery["skill_id"], mastery["mastery"],
                                 mastery["evidence_count"], mastery["last_evidence_at"])
    }


def get(lookup: dict, learner_id: str, skill_id: str) -> tuple:
    """(mastery, evidence_count, last_evidence_at); the prior if there is no evidence."""
    return lookup.get((learner_id, skill_id), (C.MASTERY_PRIOR, 0, None))


def predicted_success(mastery: float, difficulty: int) -> float:
    """Simple, hand-set pass-probability curve (not fitted on test data)."""
    z = 4.0 * (mastery - 0.45) - 0.55 * (difficulty - 2.5)
    return round(1.0 / (1.0 + math.exp(-z)), 4)
