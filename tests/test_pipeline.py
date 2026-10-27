"""Tests for the acceptance criteria: prerequisites respected, reproducible mastery,
temporal split, reason codes, anomaly detection and fixed-seed reproducibility.

Run with:  python -m pytest -q
"""

import pandas as pd
import pytest

from skillpath import config as C
from skillpath import generate, ids, io, mastery as M, pipeline, recommend as R, validate as V
from skillpath.schema import INJECTED_ISSUE_TYPES, TABLES

TINY = C.SCALES["tiny"]


@pytest.fixture(scope="module")
def built():
    tables, truth, gt = pipeline.build_dataset(TINY, C.DEFAULT_SEED)
    return tables, truth, gt


@pytest.fixture(scope="module")
def exported(tmp_path_factory, built):
    out = tmp_path_factory.mktemp("tiny")
    pipeline.export(TINY, C.DEFAULT_SEED, out, log=lambda *_: None)
    return out, io.read_tables(out)


@pytest.fixture(scope="module")
def validated(exported):
    _, tables = exported
    return V.validate(tables)


# ------------------------------------------------------------------ IDs & schema
def test_ids_follow_conventions(built):
    t = built[0]
    assert ids.learner(1) == "LRN-000001" and ids.attempt("LRN-000001", 1) == "ATT-LRN-000001-000001"
    assert t["learning_path"]["path_id"].str.match(r"^PATH-\d{4}-\d{6}$").all()
    assert t["attempt"]["attempt_id"].str.match(r"^ATT-LRN-\d{6}-\d{6}$").all()


def test_prerequisite_graph_is_acyclic(built):
    assert V._cycle_nodes(built[0]["skill_prerequisite"]) == []


def test_ground_truth_never_leaks_into_inputs(built):
    tables, truth, gt = built
    forbidden = {"issue_type", "ground_truth", "is_anomaly", "ability", "learning_rate", "latent_knowledge", "segment"}
    for name, df in tables.items():
        assert not forbidden & set(df.columns), name
        assert set(df.columns) <= {f.name for f in TABLES[name].fields}, name


# ------------------------------------------------------------------ mastery
def test_mastery_ignores_evidence_at_or_after_as_of():
    res = pd.DataFrame({
        "result_id": ["r1", "r2"], "learner_id": ["L", "L"], "skill_id": ["S", "S"],
        "skill_weight": [1.0, 1.0], "score": [40, 100],
        "assessed_at": pd.to_datetime(["2026-01-01", "2026-02-01"]),
    })
    m = M.compute_mastery(res, "2026-02-01")  # the 100 happens exactly at as_of -> excluded
    assert len(m) == 1 and m.iloc[0]["evidence_count"] == 1
    assert m.iloc[0]["mastery"] == pytest.approx((0.3 * 1.0 + 0.4) / 2.0)


def test_mastery_is_reproducible(built):
    res = built[0]["assessment_result"]
    a = M.compute_mastery(res, C.TEST_CUTOFF)
    b = M.compute_mastery(res.sample(frac=1.0, random_state=1), C.TEST_CUTOFF)  # row order must not matter
    pd.testing.assert_frame_equal(a.reset_index(drop=True), b.reset_index(drop=True))


# ------------------------------------------------------------------ recommender
def test_engine_recommendations_respect_prerequisites(built):
    tables, _, gt = built
    injected = set(gt.loc[gt["issue_type"] == "REC_VIOLATES_PREREQUISITE", "record_id"])
    recs = tables["recommendation"]
    recs = recs[recs["content_id"].notna() & ~recs["recommendation_id"].isin(injected)]
    ctx = R.Context(tables)
    for d, g in recs.groupby("as_of"):
        look = M.to_lookup(M.compute_mastery(tables["assessment_result"], d))
        for l, c in zip(g["learner_id"], g["content_id"]):
            assert ctx.missing(c, l, look, d.value) == []


def test_every_recommendation_has_reason_and_rationale(built):
    r = built[0]["recommendation"]
    assert r["reason_code"].notna().all()
    assert (r["rationale"].str.len() > 10).all()


def test_loop_rule():
    assert R.is_loop(["A", "B"] * 4, [55, 54, 52, 50, 49, 47, 45, 44])
    assert not R.is_loop(["A", "B"] * 4, [55, 54, 52, 50, 49, 47, 45, 75])   # a pass is progress
    assert not R.is_loop(["A", "B"] * 4, [40, 41, 42, 43, 50, 51, 52, 53])   # score gain is progress
    assert not R.is_loop(["A", "B", "C", "A", "B", "C", "A", "B"], [40] * 8)  # 3 items: not a loop


def test_injected_loops_are_escalated_by_the_engine(built):
    tables, _, gt = built
    loop_paths = set(gt.loc[gt["issue_type"] == "PATH_LOOP_NO_PROGRESS", "record_id"])
    r = tables["recommendation"]
    escalated = set(r.loc[r["reason_code"] == "ESCALATE_LOOP_NO_PROGRESS", "path_id"])
    assert loop_paths and loop_paths <= escalated


def test_cold_start_learner_gets_entry_items():
    tables, _ = generate.generate(TINY, C.DEFAULT_SEED)
    ctx = R.Context(tables)
    path = tables["learning_path"].iloc[0]
    t = pd.Timestamp("2026-09-01").value
    recs, _ = R.decide(ctx, {}, [], path["learner_id"], path["path_id"], path["course_id"], t, "VIDEO")
    assert recs and all(r["reason_code"] in ("COLD_START_ENTRY", "REMEDIATE_PREREQUISITE") for r in recs)


# ------------------------------------------------------------------ validation
def test_validator_finds_every_injected_anomaly_and_nothing_else(exported, validated):
    out, _ = exported
    issues, summary = validated
    gt = pd.read_csv(out / "ground_truth" / "anomalies.csv")
    d = V.compare_with_ground_truth(issues, gt)
    assert set(d["issue_type"]) == set(INJECTED_ISSUE_TYPES)
    assert (d["recall"] == 1.0).all() and (d["precision"] == 1.0).all()
    assert summary["unexpected_issue_types"] == []


def test_temporal_split_has_no_leakage(exported):
    from skillpath import evaluate
    _, tables = exported
    rep = V.Report()
    clean, _ = V.quarantine(tables, rep)
    latent = pd.DataFrame(columns=["learner_id", "skill_id", "latent_knowledge"])
    out = evaluate.mastery_eval(clean, latent)
    assert out["leakage_check"].endswith("OK") and out["test_attempts"] > 0


# ------------------------------------------------------------------ reproducibility
def test_same_seed_same_bytes_different_seed_differs(tmp_path):
    res = pipeline.reproduce(TINY, C.DEFAULT_SEED, tmp_path, log=lambda *_: None)
    assert res["same_seed_identical"] is True
    assert res["different_seed_identical"] is False


def test_every_prerequisite_of_every_covered_skill_is_met(built):
    """Independent of Context.required: check straight from the raw tables."""
    tables, _, gt = built
    injected = set(gt["record_id"])
    pre = tables["skill_prerequisite"].groupby("skill_id")["prerequisite_skill_id"].apply(list).to_dict()
    covers = tables["content_skill"].groupby("content_id")["skill_id"].apply(list).to_dict()
    recs = tables["recommendation"]
    recs = recs[recs["content_id"].notna() & ~recs["recommendation_id"].isin(injected)]
    for d, g in recs.groupby("as_of"):
        look = M.to_lookup(M.compute_mastery(tables["assessment_result"], d))
        for l, c in zip(g["learner_id"], g["content_id"]):
            for s in covers[c]:
                for p in pre.get(s, []):
                    assert M.get(look, l, p)[0] >= C.PREREQ_MIN_MASTERY, (c, s, p)
