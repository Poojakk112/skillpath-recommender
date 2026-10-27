"""Self-contained HTML report (no JavaScript, no external assets) built from metrics.json."""
from __future__ import annotations

import html
import json

CSS = """
:root{color-scheme:light;--surface:#fcfcfb;--panel:#ffffff;--border:#e4e3df;--text:#0b0b0b;
--muted:#52514e;--series:#2a78d6;--track:#efeee9;--good:#008300;--bad:#c62f2e}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--surface:#1a1a19;
--panel:#232322;--border:#3a3a38;--text:#f0efec;--muted:#c3c2b7;--series:#3987e5;--track:#2e2e2c;
--good:#3fb950;--bad:#e66767}}
:root[data-theme="dark"]{color-scheme:dark;--surface:#1a1a19;--panel:#232322;--border:#3a3a38;
--text:#f0efec;--muted:#c3c2b7;--series:#3987e5;--track:#2e2e2c;--good:#3fb950;--bad:#e66767}
*{box-sizing:border-box}body{margin:0;background:var(--surface);color:var(--text);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:980px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:28px;margin:0 0 4px}h2{font-size:20px;margin:40px 0 8px}h3{font-size:15px;margin:20px 0 8px}
p.lead,.muted{color:var(--muted)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:20px 0}
.tile{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:14px}
.tile b{display:block;font-size:26px;font-variant-numeric:tabular-nums}.tile span{color:var(--muted);font-size:13px}
table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--border);
border-radius:10px;overflow:hidden;font-size:14px;margin:8px 0}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--border)}th{color:var(--muted);font-weight:600}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.wrap{overflow-x:auto}
.bars{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:14px 16px;margin:8px 0}
.bar{display:grid;grid-template-columns:minmax(120px,240px) 1fr 64px;gap:10px;align-items:center;margin:6px 0}
.bar .lbl{font-size:13px;color:var(--muted);overflow-wrap:anywhere}
.bar .trk{background:var(--track);border-radius:4px;height:14px}
.bar .fill{background:var(--series);height:14px;border-radius:0 4px 4px 0}
.bar .fill:hover{filter:brightness(1.15)}
.bar .val{font-size:13px;text-align:right;font-variant-numeric:tabular-nums}
.ok{color:var(--good);font-weight:600}.fail{color:var(--bad);font-weight:600}
code{font-size:13px}
"""


def _e(x) -> str:
    return html.escape(str(x))


def bars(rows: list[tuple], unit: str = "", maxv: float | None = None, note: str = "") -> str:
    """Single-series horizontal bars; identity comes from the row label, value is labelled."""
    vals = [v for _, v in rows if v is not None]
    top = maxv or (max(vals) if vals else 1) or 1
    out = ['<div class="bars" role="img">']
    for label, v in rows:
        w = 0 if v is None else max(0.5, 100 * v / top)
        txt = "n/a" if v is None else f"{v:g}{unit}"
        out.append(f'<div class="bar" title="{_e(label)}: {_e(txt)}"><div class="lbl">{_e(label)}</div>'
                   f'<div class="trk"><div class="fill" style="width:{w:.1f}%"></div></div>'
                   f'<div class="val">{_e(txt)}</div></div>')
    if note:
        out.append(f'<div class="muted" style="font-size:13px;margin-top:6px">{_e(note)}</div>')
    out.append("</div>")
    return "".join(out)


def table(header: list, rows: list[list], numeric: set = frozenset()) -> str:
    th = "".join(f'<th class="{"n" if i in numeric else ""}">{_e(h)}</th>' for i, h in enumerate(header))
    body = "".join("<tr>" + "".join(
        f'<td class="{"n" if i in numeric else ""}">{c if str(c).startswith("<span") else _e(c)}</td>'
        for i, c in enumerate(r)) + "</tr>" for r in rows)
    return f'<div class="wrap"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def status(ok: bool, yes="PASS", no="FAIL") -> str:
    return f'<span class="ok">✓ {yes}</span>' if ok else f'<span class="fail">✗ {no}</span>'


def render(metrics: dict, validation: dict, manifest: dict, repro: dict | None) -> str:
    dp = metrics["data_profile"]
    me = metrics["mastery_temporal_evaluation"]
    re_ = metrics["recommendation_evaluation"]
    det = metrics["anomaly_detection"]
    groups = re_["offline_pass_rate_by_item_group"]
    scale = manifest["scale"]
    unexpected = validation["unexpected_issue_types"]
    recall_ok = all(d["recall"] == 1.0 for d in det)
    prec_ok = all(d["precision"] == 1.0 for d in det)
    viol = next((d for d in det if d["issue_type"] == "REC_VIOLATES_PREREQUISITE"), {})

    h = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
         f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
         f"<title>SkillPath Report</title><style>{CSS}</style></head><body><main>"]
    h.append(f"<h1>SkillPath — evaluation report</h1><p class='lead'>Scale <b>{_e(scale['name'])}</b> · seed "
             f"<code>{manifest['seed']}</code> · generator {manifest['generator_version']} · engine "
             f"{manifest['engine_version']} · all data synthetic</p>")
    rec = groups["RECOMMENDED"]["pass_rate_pct"]
    blk = groups["BLOCKED_BY_PREREQUISITE"]["pass_rate_pct"]
    tiles = [
        (f"{scale['learners']:,}", "learners"), (f"{scale['skills']:,}", "skills"),
        (f"{scale['content_items']:,}", "content items"), (f"{dp['attempts']:,}", "attempts"),
        (f"{validation['checks_run']}", "validation checks run"),
        ("0" if not unexpected else str(len(unexpected)), "unexpected issue types on baseline"),
        ("100%" if recall_ok else "<100%", "injected anomalies detected"),
        (f"{rec:.0f}% vs {blk:.0f}%", "pass rate: recommended vs prerequisite-blocked items"),
    ]
    h.append("<div class='tiles'>" + "".join(f"<div class='tile'><b>{_e(a)}</b><span>{_e(b)}</span></div>"
                                              for a, b in tiles) + "</div>")

    # ---------------------------------------------------------------- recommendations
    h.append("<h2>1 · Do the recommendations help?</h2><p class='muted'>Test period only (decisions on "
             f"{', '.join(re_['test_decision_dates'])}). Learners in the synthetic history never saw our "
             "recommendations, so this is an offline comparison: when a learner happened to attempt an item "
             "within 30 days of a decision, how often did they pass it, grouped by what the engine said about "
             "that item at decision time.</p>")
    h.append(bars([("Recommended (top 3)", groups["RECOMMENDED"]["pass_rate_pct"]),
                   ("Eligible, not recommended", groups["ELIGIBLE_NOT_RECOMMENDED"]["pass_rate_pct"]),
                   ("Blocked by prerequisite", groups["BLOCKED_BY_PREREQUISITE"]["pass_rate_pct"])],
                  "%", 100, "Pass rate on the first attempt after the decision. Attempts: " +
                  ", ".join(f"{k.replace('_', ' ').lower()} {v['attempts']}" for k, v in groups.items())))
    h.append(table(["Measure", "Value"], [
        ["Decisions / recommendations", f"{re_['decisions']:,} / {re_['recommendations']:,}"],
        ["Recommendations violating a prerequisite (engine output)",
         status(viol.get("detected", 0) == viol.get("injected", 0), "0 — only the injected ones were found")],
        ["Every recommendation has a reason code + rationale", status(re_["every_recommendation_has_reason_code"])],
        ["Exceptions routed to an instructor", re_["exceptions_routed_to_instructor"]],
        ["Catalog coverage (items recommended / available)", f"{re_['catalog_coverage_pct']}%"],
        ["Learner's next attempt was one of the recommendations", f"{re_['learner_next_attempt_was_recommended_pct']}%"],
        ["Taken recommendations: predicted vs actual pass rate",
         f"{re_['predicted_vs_actual_for_taken_recommendations']['mean_predicted']} vs "
         f"{re_['predicted_vs_actual_for_taken_recommendations']['actual_pass_rate']}"],
    ]))
    h.append("<h3>Reason codes</h3>")
    h.append(bars(sorted(re_["reason_codes"].items(), key=lambda kv: -kv[1])))

    # ---------------------------------------------------------------- mastery
    h.append("<h2>2 · Is mastery estimated well? (temporal split)</h2><p class='muted'>Mastery is computed "
             f"only from evidence before <code>{_e(me['cutoff'][:10])}</code> and scored on "
             f"{me['test_attempts']:,} assessed attempts after it. {_e(me['leakage_check'])}.</p>")
    h.append(bars([(k, v["auc"]) for k, v in me["models"].items()], "", 1.0,
                  "ROC AUC for predicting pass/fail of each test attempt (0.5 = guessing)."))
    h.append(table(["Model", "AUC", "Brier (lower is better)"],
                   [[k, v["auc"], v["brier"]] for k, v in me["models"].items()], {1, 2}))
    h.append("<h3>Calibration of our pass-probability</h3>")
    h.append(table(["Predicted band", "Attempts", "Mean predicted", "Actual pass rate"],
                   [[c["bin"], c["n"], c["predicted"], c["actual"]] for c in me["calibration"]], {1, 2, 3}))
    ga = me["ground_truth_agreement"]
    h.append(f"<p>Against the hidden simulator state (ground truth only), estimated mastery has a Spearman "
             f"correlation of <b>{ga['spearman_mastery_vs_hidden_knowledge']}</b> with true knowledge over "
             f"{ga['pairs']:,} learner-skill pairs.</p>")

    # ---------------------------------------------------------------- anomalies
    h.append("<h2>3 · Are bad records caught?</h2><p class='muted'>Labelled edge cases were injected as new "
             "rows after the valid baseline; labels live only in <code>ground_truth/anomalies.csv</code>. "
             "The validator never reads them.</p>")
    h.append(table(["Issue type", "Injected", "Detected", "False +", "False −", "Precision", "Recall"],
                   [[d["issue_type"], d["injected"], d["detected"], d["false_positive"], d["false_negative"],
                     d["precision"], d["recall"]] for d in det], {1, 2, 3, 4, 5, 6}))

    # ---------------------------------------------------------------- validation
    h.append("<h2>4 · Validation summary</h2>")
    cats: dict = {}
    for c in validation["checks"]:
        k = cats.setdefault(c["category"], [0, 0])
        k[0] += 1
        k[1] += c["status"] == "FAIL"
    h.append(table(["Category", "Checks", "Failing checks"], [[k, v[0], v[1]] for k, v in sorted(cats.items())], {1, 2}))
    h.append(f"<p>Quarantined before business-rule checks: {_e(validation['quarantined'])}. Unexpected issue "
             f"types (anything not deliberately injected): {status(not unexpected, 'none', ', '.join(unexpected))}</p>")
    fails = [c for c in validation["checks"] if c["status"] == "FAIL"]
    h.append(table(["Failing check", "Issue type", "Rows checked", "Failures"],
                   [[c["check"], c["issue_type"], c["rows_checked"], c["failures"]] for c in fails], {2, 3}))

    # ---------------------------------------------------------------- data profile
    gap = dp["prerequisite_gap"]
    h.append("<h2>5 · Does the data look realistic?</h2>")
    h.append(bars([(f"Difficulty {k}", v) for k, v in dp["pass_rate_by_difficulty_pct"].items()], "%", 100,
                  "Pass rate by content difficulty."))
    h.append(bars([("Prerequisite never passed", gap["pass_rate_with_gap_pct"]),
                   ("All prerequisites passed", gap["pass_rate_without_gap_pct"])], "%", 100,
                  f"Pass rate on items that have prerequisites. {gap['attempted_with_unpassed_prerequisite_pct']}% "
                  "of those attempts were made with a prerequisite gap."))
    h.append(table(["Measure", "Value"], [
        ["Cold-start learners (enrolled, no attempts)", dp["cold_start_learners_no_attempts"]],
        ["Attempts per learner p10 / p50 / p90 / p99",
         " / ".join(f"{v:g}" for v in dp["attempts_per_learner_percentiles"].values())],
        ["Repeated attempts", f"{dp['repeated_attempt_share_pct']}%"],
        ["Abandoned attempts", f"{dp['abandoned_share_pct']}%"],
        ["Overall pass rate", f"{dp['pass_rate_pct']}%"],
        ["Attempts by format", ", ".join(f"{k} {v:,}" for k, v in dp["attempts_by_format"].items())],
        ["Content by status", ", ".join(f"{k} {v}" for k, v in dp["content_by_status"].items())],
        ["Learning paths by status", ", ".join(f"{k} {v:,}" for k, v in dp["path_status"].items())],
    ]))

    # ---------------------------------------------------------------- reproducibility
    h.append("<h2>6 · Reproducibility</h2>")
    if repro:
        h.append(table(["Run", "Files", "Identical to run A"], [
            ["B: same seed", repro["files_compared"], status(repro["same_seed_identical"], "all byte-identical", "differs")],
            ["C: seed + 1", repro["files_compared"],
             status(not repro["different_seed_identical"], "differs (as expected)", "identical?!")],
        ], {1}))
    else:
        h.append("<p class='muted'>Run <code>python -m skillpath reproduce</code> to add this section.</p>")
    h.append(f"<p class='muted'>Regenerate: <code>{_e(manifest['generator_command'])}</code></p>")
    h.append("</main></body></html>")
    return "".join(h)


def write(path, metrics, validation, manifest, repro=None) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render(metrics, validation, manifest, repro))


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
