"""End-to-end pipeline: generate -> recommend -> inject -> export -> validate -> evaluate -> report."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd

from . import config as C
from . import generate, inject, io, recommend


def build_dataset(scale: C.Scale, seed: int) -> tuple[dict, dict, pd.DataFrame]:
    """Steps 1-7 of the brief: valid baseline first, then labelled anomalies, then outcomes."""
    labels = inject.Labels()
    tables, truth = generate.generate(scale, seed)                       # valid baseline
    tables = inject.inject_behaviour(tables, scale, seed, labels)        # loops (real behaviour)
    generate.refresh_derived_state(tables)                               # lifecycle follows behaviour
    tables = inject.inject_data_errors(tables, scale, seed, labels)      # corrupt source rows
    recs, snaps = recommend.run(tables)                                  # engine decisions
    tables["recommendation"], tables["mastery_snapshot"] = recs, snaps
    tables = inject.inject_decision_errors(tables, scale, seed, labels)  # bad decision records
    # outcomes are recorded after each 30-day window closes, from the final attempts table
    tables["recommendation"] = recommend.fill_outcomes(tables["recommendation"], tables["attempt"])
    return tables, truth, labels.frame()


def export(scale: C.Scale, seed: int, out: Path, formats=None, log=print) -> dict:
    """Generate and write a dataset folder. Returns the manifest."""
    out = Path(out)
    formats = formats or (("csv", "json", "sql") if scale.name != "full" else ("csv",))
    t0 = time.time()
    tables, truth, anomalies = build_dataset(scale, seed)
    log(f"  generated {sum(len(v) for v in tables.values()):,} rows in {time.time() - t0:.1f}s")
    digests = io.write_tables(tables, out, formats)
    gt = out / "ground_truth"
    digests["ground_truth/anomalies.csv"] = io.write_frame(anomalies, gt / "anomalies.csv")
    for name, df in truth.items():
        digests[f"ground_truth/{name}.csv"] = io.write_frame(df, gt / f"{name}.csv")
    counts = {k: int(len(v)) for k, v in tables.items()}
    counts["ground_truth/anomalies"] = int(len(anomalies))
    cmd = f"python -m skillpath run --scale {scale.name} --seed {seed}"
    man = io.manifest(scale, seed, digests, counts, cmd)
    (out / "manifest.json").write_text(json.dumps(man, indent=2), encoding="utf-8")
    log(f"  wrote {out} ({', '.join(formats)})")
    return man


def reproduce(scale: C.Scale, seed: int, workdir: Path, log=print) -> dict:
    """Generate twice with the same seed (must be byte-identical) and once with seed+1 (must differ)."""
    workdir = Path(workdir)
    quiet = lambda *_: None
    runs = {}
    for name, s in (("A", seed), ("B", seed), ("C", seed + 1)):
        log(f"  run {name} (seed {s})")
        runs[name] = export(scale, s, workdir / f"run_{name}", formats=("csv",), log=quiet)
    a, b, c = (runs[k]["sha256"] for k in "ABC")
    per_file = [{"file": f, "sha256_A": a[f], "same_in_B": a[f] == b.get(f), "same_in_C": a[f] == c.get(f),
                 "rows_A": runs["A"]["row_counts"].get(f.split("/")[-1][:-4]),
                 "rows_B": runs["B"]["row_counts"].get(f.split("/")[-1][:-4])} for f in sorted(a)]
    return {
        "scale": scale.name, "seed": seed, "files_compared": len(a),
        "same_seed_identical": all(p["same_in_B"] for p in per_file) and runs["A"]["row_counts"] == runs["B"]["row_counts"],
        "different_seed_identical": all(p["same_in_C"] for p in per_file),
        "files": per_file,
    }
