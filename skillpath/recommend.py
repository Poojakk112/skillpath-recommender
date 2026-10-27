"""Prerequisite-aware next-activity recommender (rules engine `rules-v1`).

For one learner, one learning path and one decision time `as_of`:

1. Exception: if the path's last 8 completed attempts loop on <= 2 items with
   no pass and no score gain -> ESCALATE_LOOP_NO_PROGRESS (routed to an instructor).
2. Candidates = items of the path's course that are available at `as_of` and
   not yet done (assessed: passed; unassessed: completed).
3. Hard gate: an item is ELIGIBLE only if every prerequisite of every skill it
   maps to has mastery >= min_mastery, using evidence strictly before `as_of`.
4. Rank eligible items with a transparent utility (need, difficulty fit,
   syllabus frontier, evidence value, format preference) and attach a reason code.
5. If nothing is eligible, walk the prerequisite graph to the nearest
   unlockable prerequisite and recommend that (REMEDIATE_PREREQUISITE).
   If even that fails -> ESCALATE_NO_ELIGIBLE_CONTENT.
"""
from __future__ import annotations

from bisect import bisect_left
from collections import namedtuple

import numpy as np
import pandas as pd

from . import config as C
from . import ids
from . import lifecycle
from . import mastery as M
from .schema import OPEN_PATH_STATUS

NS_PER_DAY = 86_400 * 10**9
Row = namedtuple("Row", "start attempt_id content path status score")


def _ns(ts) -> int | None:
    return None if pd.isna(ts) else pd.Timestamp(ts).value


class Context:
    """Read-only lookups over the catalog tables."""

    def __init__(self, tables: dict):
        content = tables["content_item"]
        self.course_of = dict(zip(content["content_id"], content["course_id"]))
        self.format_of = dict(zip(content["content_id"], content["format"]))
        self.difficulty = dict(zip(content["content_id"], content["difficulty"].astype(int)))
        self.assessed = dict(zip(content["content_id"], content["is_assessed"].astype(bool)))
        self.avail_from = {c: _ns(t) for c, t in zip(content["content_id"], content["published_at"])}
        self.avail_to = {c: _ns(t) for c, t in zip(content["content_id"], content["retired_at"])}

        skills = tables["skill"]
        self.skill_level = dict(zip(skills["skill_id"], skills["level"].astype(int)))
        self.course_skills: dict[str, list] = {}
        for s, c in zip(skills["skill_id"], skills["course_id"]):
            self.course_skills.setdefault(c, []).append(s)

        self.prereqs: dict[str, list] = {}
        pre = tables["skill_prerequisite"]
        for s, p, mm, eff in zip(pre["skill_id"], pre["prerequisite_skill_id"], pre["min_mastery"],
                                 pre["effective_from"]):
            self.prereqs.setdefault(s, []).append((p, float(mm), _ns(eff)))

        self.mapped: dict[str, list] = {}
        self.primary: dict[str, str] = {}
        cs = tables["content_skill"]
        known_skills = set(self.skill_level)
        for c, s, role in zip(cs["content_id"], cs["skill_id"], cs["role"]):
            if s not in known_skills:
                continue  # orphan mapping (quarantined by the validator)
            self.mapped.setdefault(c, []).append(s)
            if role == "PRIMARY":
                self.primary[c] = s

        # required prerequisites per item = prerequisites of its skills it does not teach itself
        self.required: dict[str, list] = {}
        for c, skills_of_c in self.mapped.items():
            req = {}
            for s in skills_of_c:
                for p, mm, eff in self.prereqs.get(s, ()):
                    if p not in skills_of_c:
                        old = req.get(p)
                        req[p] = (max(mm, old[0]), min(eff, old[1])) if old else (mm, eff)
            self.required[c] = sorted((p, mm, eff) for p, (mm, eff) in req.items())

        self.course_items: dict[str, list] = {}
        self.skill_items: dict[str, list] = {}
        for c in sorted(self.course_of):
            if c not in self.primary:
                continue
            self.course_items.setdefault(self.course_of[c], []).append(c)
            self.skill_items.setdefault(self.primary[c], []).append(c)

    def available(self, c: str, t: int) -> bool:
        f, to = self.avail_from.get(c), self.avail_to.get(c)
        return f is not None and f <= t and (to is None or to > t)

    def missing(self, c: str, learner: str, look: dict, t: int) -> list:
        """Prerequisites of item `c` that the learner has NOT met at time t: [(skill, mastery, needed)]."""
        out = []
        for p, mm, eff in self.required.get(c, ()):
            if eff is not None and eff > t:
                continue
            m = M.get(look, learner, p)[0]
            if m < mm:
                out.append((p, m, mm))
        return out


class History:
    """Each learner's attempts in chronological order, with fast time slicing."""

    def __init__(self, attempts: pd.DataFrame):
        a = attempts.sort_values(["learner_id", "started_at", "attempt_id"], kind="mergesort")
        starts = a["started_at"].to_numpy(dtype="datetime64[ns]").astype(np.int64)
        scores = a["score"].astype("float64").to_numpy()
        rows = [Row(int(s), aid, c, p, st, None if np.isnan(sc) else int(sc))
                for s, aid, c, p, st, sc in zip(starts, a["attempt_id"], a["content_id"], a["path_id"],
                                                a["status"], scores)]
        self.by_learner: dict[str, tuple] = {}
        learners = a["learner_id"].to_numpy()
        i = 0
        while i < len(rows):
            j = i
            while j < len(rows) and learners[j] == learners[i]:
                j += 1
            self.by_learner[learners[i]] = ([r.start for r in rows[i:j]], rows[i:j])
            i = j

    def before(self, learner: str, t: int) -> list:
        starts, rows = self.by_learner.get(learner, ([], []))
        return rows[: bisect_left(starts, t)]

    def between(self, learner: str, t0: int, t1: int) -> list:
        starts, rows = self.by_learner.get(learner, ([], []))
        return rows[bisect_left(starts, t0): bisect_left(starts, t1)]


# ------------------------------------------------------------------ loop rule
def is_loop(contents: list, scores: list) -> bool:
    """True if this window of LOOP_WINDOW completed attempts shows a loop without progress."""
    if len(contents) < C.LOOP_WINDOW or len(set(contents)) > C.LOOP_MAX_DISTINCT:
        return False
    if any(s is None for s in scores):     # an unassessed completion counts as progress
        return False
    if max(scores) >= C.PASS_SCORE:
        return False
    half = C.LOOP_WINDOW // 2
    return max(scores[half:]) <= max(scores[:half])


def path_loops_now(rows: list) -> bool:
    done = [r for r in rows if r.status == "COMPLETED"][-C.LOOP_WINDOW:]
    return is_loop([r.content for r in done], [r.score for r in done])


# ------------------------------------------------------------------ one decision
def decide(ctx: Context, look: dict, history: list, learner: str, path_id: str, course: str,
           t: int, preferred_format: str) -> tuple[list, set]:
    """Return (recommendations, skills whose mastery the decision relied on)."""
    used: set = set()
    path_rows = [r for r in history if r.path == path_id]
    if path_loops_now(path_rows):
        last = [r for r in path_rows if r.status == "COMPLETED"][-C.LOOP_WINDOW:]
        items = sorted({r.content for r in last})
        target = ctx.primary.get(last[-1].content)
        used.add(target)
        return [_exception("ESCALATE_LOOP_NO_PROGRESS", target,
                           f"Last {C.LOOP_WINDOW} attempts cycle over {', '.join(items)} with no pass and "
                           f"no score gain (best {max(r.score for r in last)}); needs instructor support")], used

    done, last_assessed, has_evidence = set(), {}, False
    for r in history:
        if r.status != "COMPLETED":
            continue
        if ctx.assessed.get(r.content):
            has_evidence = True
            last_assessed[ctx.primary.get(r.content)] = (r.score, ctx.difficulty[r.content])
            if r.score >= C.PASS_SCORE:
                done.add(r.content)
        else:
            done.add(r.content)

    remaining = [c for c in ctx.course_items.get(course, ()) if c not in done and ctx.available(c, t)]
    if not remaining:
        return [_exception("ESCALATE_NO_REMAINING_CONTENT", None,
                           "Every available item of the course is already done but the path is not "
                           "complete; instructor should review the course content")], used

    eligible, blocked = [], {}
    for c in remaining:
        miss = ctx.missing(c, learner, look, t)
        if miss:
            for p, m, mm in miss:
                blocked.setdefault(p, [0, m, mm])[0] += 1
        else:
            eligible.append(c)

    if eligible:
        recs = _rank(ctx, look, learner, course, eligible, last_assessed, has_evidence, preferred_format, used)
        return recs, used
    recs = _remediate(ctx, look, learner, t, blocked, done, len(remaining), used)
    return recs, used


def _rank(ctx, look, learner, course, eligible, last_assessed, has_evidence, pref, used) -> list:
    unmastered_levels = [ctx.skill_level[s] for s in ctx.course_skills.get(course, ())
                         if M.get(look, learner, s)[0] < C.MASTERED_THRESHOLD]
    frontier = min(unmastered_levels) if unmastered_levels else None
    scored = []
    for pos, c in enumerate(eligible):
        s = ctx.primary[c]
        m, n, _ = M.get(look, learner, s)
        d, lvl = ctx.difficulty[c], ctx.skill_level[s]
        fmt_bonus = 0.05 if ctx.format_of[c] == pref else 0.0
        if not has_evidence:
            reason = "COLD_START_ENTRY"
            u = 1.0 - 0.15 * (d - 1) - 0.10 * (lvl - 1) + fmt_bonus
        else:
            desired = min(5.0, max(1.0, lvl + 2.0 * (m - 0.5)))
            u = (0.45 * (1.0 - m) + 0.30 * (1.0 - abs(d - desired) / 4.0)
                 + (0.15 if lvl == frontier else 0.0) + (0.05 if ctx.assessed[c] else 0.0) + fmt_bonus)
            last = last_assessed.get(s)
            if last is not None and last[0] < C.PASS_SCORE:
                reason = "REMEDIATE_RECENT_FAIL"
                if d <= last[1]:
                    u += 0.10
            elif m >= C.MASTERED_THRESHOLD:
                reason = "STRETCH_CHALLENGE"
            elif m >= C.PREREQ_MIN_MASTERY:
                reason = "REINFORCE_NEAR_MASTERY"
            else:
                reason = "NEXT_FRONTIER_SKILL"
        scored.append((-round(u, 6), pos, c, s, reason, m, n))
    scored.sort()

    recs, per_skill = [], {}
    for _, _, c, s, reason, m, n in scored:
        if per_skill.get(s, 0) >= 2:
            continue
        per_skill[s] = per_skill.get(s, 0) + 1
        recs.append(_content_rec(ctx, look, learner, c, s, reason, m, n, used, extra=_REASON_TEXT[reason]))
        if len(recs) == C.TOP_K:
            break
    return recs


def _remediate(ctx, look, learner, t, blocked, done, n_remaining, used) -> list:
    """Nothing in the course is eligible: find the nearest prerequisite the learner CAN work on."""
    queue = sorted(blocked, key=lambda s: (-blocked[s][0], blocked[s][1], s))
    seen = set(queue)
    recs = []
    while queue and len(recs) < C.TOP_K:
        s = queue.pop(0)
        items = [c for c in ctx.skill_items.get(s, ()) if ctx.available(c, t)]
        items = [c for c in items if c not in done] or items  # allow a review if all are done
        ok = []
        for c in items:
            miss = ctx.missing(c, learner, look, t)
            if miss:
                for p, _, _ in miss:
                    if p not in seen:
                        seen.add(p)
                        queue.append(p)
            else:
                ok.append(c)
        m, n, _ = M.get(look, learner, s)
        desired = min(5.0, max(1.0, ctx.skill_level[s] + 2.0 * (m - 0.5)))
        ok.sort(key=lambda c: (abs(ctx.difficulty[c] - desired), c))
        why = (f"all {n_remaining} remaining course items are blocked by prerequisites; "
               f"{s} is the nearest prerequisite the learner can work on now")
        for c in ok[:2]:
            recs.append(_content_rec(ctx, look, learner, c, s, "REMEDIATE_PREREQUISITE", m, n, used, extra=why))
            if len(recs) == C.TOP_K:
                break
    if recs:
        return recs
    top = sorted(blocked, key=lambda s: (-blocked[s][0], blocked[s][1], s))[:3]
    used.update(top)
    detail = ", ".join(f"{p} {blocked[p][1]:.2f}<{blocked[p][2]:.2f}" for p in top)
    return [_exception("ESCALATE_NO_ELIGIBLE_CONTENT", top[0] if top else None,
                       f"All {n_remaining} remaining items are blocked by unmet prerequisites ({detail}) "
                       f"and no prerequisite item is reachable; instructor review needed")]


_REASON_TEXT = {
    "COLD_START_ENTRY": "no assessment history yet; easy entry item",
    "NEXT_FRONTIER_SKILL": "prerequisites met and skill not yet learned",
    "REINFORCE_NEAR_MASTERY": "skill close to mastery; one more success should consolidate it",
    "REMEDIATE_RECENT_FAIL": "last assessed attempt on this skill failed; practise at same or lower difficulty",
    "STRETCH_CHALLENGE": "skill mastered; harder item still open",
}


def _content_rec(ctx, look, learner, c, s, reason, m, n, used, extra) -> dict:
    used.add(s)
    met = []
    for p, mm, _ in ctx.required.get(c, ()):
        used.add(p)
        met.append(f"{p} {M.get(look, learner, p)[0]:.2f}>={mm:.2f}")
    prereq_txt = "prerequisites met: " + ", ".join(met) if met else "no prerequisites"
    return dict(
        content_id=c, target_skill_id=s, reason_code=reason, route_to="LEARNER",
        predicted_success=M.predicted_success(m, ctx.difficulty[c]),
        rationale=f"{extra}; {s} mastery {m:.2f} (n={n}); difficulty {ctx.difficulty[c]}; {prereq_txt}",
    )


def _exception(reason: str, target, rationale: str) -> dict:
    return dict(content_id=None, target_skill_id=target, reason_code=reason, route_to="INSTRUCTOR",
                predicted_success=None, rationale=rationale)


# ------------------------------------------------------------------ batch run
def run(tables: dict, dates=C.DECISION_DATES) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Make decisions for every open path at every decision date.

    Returns (recommendation, mastery_snapshot) tables. Outcomes are filled from
    the learner's attempts in the 30 days after each decision.
    """
    ctx = Context(tables)
    hist = History(tables["attempt"])
    results = tables["assessment_result"]
    events = tables["learning_path_event"]
    paths = tables["learning_path"]
    path_info = dict(zip(paths["path_id"], zip(paths["learner_id"], paths["course_id"])))
    pref = dict(zip(tables["learner"]["learner_id"], tables["learner"]["preferred_format"]))

    rows, snaps = [], {}
    n_dec = 0
    for d in dates:
        t = pd.Timestamp(d).value
        look = M.to_lookup(M.compute_mastery(results, d))
        status = lifecycle.status_as_of(events, d)
        for pid in sorted(status.index[status.isin(OPEN_PATH_STATUS)]):
            learner, course = path_info[pid]
            n_dec += 1
            recs, used = decide(ctx, look, hist.before(learner, t), learner, pid, course, t, pref[learner])
            for s in used:
                if s is not None:
                    snaps[(t, learner, s)] = M.get(look, learner, s)
            for rank, r in enumerate(recs, 1):
                rows.append(dict(decision_id=ids.seq("DEC", n_dec), learner_id=learner, path_id=pid,
                                 as_of=t, rank=rank, **r))

    snap_keys = sorted(snaps)
    snap_id = {k: ids.seq("MSN", i + 1) for i, k in enumerate(snap_keys)}
    snapshots = pd.DataFrame({
        "snapshot_id": [snap_id[k] for k in snap_keys],
        "learner_id": [k[1] for k in snap_keys],
        "skill_id": [k[2] for k in snap_keys],
        "as_of": pd.to_datetime([k[0] for k in snap_keys]),
        "mastery": [float(snaps[k][0]) for k in snap_keys],
        "evidence_count": [int(snaps[k][1]) for k in snap_keys],
        "last_evidence_at": pd.to_datetime([snaps[k][2] for k in snap_keys]),
        "method_version": C.MASTERY_METHOD,
    })

    recs = pd.DataFrame(rows)
    recs.insert(0, "recommendation_id", [ids.seq("REC", i + 1) for i in range(len(recs))])
    recs["mastery_snapshot_id"] = [snap_id.get((t, l, s)) for t, l, s in
                                   zip(recs["as_of"], recs["learner_id"], recs["target_skill_id"])]
    recs["engine_version"] = C.ENGINE_VERSION
    recs["as_of"] = pd.to_datetime(recs["as_of"])
    recs = fill_outcomes(recs, tables["attempt"], ctx, hist)
    return recs[REC_COLUMNS], snapshots


REC_COLUMNS = ["recommendation_id", "decision_id", "learner_id", "path_id", "as_of", "rank", "content_id",
               "target_skill_id", "mastery_snapshot_id", "reason_code", "rationale", "predicted_success",
               "route_to", "engine_version", "outcome", "outcome_attempt_id", "outcome_at"]


def fill_outcomes(recs: pd.DataFrame, attempts: pd.DataFrame, ctx=None, hist=None) -> pd.DataFrame:
    """Outcomes are recorded after the 30-day window closes, from the attempts table."""
    hist = hist or History(attempts)
    recs = recs.copy()
    res = [outcome(ctx, hist, l, c, pd.Timestamp(t).value)
           for l, c, t in zip(recs["learner_id"], recs["content_id"], recs["as_of"])]
    recs["outcome"] = [o[0] for o in res]
    recs["outcome_attempt_id"] = [o[1] for o in res]
    recs["outcome_at"] = pd.to_datetime([o[2] for o in res])
    return recs


def outcome(ctx: Context, hist: History, learner: str, content, t: int) -> tuple:
    """First attempt of the recommended item within OUTCOME_WINDOW_DAYS after the decision."""
    if content is None or (not isinstance(content, str) and pd.isna(content)):
        return "NOT_APPLICABLE", None, None
    for r in hist.between(learner, t, t + C.OUTCOME_WINDOW_DAYS * NS_PER_DAY):
        if r.content == content:
            if r.status == "ABANDONED":
                return "ABANDONED", r.attempt_id, r.start
            if r.score is None:
                return "COMPLETED", r.attempt_id, r.start
            return ("PASSED" if r.score >= C.PASS_SCORE else "FAILED"), r.attempt_id, r.start
    return "NOT_TAKEN", None, None
