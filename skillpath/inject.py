"""Inject labelled edge cases AFTER the valid baseline exists.

Every anomaly is added as NEW rows with their own IDs (we never silently
corrupt a valid baseline row). The label for each one goes to
ground_truth/anomalies.csv only, never into an input table.

    REC_VIOLATES_PREREQUISITE        recommendation of an item whose prerequisites are not met
    ATTEMPT_TIMESTAMP_ORDER_INVALID  completed before started / started before signup
    CONTENT_MAPPED_TO_MISSING_SKILL  content_skill row pointing to a skill that does not exist
    MASTERY_USES_FUTURE_ATTEMPT      mastery snapshot computed with evidence from after as_of
    PATH_LOOP_NO_PROGRESS            8 attempts cycling over 2 items, no pass, no score gain
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C
from . import ids
from . import lifecycle
from . import mastery as M
from . import recommend as R
from .generate import _rng, derive_assessment_results

# on-demand decisions (e.g. learner pressed "what next?") used for injected bad recommendations
AD_HOC_DATES = (pd.Timestamp("2026-02-15 09:00"), pd.Timestamp("2026-05-15 09:00"),
                pd.Timestamp("2026-08-14 09:00"))
EXPECTED_ACTION = {
    "REC_VIOLATES_PREREQUISITE": "REJECT_RECOMMENDATION",
    "ATTEMPT_TIMESTAMP_ORDER_INVALID": "QUARANTINE_ATTEMPT",
    "CONTENT_MAPPED_TO_MISSING_SKILL": "QUARANTINE_MAPPING",
    "MASTERY_USES_FUTURE_ATTEMPT": "RECOMPUTE_SNAPSHOT",
    "PATH_LOOP_NO_PROGRESS": "ESCALATE_TO_INSTRUCTOR",
}


class Labels:
    """Collects ground-truth labels; written to ground_truth/anomalies.csv only."""

    def __init__(self):
        self.rows: list[dict] = []

    def __call__(self, issue, table, record_id, detail):
        self.rows.append(dict(issue_type=issue, table=table, record_id=record_id,
                              expected_action=EXPECTED_ACTION[issue], detail=detail))

    def frame(self) -> pd.DataFrame:
        gt = pd.DataFrame(self.rows)
        gt.insert(0, "anomaly_id", [ids.seq("ANM", i + 1) for i in range(len(gt))])
        return gt


def inject_behaviour(tables: dict, scale: C.Scale, seed: int, label: Labels) -> dict:
    """Stage A (before decisions): learners looping without progress.

    This is genuine (if unhealthy) learner behaviour, so the recommender sees it
    and must escalate it; derived state is refreshed afterwards.
    """
    tables = dict(tables)
    _loops(tables, _rng(seed, 21), scale.anomalies_per_type, label)
    return tables


def inject_data_errors(tables: dict, scale: C.Scale, seed: int, label: Labels) -> dict:
    """Stage B: corrupt source records (bad timestamps, orphan skill mappings)."""
    tables = dict(tables)
    _bad_timestamps(tables, _rng(seed, 22), scale.anomalies_per_type, label)
    _orphan_mappings(tables, _rng(seed, 23), scale.anomalies_per_type, label)
    return tables


def inject_decision_errors(tables: dict, scale: C.Scale, seed: int, label: Labels) -> dict:
    """Stage C (after decisions): a bad recommendation log and leaky mastery snapshots."""
    tables = dict(tables)
    _bad_recommendations(tables, _rng(seed, 24), scale.anomalies_per_type, label)
    _future_mastery(tables, _rng(seed, 25), scale.anomalies_per_type, label)
    return tables


def _next_seq(df: pd.DataFrame, col: str) -> int:
    return max(ids.number(x) for x in df[col]) + 1


# --------------------------------------------------------------------------- 1
def _bad_recommendations(tables, rng, n, label):
    ctx = R.Context(tables)
    hist = R.History(tables["attempt"])
    paths = tables["learning_path"]
    info = dict(zip(paths["path_id"], zip(paths["learner_id"], paths["course_id"])))
    recs = tables["recommendation"]
    next_rec, next_dec = _next_seq(recs, "recommendation_id"), _next_seq(recs, "decision_id")
    rows = []
    for k in range(n):
        d = AD_HOC_DATES[k % len(AD_HOC_DATES)]
        t = d.value
        look = M.to_lookup(M.compute_mastery(tables["assessment_result"], d))
        status = lifecycle.status_as_of(tables["learning_path_event"], d)
        open_paths = sorted(status.index[status.isin(("ACTIVE",))])
        for pid in rng.permutation(open_paths):
            learner, course = info[pid]
            if any(r["path_id"] == pid for r in rows):
                continue
            done = {r.content for r in hist.before(learner, t) if r.status == "COMPLETED"}
            bad = [c for c in ctx.course_items.get(course, ())
                   if c not in done and ctx.available(c, t) and ctx.missing(c, learner, look, t)]
            if not bad:
                continue
            c = bad[0]
            miss = ctx.missing(c, learner, look, t)
            s = ctx.primary[c]
            m = M.get(look, learner, s)[0]
            o = R.outcome(ctx, hist, learner, c, t)
            rows.append(dict(
                recommendation_id=ids.seq("REC", next_rec), decision_id=ids.seq("DEC", next_dec),
                learner_id=learner, path_id=pid, as_of=d, rank=1, content_id=c, target_skill_id=s,
                mastery_snapshot_id=None, reason_code="NEXT_FRONTIER_SKILL",
                rationale=f"prerequisites met and skill not yet learned; {s} mastery {m:.2f}",
                predicted_success=M.predicted_success(m, ctx.difficulty[c]), route_to="LEARNER",
                engine_version=C.ENGINE_VERSION, outcome=o[0], outcome_attempt_id=o[1],
                outcome_at=pd.to_datetime(o[2])))
            label("REC_VIOLATES_PREREQUISITE", "recommendation", ids.seq("REC", next_rec),
                  f"{c} needs " + ", ".join(f"{p}>={mm:.2f} (has {mv:.2f})" for p, mv, mm in miss))
            next_rec += 1
            next_dec += 1
            break
    tables["recommendation"] = pd.concat([recs, pd.DataFrame(rows)], ignore_index=True)


# --------------------------------------------------------------------------- 2
def _bad_timestamps(tables, rng, n, label):
    att = tables["attempt"]
    content = tables["content_item"]
    paths = tables["learning_path"]
    signup = dict(zip(tables["learner"]["learner_id"], tables["learner"]["signup_at"]))
    counts = att.groupby("learner_id").size()
    first = att.groupby("learner_id")["started_at"].min()
    unassessed = content[(~content["is_assessed"]) & (content["status"] == "PUBLISHED")]
    by_course = {c: list(g["content_id"]) for c, g in unassessed.groupby("course_id")}
    tried = set(zip(att["learner_id"], att["content_id"]))
    rows = []
    learners = rng.permutation(sorted(counts.index))
    for learner in learners:
        if len(rows) == n:
            break
        mine = paths[paths["learner_id"] == learner]
        pid, course, created = mine.iloc[0][["path_id", "course_id", "created_at"]]
        options = [c for c in by_course.get(course, []) if (learner, c) not in tried]
        if not options:
            continue
        c = options[int(rng.integers(len(options)))]
        if len(rows) % 2 == 0:   # completed before it started
            start = first[learner] + pd.Timedelta(minutes=int(rng.integers(60, 60 * 24 * 30)))
            end = start - pd.Timedelta(minutes=int(rng.integers(5, 300)))
            why = "completed_at is before started_at"
        else:                    # started before the learner even signed up
            start = signup[learner] - pd.Timedelta(days=int(rng.integers(2, 40)))
            end = start + pd.Timedelta(minutes=15)
            why = "started_at is before learner signup and path creation"
        aid = ids.attempt(learner, int(counts[learner]) + 1)
        rows.append(dict(attempt_id=aid, learner_id=learner, content_id=c, path_id=pid, attempt_no=1,
                         status="COMPLETED", started_at=start, completed_at=end, score=pd.NA,
                         duration_min=15))
        label("ATTEMPT_TIMESTAMP_ORDER_INVALID", "attempt", aid, why)
    new = pd.DataFrame(rows)
    new["score"] = new["score"].astype("Int64")
    tables["attempt"] = pd.concat([att, new], ignore_index=True)


# --------------------------------------------------------------------------- 3
def _orphan_mappings(tables, rng, n, label):
    cs = tables["content_skill"]
    picks = rng.choice(sorted(tables["content_item"]["content_id"]), size=n, replace=False)
    rows = []
    for k, c in enumerate(sorted(picks)):
        s = ids.skill(900_001 + k)
        rows.append(dict(content_id=c, skill_id=s, role="SECONDARY", weight=0.5))
        label("CONTENT_MAPPED_TO_MISSING_SKILL", "content_skill", f"{c}|{s}", f"{s} does not exist in skill")
    tables["content_skill"] = pd.concat([cs, pd.DataFrame(rows)], ignore_index=True)


# --------------------------------------------------------------------------- 4
def _future_mastery(tables, rng, n, label):
    snaps = tables["mastery_snapshot"]
    existing = set(zip(snaps["learner_id"], snaps["skill_id"], snaps["as_of"]))
    end_view = M.compute_mastery(tables["assessment_result"], pd.Timestamp(C.WINDOW_END) + pd.Timedelta(minutes=1))
    nxt = _next_seq(snaps, "snapshot_id")
    rows = []
    order = rng.permutation(len(end_view))
    for i in order:
        if len(rows) == n:
            break
        r = end_view.iloc[i]
        # the latest decision date that is still BEFORE the newest evidence -> uses the future
        earlier = [pd.Timestamp(x) for x in C.DECISION_DATES if pd.Timestamp(x) < r["last_evidence_at"]]
        if not earlier:
            continue
        d = earlier[-1]
        if (r["learner_id"], r["skill_id"], d) in existing:
            continue
        sid = ids.seq("MSN", nxt)
        rows.append(dict(snapshot_id=sid, learner_id=r["learner_id"], skill_id=r["skill_id"], as_of=d,
                         mastery=float(r["mastery"]), evidence_count=int(r["evidence_count"]),
                         last_evidence_at=r["last_evidence_at"], method_version=C.MASTERY_METHOD))
        label("MASTERY_USES_FUTURE_ATTEMPT", "mastery_snapshot", sid,
              f"uses evidence up to {r['last_evidence_at']} for a decision at {d}")
        nxt += 1
    tables["mastery_snapshot"] = pd.concat([snaps, pd.DataFrame(rows)], ignore_index=True)


# --------------------------------------------------------------------------- 5
def _loops(tables, rng, n, label):
    att = tables["attempt"]
    ctx = R.Context(tables)
    paths = tables["learning_path"]
    last_any = att.groupby("learner_id")["started_at"].max()
    counts = att.groupby("learner_id").size()
    start = pd.Timestamp("2026-08-03 18:00")
    window = (pd.Timestamp("2026-06-01"), pd.Timestamp("2026-08-01"))
    cand = paths[paths["current_status"].isin(("ACTIVE", "STALLED"))
                 & (paths["created_at"] < start)
                 & paths["learner_id"].map(last_any).between(*window, inclusive="left")]
    cand = cand.drop_duplicates("learner_id")
    passed = set(zip(att.loc[att["score"] >= C.PASS_SCORE, "learner_id"],
                     att.loc[att["score"] >= C.PASS_SCORE, "content_id"]))
    tries = att.groupby(["learner_id", "content_id"]).size().to_dict()
    new_att, chosen = [], 0
    for idx in rng.permutation(len(cand)):
        if chosen == n:
            break
        pid, learner, course = cand.iloc[idx][["path_id", "learner_id", "course_id"]]
        items = [c for c in ctx.course_items.get(course, ())
                 if ctx.assessed[c] and ctx.available(c, start.value) and (learner, c) not in passed]
        if len(items) < 2:
            continue
        a, b = items[0], items[1]
        s0 = int(rng.integers(40, 60))
        k = int(counts[learner])
        aids = []
        for j in range(C.LOOP_WINDOW):
            c = a if j % 2 == 0 else b
            tries[(learner, c)] = tries.get((learner, c), 0) + 1
            score = s0 - j - int(rng.integers(0, 4))
            t = start + pd.Timedelta(hours=60 * j + int(rng.integers(0, 5)))
            k += 1
            aid = ids.attempt(learner, k)
            aids.append(aid)
            new_att.append(dict(attempt_id=aid, learner_id=learner, content_id=c, path_id=pid,
                                attempt_no=tries[(learner, c)], status="COMPLETED", started_at=t,
                                completed_at=t + pd.Timedelta(minutes=20), score=score, duration_min=20))
        label("PATH_LOOP_NO_PROGRESS", "learning_path", pid,
              f"{C.LOOP_WINDOW} attempts alternating {a}/{b}, scores falling from {s0}: {aids[0]}..{aids[-1]}")
        chosen += 1
    new = pd.DataFrame(new_att)
    new["score"] = new["score"].astype("Int64")
    tables["attempt"] = pd.concat([att, new], ignore_index=True)
    # their assessment results are derived exactly like the baseline ones
    res = derive_assessment_results(new, tables["content_skill"][tables["content_skill"]["skill_id"].isin(
        tables["skill"]["skill_id"])])
    tables["assessment_result"] = pd.concat([tables["assessment_result"], res], ignore_index=True)
