"""Measurable outputs.

1. Data profile      - distributions, coverage of cold-start / repeats / gaps / formats.
2. Mastery (temporal split) - mastery is computed ONLY from evidence before
   TEST_CUTOFF and scored on attempts on/after it (AUC, Brier, calibration),
   compared with simpler baselines. Also checked against the hidden ground truth.
3. Recommendations   - on the test decision dates: prerequisite compliance,
   reason-code mix, exceptions, coverage, and an offline outcome analysis.
4. Anomaly detection - validator precision / recall per injected issue type.
5. One worked example - every lesson of one learner's course and why it is (not) recommended.

Ground truth (ground_truth/*.csv) is read here ONLY to score; it never feeds
the recommender or the mastery estimate.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C
from . import mastery as M
from . import recommend as R
from . import validate as V

NS_PER_DAY = 86_400 * 10**9


# ------------------------------------------------------------------ metrics helpers
def auc(y, score) -> float | None:
    y = np.asarray(y, dtype=bool)
    n1, n0 = int(y.sum()), int((~y).sum())
    if n1 == 0 or n0 == 0:
        return None
    ranks = pd.Series(score).rank(method="average").to_numpy()
    return round(float((ranks[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)), 4)


def brier(y, p) -> float:
    return round(float(np.mean((np.asarray(p, float) - np.asarray(y, float)) ** 2)), 4)


def spearman(a, b) -> float:
    return round(float(pd.Series(a).rank().corr(pd.Series(b).rank())), 4)


def calibration(y, p, bins=(0, .2, .4, .6, .8, 1.0001)) -> list:
    df = pd.DataFrame({"y": np.asarray(y, float), "p": np.asarray(p, float)})
    df["bin"] = pd.cut(df["p"], bins=bins, right=False)
    g = df.groupby("bin", observed=True).agg(n=("y", "size"), predicted=("p", "mean"), actual=("y", "mean"))
    return [dict(bin=str(i), n=int(r.n), predicted=round(r.predicted, 3), actual=round(r.actual, 3))
            for i, r in g.iterrows()]


def _pct(x) -> float:
    return round(float(x) * 100, 2)


# ------------------------------------------------------------------ 1. data profile
def data_profile(t: dict, truth: pd.DataFrame) -> dict:
    a, content = t["attempt"], t["content_item"]
    per_learner = t["learner"]["learner_id"].map(a.groupby("learner_id").size()).fillna(0)
    scored = a[a["score"].notna()]
    ctx = R.Context(t)
    # prerequisite gap = at attempt time the learner had never passed a required prerequisite
    first_pass = (t["assessment_result"][t["assessment_result"]["passed"]]
                  .groupby(["learner_id", "skill_id"])["assessed_at"].min())
    req = pd.DataFrame([(c, p) for c, lst in ctx.required.items() for p, _, _ in lst],
                       columns=["content_id", "prereq"])
    x = scored[["attempt_id", "learner_id", "content_id", "started_at", "score"]].merge(req, on="content_id")
    x["first_pass"] = pd.MultiIndex.from_arrays([x["learner_id"], x["prereq"]]).map(first_pass)
    x["gap"] = x["first_pass"].isna() | (x["first_pass"] >= x["started_at"])
    gap_ids = set(x.loc[x["gap"], "attempt_id"])
    has_req = set(x["attempt_id"])
    s_req = scored[scored["attempt_id"].isin(has_req)]
    in_gap = s_req["attempt_id"].isin(gap_ids)
    return {
        "learners": int(len(t["learner"])),
        "cold_start_learners_no_attempts": int((per_learner == 0).sum()),
        "attempts": int(len(a)),
        "attempts_per_learner_percentiles": {f"p{q}": float(np.percentile(per_learner, q)) for q in (10, 50, 90, 99)},
        "repeated_attempt_share_pct": _pct((a["attempt_no"] > 1).mean()),
        "abandoned_share_pct": _pct((a["status"] == "ABANDONED").mean()),
        "pass_rate_pct": _pct((scored["score"] >= C.PASS_SCORE).mean()),
        "score_percentiles": {f"p{q}": float(np.percentile(scored["score"].astype(float), q)) for q in (10, 25, 50, 75, 90)},
        "attempts_by_format": a["content_id"].map(dict(zip(content["content_id"], content["format"]))).value_counts().to_dict(),
        "content_by_status": content["status"].value_counts().to_dict(),
        "pass_rate_by_difficulty_pct": {int(k): _pct(v) for k, v in
                                        (scored.assign(d=scored["content_id"].map(ctx.difficulty))
                                         .groupby("d")["score"].apply(lambda s: (s >= C.PASS_SCORE).mean())).items()},
        "prerequisite_gap": {
            "scored_attempts_on_items_with_prerequisites": int(len(s_req)),
            "attempted_with_unpassed_prerequisite_pct": _pct(in_gap.mean()) if len(s_req) else None,
            "pass_rate_with_gap_pct": _pct((s_req.loc[in_gap, "score"] >= C.PASS_SCORE).mean()) if in_gap.any() else None,
            "pass_rate_without_gap_pct": _pct((s_req.loc[~in_gap, "score"] >= C.PASS_SCORE).mean()) if (~in_gap).any() else None,
        },
        "path_status": t["learning_path"]["current_status"].value_counts().to_dict(),
        "learner_segments": truth["segment"].value_counts().to_dict(),
    }


# ------------------------------------------------------------------ 2. mastery on a temporal split
def mastery_eval(t: dict, latent: pd.DataFrame) -> dict:
    cutoff = pd.Timestamp(C.TEST_CUTOFF)
    res = t["assessment_result"]
    train = M.compute_mastery(res, cutoff)
    leak = train["last_evidence_at"].max()
    assert pd.isna(leak) or leak < cutoff, "mastery used evidence from the test period"
    look = M.to_lookup(train)
    ctx = R.Context(t)
    a = t["attempt"]
    test = a[(a["started_at"] >= cutoff) & a["score"].notna()].copy()
    test["skill"] = test["content_id"].map(ctx.primary)
    test["difficulty"] = test["content_id"].map(ctx.difficulty)
    test["mastery"] = [M.get(look, l, s)[0] for l, s in zip(test["learner_id"], test["skill"])]
    test["evidence"] = [M.get(look, l, s)[1] for l, s in zip(test["learner_id"], test["skill"])]
    test["p_model"] = [M.predicted_success(m, d) for m, d in zip(test["mastery"], test["difficulty"])]
    test["p_difficulty_only"] = [M.predicted_success(C.MASTERY_PRIOR, d) for d in test["difficulty"]]
    train_att = a[(a["started_at"] < cutoff) & a["score"].notna()]
    avg = (train_att.groupby("learner_id")["score"].mean() / 100.0)
    test["p_learner_avg"] = test["learner_id"].map(avg).fillna(0.5).astype(float)
    base_rate = float((train_att["score"] >= C.PASS_SCORE).mean())
    y = (test["score"] >= C.PASS_SCORE).to_numpy()
    warm = test["evidence"].to_numpy() > 0

    out = {
        "cutoff": cutoff.isoformat(),
        "train_evidence_rows": int((res["assessed_at"] < cutoff).sum()),
        "test_attempts": int(len(test)),
        "test_attempts_with_prior_skill_evidence": int(warm.sum()),
        "leakage_check": "max evidence time used < cutoff: OK",
        "models": {
            "mastery_model (ours)": {"auc": auc(y, test["p_model"]), "brier": brier(y, test["p_model"])},
            "difficulty_only": {"auc": auc(y, test["p_difficulty_only"]), "brier": brier(y, test["p_difficulty_only"])},
            "learner_average_score": {"auc": auc(y, test["p_learner_avg"]), "brier": brier(y, test["p_learner_avg"])},
            "global_pass_rate": {"auc": 0.5, "brier": brier(y, np.full(len(y), base_rate))},
        },
        "auc_warm_vs_cold": {
            "with_prior_evidence_on_skill": auc(y[warm], test.loc[warm, "p_model"]),
            "no_prior_evidence_on_skill": auc(y[~warm], test.loc[~warm, "p_model"]),
        },
        "calibration": calibration(y, test["p_model"]),
    }
    end = M.compute_mastery(res, pd.Timestamp(C.WINDOW_END) + pd.Timedelta(minutes=1))
    j = end.merge(latent, on=["learner_id", "skill_id"])
    out["ground_truth_agreement"] = {
        "pairs": int(len(j)),
        "spearman_mastery_vs_hidden_knowledge": spearman(j["mastery"], j["latent_knowledge"]),
        "spearman_when_3plus_evidence": spearman(j.loc[j["evidence_count"] >= 3, "mastery"],
                                                 j.loc[j["evidence_count"] >= 3, "latent_knowledge"]),
    }
    return out


# ------------------------------------------------------------------ 3. recommendations
def recommendation_eval(t: dict, flagged_recs: set) -> dict:
    cutoff = pd.Timestamp(C.TEST_CUTOFF)
    r = t["recommendation"]
    r = r[(r["as_of"] >= cutoff) & ~r["recommendation_id"].isin(flagged_recs)]
    content_recs = r[r["content_id"].notna()]
    ctx = R.Context(t)
    hist = R.History(t["attempt"])
    paths = t["learning_path"].set_index("path_id")

    groups = {"RECOMMENDED": [], "ELIGIBLE_NOT_RECOMMENDED": [], "BLOCKED_BY_PREREQUISITE": []}
    pred_vs_actual = []
    first_choice_hits = decisions_with_next = 0
    for d, grp in r.groupby("as_of"):
        look = M.to_lookup(M.compute_mastery(t["assessment_result"], d))
        tv = d.value
        for (dec, learner, pid), g in grp.groupby(["decision_id", "learner_id", "path_id"]):
            recommended = set(g["content_id"].dropna())
            course = paths.at[pid, "course_id"]
            seen = set()
            window = hist.between(learner, tv, tv + C.OUTCOME_WINDOW_DAYS * NS_PER_DAY)
            nxt = next((x for x in window if x.path == pid), None)
            if nxt is not None and recommended:
                decisions_with_next += 1
                first_choice_hits += nxt.content in recommended
            for x in window:
                if x.path != pid or x.content in seen or x.status != "COMPLETED" or x.score is None:
                    continue
                seen.add(x.content)
                passed = x.score >= C.PASS_SCORE
                if x.content in recommended:
                    groups["RECOMMENDED"].append(passed)
                    p = g.loc[g["content_id"] == x.content, "predicted_success"].iloc[0]
                    pred_vs_actual.append((p, passed))
                elif ctx.course_of.get(x.content) == course and ctx.missing(x.content, learner, look, tv):
                    groups["BLOCKED_BY_PREREQUISITE"].append(passed)
                else:
                    groups["ELIGIBLE_NOT_RECOMMENDED"].append(passed)

    available = {c for c in ctx.course_of if ctx.available(c, cutoff.value)}
    pv = np.array(pred_vs_actual, dtype=float) if pred_vs_actual else np.zeros((0, 2))
    return {
        "test_decision_dates": [str(pd.Timestamp(d).date()) for d in C.TEST_DECISION_DATES],
        "decisions": int(r["decision_id"].nunique()),
        "recommendations": int(len(r)),
        "content_recommendations": int(len(content_recs)),
        "every_recommendation_has_reason_code": bool(r["reason_code"].notna().all() and (r["rationale"].str.len() > 0).all()),
        "reason_codes": r["reason_code"].value_counts().to_dict(),
        "exceptions_routed_to_instructor": int((r["route_to"] == "INSTRUCTOR").sum()),
        "catalog_coverage_pct": _pct(content_recs["content_id"].nunique() / max(1, len(available))),
        "outcomes": r["outcome"].value_counts().to_dict(),
        "learner_next_attempt_was_recommended_pct": _pct(first_choice_hits / decisions_with_next) if decisions_with_next else None,
        "offline_pass_rate_by_item_group": {
            k: {"attempts": len(v), "pass_rate_pct": _pct(np.mean(v)) if v else None} for k, v in groups.items()
        },
        "predicted_vs_actual_for_taken_recommendations": {
            "n": int(len(pv)),
            "mean_predicted": round(float(pv[:, 0].mean()), 3) if len(pv) else None,
            "actual_pass_rate": round(float(pv[:, 1].mean()), 3) if len(pv) else None,
        },
    }


# ------------------------------------------------------------------ entry point
def evaluate(tables: dict, truth: dict, issues: pd.DataFrame, gt: pd.DataFrame) -> dict:
    rep = V.Report()
    clean, _ = V.quarantine(tables, rep)
    flagged = set(issues.loc[issues["table"] == "recommendation", "record_id"])
    detection = V.compare_with_ground_truth(issues, gt)
    return {
        "data_profile": data_profile(clean, truth["learner_truth"]),
        "mastery_temporal_evaluation": mastery_eval(clean, truth["latent_skill_state"]),
        "recommendation_evaluation": recommendation_eval(clean, flagged),
        "anomaly_detection": detection.to_dict(orient="records"),
        "example_learner": example_learner(clean),
    }


# ------------------------------------------------------------------ 5. one worked example
def example_learner(t: dict, as_of=None) -> dict:
    """Every lesson of one learner's course on the last test date, with its status:
    done, recommended (#rank), available, or locked (and which prerequisite blocks it)."""
    as_of = pd.Timestamp(as_of or C.TEST_DECISION_DATES[-1])
    ctx = R.Context(t)
    hist = R.History(t["attempt"])
    look = M.to_lookup(M.compute_mastery(t["assessment_result"], as_of))
    tv = as_of.value
    skill_name = dict(zip(t["skill"]["skill_id"], t["skill"]["name"]))
    short = lambda s: skill_name.get(s, s).split(" · ")[-1]
    fmt = lambda c: f"{ctx.format_of[c].title()}, level {ctx.difficulty[c]}"
    recs = t["recommendation"]
    recs = recs[(recs["as_of"] == as_of) & recs["content_id"].notna()]
    for dec, g in recs.groupby("decision_id", sort=True):
        learner, pid = g["learner_id"].iloc[0], g["path_id"].iloc[0]
        course = ctx.course_of[g["content_id"].iloc[0]]
        if len(g) < 3 or g["reason_code"].eq("REMEDIATE_PREREQUISITE").any():
            continue
        rank = dict(zip(g["content_id"], g["rank"]))
        rows = hist.before(learner, tv)
        done = {r.content for r in rows if r.status == "COMPLETED" and (r.score is None or r.score >= C.PASS_SCORE)}
        by_skill: dict = {}
        for c in ctx.course_items[course]:
            if not ctx.available(c, tv):
                continue
            miss = ctx.missing(c, learner, look, tv)
            if c in rank:
                status, why = "recommended", f"#{int(rank[c])}"
            elif c in done:
                status, why = "done", ""
            elif miss:
                status, why = "locked", "; ".join(f"needs {short(p)} {m:.2f} of {mm:.2f}" for p, m, mm in miss)
            else:
                status, why = "available", ""
            by_skill.setdefault(short(ctx.primary[c]), []).append([fmt(c), status, why])
        statuses = [i[1] for v in by_skill.values() for i in v]
        if "locked" not in statuses or statuses.count("done") < 5:
            continue
        course_title = t["course"].set_index("course_id").at[course, "title"]
        return {
            "learner": learner, "course": course_title, "as_of": as_of.strftime("%d %B %Y"),
            "recommendations": [[int(k), fmt(c), short(s), rc] for k, c, s, rc in
                                zip(g["rank"], g["content_id"], g["target_skill_id"], g["reason_code"])],
            "lessons": by_skill,
        }
    return {}
