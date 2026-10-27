"""Validator: reads the exported tables and proves (or disproves) their integrity.

Three phases
  1. Schema checks generated from schema.py: required, pattern, enum, range,
     uniqueness, primary key, foreign key.
  2. Quarantine: rows that are structurally broken (orphan mappings, attempts
     with impossible timestamps) are set aside so they cannot poison step 3.
  3. Business-rule checks on the clean data: lifecycle transitions, derived
     values re-computed from source records (assessment results, path history,
     account status, mastery, outcomes), prerequisite compliance of every
     recommendation, and loop detection.

Every failing record is written to issues_detected.csv with an issue_type.
"""
from __future__ import annotations

import re
from collections import defaultdict

import numpy as np
import pandas as pd

from . import config as C
from . import generate
from . import lifecycle
from . import mastery as M
from . import recommend as R
from .schema import (ASSESSED_FORMATS, EXCEPTION_REASONS, INJECTED_ISSUE_TYPES, PATH_TRANSITIONS,
                     TABLES)


class Report:
    def __init__(self):
        self.checks: list[dict] = []
        self.issues: list[tuple] = []

    def add(self, check: str, category: str, table: str, issue_type: str, checked: int,
            failing: pd.DataFrame | list, detail: str = ""):
        """`failing`: list of record ids, or a DataFrame with record_id (+ optional detail)."""
        if isinstance(failing, pd.DataFrame):
            recs = list(failing["record_id"])
            details = list(failing["detail"]) if "detail" in failing else [detail] * len(recs)
        else:
            recs, details = list(failing), [detail] * len(failing)
        self.checks.append(dict(check=check, category=category, table=table, issue_type=issue_type,
                                rows_checked=int(checked), failures=len(recs),
                                status="PASS" if not recs else "FAIL"))
        self.issues.extend((check, issue_type, table, r, d) for r, d in zip(recs, details))

    def issues_frame(self) -> pd.DataFrame:
        df = pd.DataFrame(self.issues, columns=["check", "issue_type", "table", "record_id", "detail"])
        return df.drop_duplicates(["issue_type", "table", "record_id"]).reset_index(drop=True)


def _rid(df: pd.DataFrame, table: str) -> pd.Series:
    pk = list(TABLES[table].primary_key)
    if len(pk) == 1:
        return df[pk[0]].astype(str)
    if df.empty:
        return pd.Series([], dtype=object)
    return pd.Series(["|".join(map(str, row)) for row in df[pk].itertuples(index=False)], index=df.index)


# =================================================================== phase 1
def schema_checks(tables: dict, rep: Report) -> None:
    for name, t in TABLES.items():
        df = tables[name]
        rid = _rid(df, name)
        n = len(df)
        for f in t.fields:
            col = df[f.name]
            if f.required:
                rep.add(f"{name}.{f.name}.required", "schema", name, "REQUIRED_FIELD_MISSING", n,
                        list(rid[col.isna()]))
            present = col.notna()
            if f.pattern:
                bad = present & ~col.astype(str).str.match(f.pattern)
                rep.add(f"{name}.{f.name}.pattern", "schema", name, "ID_FORMAT_INVALID", n, list(rid[bad]))
            if f.allowed:
                bad = present & ~col.isin(f.allowed)
                rep.add(f"{name}.{f.name}.allowed", "schema", name, "ENUM_VALUE_INVALID", n, list(rid[bad]))
            if f.min is not None or f.max is not None:
                v = pd.to_numeric(col, errors="coerce")
                bad = present & (((v < f.min) if f.min is not None else False) |
                                 ((v > f.max) if f.max is not None else False))
                rep.add(f"{name}.{f.name}.range", "schema", name, "VALUE_OUT_OF_RANGE", n, list(rid[bad]))
            if f.unique:
                bad = present & col.duplicated(keep=False)
                rep.add(f"{name}.{f.name}.unique", "schema", name, "DUPLICATE_KEY", n, list(rid[bad]))
            if f.fk:
                ptable, pcol = f.fk.split(".")
                bad = present & ~col.isin(set(tables[ptable][pcol]))
                issue = ("CONTENT_MAPPED_TO_MISSING_SKILL" if (name, f.name) == ("content_skill", "skill_id")
                         else "FOREIGN_KEY_VIOLATION")
                rep.add(f"{name}.{f.name}.fk->{f.fk}", "foreign_key", name, issue, n, list(rid[bad]))
        bad = df.duplicated(list(t.primary_key), keep=False)
        rep.add(f"{name}.primary_key", "primary_key", name, "DUPLICATE_KEY", n, list(rid[bad]))


# =================================================================== phase 2
def attempt_time_checks(tables: dict, rep: Report) -> set:
    a = tables["attempt"]
    signup = a["learner_id"].map(dict(zip(tables["learner"]["learner_id"], tables["learner"]["signup_at"])))
    created = a["path_id"].map(dict(zip(tables["learning_path"]["path_id"], tables["learning_path"]["created_at"])))
    n = len(a)
    issue = "ATTEMPT_TIMESTAMP_ORDER_INVALID"
    bad1 = a["completed_at"].notna() & (a["completed_at"] < a["started_at"])
    bad2 = a["started_at"] < signup
    bad3 = a["started_at"] < created
    # attempt counter must follow the learner's chronology
    o = a.assign(k=a["attempt_id"].str[-6:].astype(int)).sort_values(["learner_id", "k"])
    prev_start = o.groupby("learner_id")["started_at"].shift()
    bad4_ids = set(o.loc[prev_start.notna() & (o["started_at"] < prev_start), "attempt_id"])
    rep.add("attempt.completed_after_started", "temporal", "attempt", issue, n, list(a.loc[bad1, "attempt_id"]),
            "completed_at < started_at")
    rep.add("attempt.after_signup", "temporal", "attempt", issue, n, list(a.loc[bad2, "attempt_id"]),
            "started_at < learner.signup_at")
    rep.add("attempt.after_path_created", "temporal", "attempt", issue, n, list(a.loc[bad3, "attempt_id"]),
            "started_at < path.created_at")
    rep.add("attempt.counter_follows_time", "temporal", "attempt", issue, n, sorted(bad4_ids),
            "started before the learner's previous attempt")
    return set(a.loc[bad1 | bad2 | bad3, "attempt_id"]) | bad4_ids


def quarantine(tables: dict, rep: Report) -> tuple[dict, dict]:
    bad_attempts = attempt_time_checks(tables, rep)
    clean = dict(tables)
    skills = set(tables["skill"]["skill_id"])
    clean["content_skill"] = tables["content_skill"][tables["content_skill"]["skill_id"].isin(skills)]
    clean["attempt"] = tables["attempt"][~tables["attempt"]["attempt_id"].isin(bad_attempts)]
    clean["assessment_result"] = tables["assessment_result"][
        ~tables["assessment_result"]["attempt_id"].isin(bad_attempts)]
    counts = {
        "content_skill": len(tables["content_skill"]) - len(clean["content_skill"]),
        "attempt": len(tables["attempt"]) - len(clean["attempt"]),
        "assessment_result": len(tables["assessment_result"]) - len(clean["assessment_result"]),
    }
    return clean, counts


# =================================================================== phase 3
def catalog_checks(t: dict, rep: Report) -> None:
    c = t["content_item"]
    n = len(c)
    bad = ((c["status"] == "DRAFT") != c["published_at"].isna()) | \
          ((c["status"] == "RETIRED") != c["retired_at"].notna()) | \
          (c["retired_at"].notna() & (c["retired_at"] <= c["published_at"]))
    rep.add("content_item.lifecycle_dates", "lifecycle", "content_item", "CONTENT_LIFECYCLE_INVALID", n,
            list(c.loc[bad, "content_id"]))
    bad = c["is_assessed"] != c["format"].isin(ASSESSED_FORMATS)
    rep.add("content_item.is_assessed_matches_format", "derived", "content_item", "DERIVED_VALUE_MISMATCH", n,
            list(c.loc[bad, "content_id"]))

    cs = t["content_skill"]
    prim = cs[cs["role"] == "PRIMARY"].groupby("content_id").size()
    n_prim = c["content_id"].map(prim).fillna(0)
    rep.add("content_skill.exactly_one_primary", "relationship", "content_item", "CONTENT_PRIMARY_SKILL_INVALID",
            n, list(c.loc[n_prim != 1, "content_id"]))
    bad = cs["weight"] != np.where(cs["role"] == "PRIMARY", 1.0, 0.5)
    rep.add("content_skill.weight_matches_role", "derived", "content_skill", "DERIVED_VALUE_MISMATCH", len(cs),
            list(_rid(cs[bad], "content_skill")))

    pre = t["skill_prerequisite"]
    course = dict(zip(t["skill"]["skill_id"], t["skill"]["course_id"]))
    bad = (pre["skill_id"] == pre["prerequisite_skill_id"]) | \
          (pre["is_cross_course"] != (pre["skill_id"].map(course) != pre["prerequisite_skill_id"].map(course)))
    rep.add("skill_prerequisite.no_self_loop_and_cross_flag", "relationship", "skill_prerequisite",
            "PREREQ_GRAPH_INVALID", len(pre), list(_rid(pre[bad], "skill_prerequisite")))
    rep.add("skill_prerequisite.acyclic", "relationship", "skill_prerequisite", "PREREQ_GRAPH_INVALID",
            len(pre), _cycle_nodes(pre), "skill is on a prerequisite cycle")


def _cycle_nodes(pre: pd.DataFrame) -> list:
    """Kahn's algorithm: whatever cannot be topologically sorted is on (or behind) a cycle."""
    nodes = set(pre["skill_id"]) | set(pre["prerequisite_skill_id"])
    indeg = {n: 0 for n in nodes}
    out = defaultdict(list)
    for s, p in zip(pre["skill_id"], pre["prerequisite_skill_id"]):
        out[p].append(s)
        indeg[s] += 1
    queue = [n for n, d in indeg.items() if d == 0]
    seen = 0
    while queue:
        n = queue.pop()
        seen += 1
        for m in out[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)
    return sorted(n for n, d in indeg.items() if d > 0)


def attempt_checks(t: dict, rep: Report) -> None:
    a = t["attempt"]
    n = len(a)
    assessed = a["content_id"].map(dict(zip(t["content_item"]["content_id"], t["content_item"]["is_assessed"])))
    bad = ((a["status"] == "ABANDONED") != a["completed_at"].isna()) | \
          (a["score"].notna() != ((a["status"] == "COMPLETED") & assessed))
    rep.add("attempt.status_score_consistency", "lifecycle", "attempt", "ATTEMPT_STATUS_INCONSISTENT", n,
            list(a.loc[bad, "attempt_id"]))
    paths = t["learning_path"].set_index("path_id")
    c_course = a["content_id"].map(dict(zip(t["content_item"]["content_id"], t["content_item"]["course_id"])))
    bad = (a["path_id"].map(paths["course_id"]) != c_course) | (a["path_id"].map(paths["learner_id"]) != a["learner_id"])
    rep.add("attempt.matches_path_course_and_learner", "relationship", "attempt", "ATTEMPT_PATH_MISMATCH", n,
            list(a.loc[bad, "attempt_id"]))
    ctx = R.Context(t)
    bad = [aid for aid, c, s in zip(a["attempt_id"], a["content_id"], a["started_at"])
           if not ctx.available(c, s.value)]
    rep.add("attempt.content_available_when_started", "temporal", "attempt", "ATTEMPT_CONTENT_UNAVAILABLE", n, bad)
    s = a.sort_values(["learner_id", "content_id", "started_at", "attempt_id"])
    expected = s.groupby(["learner_id", "content_id"]).cumcount() + 1
    rep.add("attempt.attempt_no_sequence", "derived", "attempt", "DERIVED_VALUE_MISMATCH", n,
            list(s.loc[s["attempt_no"] != expected, "attempt_id"]))
    dup = a.duplicated(["learner_id", "content_id", "started_at"], keep="first")
    rep.add("attempt.no_duplicates", "duplicate", "attempt", "DUPLICATE_RECORD", n, list(a.loc[dup, "attempt_id"]))


def assessment_checks(t: dict, rep: Report) -> None:
    stored = t["assessment_result"]
    expected = generate.derive_assessment_results(t["attempt"], t["content_skill"])
    m = stored.merge(expected, on="result_id", how="outer", suffixes=("", "_exp"), indicator=True)
    cols = ["attempt_id", "learner_id", "skill_id", "skill_weight", "score", "passed", "assessed_at"]
    diff = m["_merge"] != "both"
    for c in cols:
        diff |= (m[c].astype(object) != m[f"{c}_exp"].astype(object)) & (m["_merge"] == "both")
    rep.add("assessment_result.reconciles_with_attempts", "reconciliation", "assessment_result",
            "DERIVED_VALUE_MISMATCH", len(stored), list(m.loc[diff, "result_id"]),
            "result differs from (or missing vs) re-derivation from attempt + content_skill")


def path_checks(t: dict, rep: Report) -> None:
    p, ev = t["learning_path"], t["learning_path_event"]
    n = len(p)
    signup = p["learner_id"].map(dict(zip(t["learner"]["learner_id"], t["learner"]["signup_at"])))
    rep.add("learning_path.created_after_signup", "temporal", "learning_path", "PATH_BEFORE_SIGNUP", n,
            list(p.loc[p["created_at"] < signup, "path_id"]))
    dup = p.duplicated(["learner_id", "course_id"], keep=False)
    rep.add("learning_path.one_per_learner_course", "duplicate", "learning_path", "DUPLICATE_RECORD", n,
            list(p.loc[dup, "path_id"]))
    trans = list(zip(ev["from_status"].where(ev["from_status"].notna(), None), ev["to_status"]))
    bad = [e for e, tr in zip(ev["event_id"], trans) if tr not in PATH_TRANSITIONS]
    rep.add("learning_path_event.allowed_transition", "lifecycle", "learning_path_event",
            "PATH_LIFECYCLE_INVALID", len(ev), bad)
    # history re-derived from attempts must match exactly (no overwritten history)
    derived = lifecycle.derive_path_events(p[["path_id", "created_at", "course_id"]], t["attempt"],
                                           t["assessment_result"], t["skill"])
    key = ["path_id", "to_status", "event_at", "reason_code"]
    a = ev[key].assign(n=ev.groupby("path_id").cumcount())
    b = derived[key].assign(n=derived.groupby("path_id").cumcount())
    m = a.merge(b, on=["path_id", "n"], how="outer", suffixes=("", "_exp"))
    diff = ((m["to_status"] != m["to_status_exp"]) | (m["event_at"] != m["event_at_exp"]) |
            (m["reason_code"] != m["reason_code_exp"]))
    rep.add("learning_path_event.reconciles_with_attempts", "reconciliation", "learning_path",
            "PATH_LIFECYCLE_MISMATCH", n, sorted(set(m.loc[diff, "path_id"])))
    state = lifecycle.path_state(ev)
    j = p.join(state, on="path_id", rsuffix="_exp")
    bad = (j["current_status"] != j["current_status_exp"]) | (j["status_updated_at"] != j["status_updated_at_exp"]) | \
          ~((j["completed_at"] == j["completed_at_exp"]) | (j["completed_at"].isna() & j["completed_at_exp"].isna()))
    rep.add("learning_path.current_status_is_latest_event", "reconciliation", "learning_path",
            "PATH_LIFECYCLE_MISMATCH", n, list(j.loc[bad, "path_id"]))
    lr = t["learner"]
    bad = lr["account_status"] != lifecycle.account_status(lr, t["attempt"])
    rep.add("learner.account_status_derived", "reconciliation", "learner", "DERIVED_VALUE_MISMATCH", len(lr),
            list(lr.loc[bad, "learner_id"]))


def loop_checks(t: dict, rep: Report) -> None:
    a = t["attempt"]
    a = a[a["status"] == "COMPLETED"].sort_values(["path_id", "started_at", "attempt_id"])
    flagged = []
    for pid, g in a.groupby("path_id", sort=True):
        if len(g) < C.LOOP_WINDOW:
            continue
        contents = list(g["content_id"])
        scores = [None if pd.isna(s) else int(s) for s in g["score"]]
        for i in range(len(g) - C.LOOP_WINDOW + 1):
            if R.is_loop(contents[i:i + C.LOOP_WINDOW], scores[i:i + C.LOOP_WINDOW]):
                flagged.append(pid)
                break
    rep.add("learning_path.no_loop_without_progress", "behaviour", "learning_path", "PATH_LOOP_NO_PROGRESS",
            t["learning_path"].shape[0], flagged, f"{C.LOOP_WINDOW} attempts on <= {C.LOOP_MAX_DISTINCT} items, no pass, no gain")


def mastery_checks(t: dict, rep: Report) -> None:
    s = t["mastery_snapshot"]
    n = len(s)
    future = s["last_evidence_at"].notna() & (s["last_evidence_at"] >= s["as_of"])
    rep.add("mastery_snapshot.evidence_before_as_of", "temporal", "mastery_snapshot",
            "MASTERY_USES_FUTURE_ATTEMPT", n, list(s.loc[future, "snapshot_id"]), "last_evidence_at >= as_of")
    dup = s.duplicated(["learner_id", "skill_id", "as_of"], keep=False)
    rep.add("mastery_snapshot.unique_learner_skill_as_of", "duplicate", "mastery_snapshot", "DUPLICATE_RECORD",
            n, list(s.loc[dup & ~future, "snapshot_id"]))
    mismatch = []
    for d, g in s[~future].groupby("as_of"):
        exp = M.compute_mastery(t["assessment_result"], d).set_index(M.KEYS)
        j = g.join(exp, on=M.KEYS, rsuffix="_exp")
        j["mastery_exp"] = j["mastery_exp"].fillna(C.MASTERY_PRIOR)
        j["evidence_count_exp"] = j["evidence_count_exp"].fillna(0)
        bad = ((j["mastery"] - j["mastery_exp"]).abs() > 1e-6) | (j["evidence_count"] != j["evidence_count_exp"])
        mismatch += list(j.loc[bad, "snapshot_id"])
    rep.add("mastery_snapshot.reproducible", "reconciliation", "mastery_snapshot", "MASTERY_RECOMPUTE_MISMATCH",
            int((~future).sum()), mismatch, "stored mastery != recomputation from assessment_result")


def recommendation_checks(t: dict, rep: Report) -> None:
    r = t["recommendation"]
    n = len(r)
    ctx = R.Context(t)
    is_exc = r["reason_code"].isin(EXCEPTION_REASONS)
    bad = (is_exc != r["content_id"].isna()) | (is_exc != (r["route_to"] == "INSTRUCTOR")) | \
          (r["rationale"].fillna("").str.len() < 10)
    rep.add("recommendation.reason_code_consistent", "business_rule", "recommendation", "REC_REASON_INCONSISTENT",
            n, list(r.loc[bad, "recommendation_id"]))
    paths = t["learning_path"].set_index("path_id")
    bad = r["path_id"].map(paths["learner_id"]) != r["learner_id"]
    rep.add("recommendation.learner_matches_path", "relationship", "recommendation", "REC_DECISION_INCONSISTENT",
            n, list(r.loc[bad, "recommendation_id"]))
    g = r.groupby("decision_id")
    bad = (g["learner_id"].transform("nunique") > 1) | (g["as_of"].transform("nunique") > 1) | \
          r.duplicated(["decision_id", "rank"], keep=False)
    rep.add("recommendation.decision_consistent", "relationship", "recommendation", "REC_DECISION_INCONSISTENT",
            n, list(r.loc[bad, "recommendation_id"]))
    snaps = t["mastery_snapshot"].set_index("snapshot_id")
    has = r["mastery_snapshot_id"].notna() & r["mastery_snapshot_id"].isin(snaps.index)
    sr = r[has]
    bad = (sr["mastery_snapshot_id"].map(snaps["learner_id"]) != sr["learner_id"]) | \
          (sr["mastery_snapshot_id"].map(snaps["skill_id"]) != sr["target_skill_id"]) | \
          (sr["mastery_snapshot_id"].map(snaps["as_of"]) != sr["as_of"])
    rep.add("recommendation.snapshot_matches", "relationship", "recommendation", "REC_DECISION_INCONSISTENT",
            int(has.sum()), list(sr.loc[bad, "recommendation_id"]))

    content = r[r["content_id"].notna()]
    viol, unavailable, wrong_course, details = [], [], [], []
    for d, grp in content.groupby("as_of"):
        look = M.to_lookup(M.compute_mastery(t["assessment_result"], d))
        tv = d.value
        for rid, l, c, pid, reason in zip(grp["recommendation_id"], grp["learner_id"], grp["content_id"],
                                          grp["path_id"], grp["reason_code"]):
            miss = ctx.missing(c, l, look, tv)
            if miss:
                viol.append(rid)
                details.append("; ".join(f"{p} {m:.2f}<{mm:.2f}" for p, m, mm in miss))
            if not ctx.available(c, tv):
                unavailable.append(rid)
            if reason != "REMEDIATE_PREREQUISITE" and ctx.course_of.get(c) != paths.at[pid, "course_id"]:
                wrong_course.append(rid)
    rep.add("recommendation.prerequisites_respected", "business_rule", "recommendation",
            "REC_VIOLATES_PREREQUISITE", len(content),
            pd.DataFrame({"record_id": viol, "detail": details}))
    rep.add("recommendation.content_available", "business_rule", "recommendation", "REC_CONTENT_UNAVAILABLE",
            len(content), unavailable)
    rep.add("recommendation.content_in_path_course", "business_rule", "recommendation", "REC_COURSE_MISMATCH",
            len(content), wrong_course)
    exp = R.fill_outcomes(r, t["attempt"])
    bad = (exp["outcome"] != r["outcome"]) | \
          ~((exp["outcome_attempt_id"] == r["outcome_attempt_id"]) |
            (exp["outcome_attempt_id"].isna() & r["outcome_attempt_id"].isna()))
    rep.add("recommendation.outcome_reconciles", "reconciliation", "recommendation", "DERIVED_VALUE_MISMATCH", n,
            list(r.loc[bad, "recommendation_id"]))


# =================================================================== entry point
def validate(tables: dict) -> tuple[pd.DataFrame, dict]:
    rep = Report()
    schema_checks(tables, rep)
    clean, quarantined = quarantine(tables, rep)
    catalog_checks(clean, rep)
    attempt_checks(clean, rep)
    assessment_checks(clean, rep)
    path_checks(clean, rep)
    mastery_checks(clean, rep)
    recommendation_checks(clean, rep)
    loop_checks(clean, rep)

    issues = rep.issues_frame()
    by_type = issues.groupby("issue_type").size().to_dict()
    summary = {
        "rows": {k: int(len(v)) for k, v in tables.items()},
        "checks_run": len(rep.checks),
        "checks_failed": sum(c["status"] == "FAIL" for c in rep.checks),
        "quarantined": quarantined,
        "issues_by_type": {k: int(v) for k, v in sorted(by_type.items())},
        "unexpected_issue_types": sorted(k for k in by_type if k not in INJECTED_ISSUE_TYPES),
        "checks": rep.checks,
    }
    return issues, summary


def compare_with_ground_truth(issues: pd.DataFrame, gt: pd.DataFrame) -> pd.DataFrame:
    """Precision / recall of the validator against the injected labels, per issue type."""
    det = set(zip(issues["issue_type"], issues["record_id"]))
    rows = []
    for it in INJECTED_ISSUE_TYPES:
        truth = set(gt.loc[gt["issue_type"] == it, "record_id"])
        found = {r for t_, r in det if t_ == it}
        tp = len(truth & found)
        rows.append(dict(issue_type=it, injected=len(truth), detected=len(found), true_positive=tp,
                         false_positive=len(found - truth), false_negative=len(truth - found),
                         precision=round(tp / len(found), 4) if found else None,
                         recall=round(tp / len(truth), 4) if truth else None))
    return pd.DataFrame(rows)
