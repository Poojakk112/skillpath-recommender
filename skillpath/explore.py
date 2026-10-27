"""Learner Explorer: a single self-contained HTML page to inspect individual decisions.

For one decision date it embeds, per learner path: the learner's mastery on every
skill of the course (and on cross-course prerequisites), the status of every item
(done / recommended / available / locked by which prerequisite), the engine's
recommendations with reason codes, and the learner's recent attempts.
All logic stays in Python (the same Context/mastery code the engine uses); the
page only displays it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from . import config as C
from . import mastery as M
from . import recommend as R
from . import validate as V

TEMPLATE = Path(__file__).with_name("explorer_template.html")


def _short(name: str) -> str:
    """'Data 02 · L2 · Topic 05' -> 'Topic 05'"""
    return name.split(" · ")[-1]


def build_payload(tables: dict, as_of=None, limit: int = 600) -> dict:
    as_of = pd.Timestamp(as_of or C.TEST_DECISION_DATES[-1])
    clean, _ = V.quarantine(tables, V.Report())
    ctx = R.Context(clean)
    hist = R.History(clean["attempt"])
    look = M.to_lookup(M.compute_mastery(clean["assessment_result"], as_of))
    t = as_of.value

    courses = clean["course"].set_index("course_id")
    skills = clean["skill"].set_index("skill_id")
    content = clean["content_item"].set_index("content_id")
    learners = clean["learner"].set_index("learner_id")
    paths = clean["learning_path"].set_index("path_id")

    recs = clean["recommendation"]
    recs = recs[recs["as_of"] == as_of]
    decisions = list(recs.groupby("decision_id", sort=True))
    # keep every exception and every reason type, then fill evenly up to the limit
    if len(decisions) > limit:
        special = [d for d in decisions if (d[1]["route_to"] == "INSTRUCTOR").any()
                   or (d[1]["reason_code"] == "REMEDIATE_PREREQUISITE").any()][: limit // 3]
        rest = [d for d in decisions if d not in special]
        step = max(1, len(rest) // (limit - len(special)))
        decisions = sorted(special + rest[::step][: limit - len(special)], key=lambda d: d[0])

    used_skills, used_content, entries = set(), set(), []
    for dec_id, g in decisions:
        learner, pid = g["learner_id"].iloc[0], g["path_id"].iloc[0]
        course = paths.at[pid, "course_id"]
        rows = hist.before(learner, t)
        passed = {r.content for r in rows if r.status == "COMPLETED" and (r.score is None or r.score >= C.PASS_SCORE)}
        rec_rank = {c: int(k) for c, k in zip(g["content_id"], g["rank"]) if isinstance(c, str)}
        items = []
        for c in ctx.course_items.get(course, ()):
            if not ctx.available(c, t):
                continue
            miss = ctx.missing(c, learner, look, t)
            status = ("recommended" if c in rec_rank else "done" if c in passed
                      else "locked" if miss else "available")
            items.append([c, status, rec_rank.get(c, 0), [[p, round(m, 2), mm] for p, m, mm in miss]])
            used_content.add(c)
        course_skills = list(ctx.course_skills.get(course, ()))
        extra = sorted({p for s in course_skills for p, _, _ in ctx.prereqs.get(s, ()) if p not in course_skills})
        targets = [s for s in g["target_skill_id"] if isinstance(s, str)]
        for c in g["content_id"]:
            if isinstance(c, str) and ctx.course_of.get(c) != course:
                extra += [s for s in ctx.mapped.get(c, ()) if s not in course_skills and s not in extra]
                used_content.add(c)
        mastery = {}
        for s in course_skills + extra + targets:
            m, n, _ = M.get(look, learner, s)
            mastery[s] = [round(float(m), 3), int(n)]
        used_skills.update(mastery)
        recent = [[pd.Timestamp(r.start).strftime("%d %b %Y"), r.content, r.status, r.score]
                  for r in [r for r in rows if r.path == pid][-8:]]
        used_content.update(r[1] for r in recent)
        entries.append(dict(
            decision=dec_id, learner=learner, path=pid, course=course,
            path_status=None, mastery=mastery, extra_skills=extra, items=items, recent=recent,
            recs=[dict(rank=int(k), content=c if isinstance(c, str) else None,
                       skill=s if isinstance(s, str) else None, reason=rc, rationale=ra,
                       p=None if pd.isna(p) else float(p), outcome=o, route=rt)
                  for k, c, s, rc, ra, p, o, rt in zip(g["rank"], g["content_id"], g["target_skill_id"],
                                                        g["reason_code"], g["rationale"], g["predicted_success"],
                                                        g["outcome"], g["route_to"])],
        ))

    learner_ids = sorted({e["learner"] for e in entries})
    lpaths = clean["learning_path"][clean["learning_path"]["learner_id"].isin(learner_ids)]
    att_counts = clean["attempt"].groupby("learner_id").size()
    return {
        "as_of": as_of.strftime("%d %B %Y"),
        "engine": C.ENGINE_VERSION,
        "thresholds": {"prereq": C.PREREQ_MIN_MASTERY, "mastered": C.MASTERED_THRESHOLD, "pass": C.PASS_SCORE},
        "courses": {c: [r["title"], r["level"].title()] for c, r in courses.iterrows()},
        "skills": {s: [_short(skills.at[s, "name"]), int(skills.at[s, "level"]), skills.at[s, "course_id"],
                       [p for p, _, _ in ctx.prereqs.get(s, ())]] for s in sorted(used_skills)},
        "content": {c: [content.at[c, "format"].title(), int(content.at[c, "difficulty"]), ctx.primary.get(c),
                        content.at[c, "course_id"]] for c in sorted(used_content)},
        "learners": {l: [learners.at[l, "signup_at"].strftime("%d %b %Y"), learners.at[l, "preferred_format"].title(),
                         learners.at[l, "account_status"].title(), int(att_counts.get(l, 0)),
                         [[p, c, st.title()] for p, c, st in zip(g["path_id"], g["course_id"], g["current_status"])]]
                     for l, g in lpaths.groupby("learner_id")},
        "decisions": entries,
    }


def write(tables: dict, out: Path, as_of=None, limit: int = 600) -> Path:
    payload = build_payload(tables, as_of, limit)
    html = TEMPLATE.read_text(encoding="utf-8").replace(
        "/*__DATA__*/null", json.dumps(payload, separators=(",", ":")).replace("</", "<\\/"))
    out = Path(out)
    out.write_text(html, encoding="utf-8")
    return out
