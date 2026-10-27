"""Command line entry point.

    python -m skillpath run --scale starter          # generate + validate + evaluate + report
    python -m skillpath run --scale full             # 10k learners, 500k attempts
    python -m skillpath validate --data data/starter # validate an existing dataset folder
    python -m skillpath reproduce --scale starter    # same seed twice -> identical checksums
    python -m skillpath load-sqlite --data data/starter
    python -m skillpath docs                         # regenerate docs/DATA_DICTIONARY.md
"""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import time
from pathlib import Path

import pandas as pd

from . import config as C
from . import evaluate, io, pipeline, report, validate

ROOT = Path(__file__).resolve().parent.parent


def _dump(obj, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")


def cmd_validate(data: Path, reports: Path) -> dict:
    t0 = time.time()
    tables = io.read_tables(data)
    issues, summary = validate.validate(tables)
    reports.mkdir(parents=True, exist_ok=True)
    issues.to_csv(reports / "issues_detected.csv", index=False)
    _dump(summary, reports / "validation_summary.json")
    gt_path = data / "ground_truth" / "anomalies.csv"
    if gt_path.exists():
        gt = pd.read_csv(gt_path)
        validate.compare_with_ground_truth(issues, gt).to_csv(reports / "detection_vs_ground_truth.csv", index=False)
    print(f"  validation: {summary['checks_run']} checks, {summary['checks_failed']} failing, "
          f"unexpected issue types: {summary['unexpected_issue_types'] or 'none'} ({time.time() - t0:.1f}s)")
    return {"tables": tables, "issues": issues, "summary": summary}


def cmd_evaluate(data: Path, reports: Path, v: dict) -> dict:
    t0 = time.time()
    gt = pd.read_csv(data / "ground_truth" / "anomalies.csv")
    truth = {n: pd.read_csv(data / "ground_truth" / f"{n}.csv") for n in ("learner_truth", "latent_skill_state")}
    metrics = evaluate.evaluate(v["tables"], truth, v["issues"], gt)
    _dump(metrics, reports / "metrics.json")
    print(f"  evaluation written ({time.time() - t0:.1f}s)")
    return metrics


def cmd_report(data: Path, reports: Path) -> None:
    repro_path = reports / "reproducibility.json"
    report.write(reports / "report.html", report.load_json(reports / "metrics.json"),
                 report.load_json(reports / "validation_summary.json"), report.load_json(data / "manifest.json"),
                 report.load_json(repro_path) if repro_path.exists() else None)
    print(f"  report: {reports / 'report.html'}")


def cmd_reproduce(scale: C.Scale, seed: int, reports: Path) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="skillpath_repro_"))
    try:
        res = pipeline.reproduce(scale, seed, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    _dump(res, reports / "reproducibility.json")
    print(f"  reproducibility: same seed identical = {res['same_seed_identical']}, "
          f"seed+1 identical = {res['different_seed_identical']} ({res['files_compared']} files)")
    return res


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="skillpath", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "reproduce"):
        p = sub.add_parser(name)
        p.add_argument("--scale", choices=sorted(C.SCALES), default="starter")
        p.add_argument("--seed", type=int, default=C.DEFAULT_SEED)
        p.add_argument("--out", type=Path, default=None, help="dataset folder (default data/<scale>)")
        p.add_argument("--reports", type=Path, default=None, help="reports folder (default reports/<scale>)")
        if name == "run":
            p.add_argument("--with-reproduce", action="store_true", help="also run the reproducibility check")
    for name in ("validate", "load-sqlite"):
        p = sub.add_parser(name)
        p.add_argument("--data", type=Path, required=True)
        p.add_argument("--reports", type=Path, default=None)
    sub.add_parser("docs")
    a = ap.parse_args(argv)

    if a.cmd in ("run", "reproduce"):
        scale = C.SCALES[a.scale]
        out = a.out or ROOT / "data" / scale.name
        reports = a.reports or ROOT / "reports" / scale.name
        if a.cmd == "reproduce":
            cmd_reproduce(scale, a.seed, reports)
            return
        print(f"[1/4] generating {scale.name} dataset (seed {a.seed}) -> {out}")
        pipeline.export(scale, a.seed, out)
        print("[2/4] validating")
        v = cmd_validate(out, reports)
        print("[3/4] evaluating")
        cmd_evaluate(out, reports, v)
        if a.with_reproduce:
            cmd_reproduce(scale, a.seed, reports)
        print("[4/4] report")
        cmd_report(out, reports)
    elif a.cmd == "validate":
        reports = a.reports or ROOT / "reports" / a.data.name
        cmd_validate(a.data, reports)
    elif a.cmd == "load-sqlite":
        db = a.data / "skillpath.db"
        rep = io.load_sqlite(a.data, db)
        for k, v in rep.items():
            print(f"  {k:22s} loaded {v['loaded']:>8,}  rejected by DB constraints {v['rejected_by_constraints']:>4}")
        print(f"  -> {db}")
    elif a.cmd == "docs":
        (ROOT / "docs").mkdir(exist_ok=True)
        (ROOT / "docs" / "DATA_DICTIONARY.md").write_text(io.data_dictionary_md(), encoding="utf-8")
        print("  wrote docs/DATA_DICTIONARY.md")
