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
    tables, truth = generate.generate(scale, seed)
    recs, snaps = recommend.run(tables)
    tables["recommendation"], tables["mastery_snapshot"] = recs, snaps
    tables, anomalies = inject.inject(tables, scale, seed)
    # outcomes are recorded after the 30-day window closes, i.e. from the final attempts table
    tables["recommendation"] = recommend.fill_outcomes(tables["recommendation"], tables["attempt"])
    return tables, truth, anomalies


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
