"""Synthetic data generator (parents first, then children, then derived values).

    course -> skill -> skill_prerequisite -> content_item -> content_skill
    learner -> learning_path -> attempt (simulated over time)
    -> assessment_result, learning_path_event, learning_path state,
       learner.account_status  (all DERIVED from attempts by documented rules)

Everything comes from one seed. Each step gets its own child random generator
(`_rng(seed, step)`) so changing one step never shifts the numbers another
step sees. Internally times are integer minutes since WINDOW_START; they are
converted to timestamps only when the tables are built.

How attempts are simulated (the "hidden truth" lives in ground_truth/ only):
* each learner has a hidden ability and learning rate,
* each (learner, skill) has hidden knowledge that grows with practice,
* the learner mostly follows the syllabus, retries failed items, sometimes
  jumps ahead (this creates real prerequisite gaps),
* score = f(ability, knowledge of the skill, difficulty, prerequisite gap) + noise.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import config as C
from . import ids
from . import lifecycle
from .schema import ASSESSED_FORMATS, COURSE_LEVELS, DOMAINS, FORMATS, REGIONS

MIN_PER_DAY = 1440
END_MIN = int((C.WINDOW_END - C.WINDOW_START).total_seconds() // 60)
FORMAT_WEIGHTS = (0.25, 0.15, 0.25, 0.25, 0.10)
FORMAT_MINUTES = {"VIDEO": 12, "ARTICLE": 8, "QUIZ": 15, "EXERCISE": 35, "PROJECT": 120}
COURSE_LEVEL_WEIGHT = {"BEGINNER": 3.0, "INTERMEDIATE": 1.5, "ADVANCED": 0.7}


def _rng(seed: int, step: int) -> np.random.Generator:
    return np.random.default_rng([seed, step])


def to_ts(minutes) -> pd.Series:
    """Integer minutes since WINDOW_START -> timestamps (NaN stays NaT)."""
    return pd.Timestamp(C.WINDOW_START) + pd.to_timedelta(pd.Series(minutes, dtype="float64"), unit="min")


# ============================================================== catalog
@dataclass
class Catalog:
    courses: pd.DataFrame
    skills: pd.DataFrame
    prerequisites: pd.DataFrame
    content: pd.DataFrame
    content_skill: pd.DataFrame
    # fast index-based views used by the simulation
    skill_prereqs: list = field(default_factory=list)       # skill idx -> [prereq skill idx]
    course_items: list = field(default_factory=list)        # course idx -> [content idx] (syllabus order)
    c_primary: list = field(default_factory=list)
    c_secondary: list = field(default_factory=list)
    c_difficulty: list = field(default_factory=list)
    c_assessed: list = field(default_factory=list)
    c_minutes: list = field(default_factory=list)
    c_from: list = field(default_factory=list)              # available from (minutes); None = draft
    c_to: list = field(default_factory=list)                # available until (minutes); None = open


def build_catalog(scale: C.Scale, seed: int) -> Catalog:
    rng = _rng(seed, 1)
    n_dom = len(DOMAINS)
    track = {d: [i for i in range(scale.courses) if DOMAINS[i % n_dom] == d] for d in DOMAINS}
    level_of_skill_pos = [1 + (j * 4) // scale.skills_per_course for j in range(scale.skills_per_course)]
    items_per_skill = scale.content_per_course // scale.skills_per_course

    course_rows, skill_rows, prereq_rows, content_rows, cs_rows = [], [], [], [], []
    skill_course, skill_level, skill_prereqs = [], [], []
    course_skills: list[list[int]] = []
    course_launch: list[int] = []
    course_items: list[list[int]] = []
    c_primary, c_secondary, c_diff, c_assessed, c_minutes, c_from, c_to = [], [], [], [], [], [], []

    for ci in range(scale.courses):
        domain = DOMAINS[ci % n_dom]
        k = track[domain].index(ci)
        level = COURSE_LEVELS[min(2, (3 * k) // len(track[domain]))]
        launch = -int(rng.integers(30, 400)) * MIN_PER_DAY
        course_launch.append(launch)
        title = f"{domain.title()} {k + 1:02d}"
        course_rows.append(dict(course_id=ids.course(ci + 1), title=title, domain=domain,
                                level=level, launched_at=launch, status="ACTIVE"))

        # ---- skills and the prerequisite graph (edges only point to lower levels
        #      or to an earlier course of the same track, so the graph is a DAG)
        mine = []
        for j in range(scale.skills_per_course):
            si = len(skill_rows)
            lvl = level_of_skill_pos[j]
            skill_rows.append(dict(skill_id=ids.skill(si + 1), course_id=ids.course(ci + 1),
                                   name=f"{title} · L{lvl} · Topic {j + 1:02d}", level=lvl))
            skill_course.append(ci)
            skill_level.append(lvl)
            skill_prereqs.append([])
            mine.append(si)
        course_skills.append(mine)
        for si in mine:
            lvl = skill_level[si]
            chosen, cross = [], False
            if lvl > 1:
                pool = [s for s in mine if skill_level[s] == lvl - 1] or [s for s in mine if skill_level[s] < lvl]
                size = 1 if (rng.random() < 0.55 or len(pool) == 1) else 2
                chosen = [int(x) for x in rng.choice(pool, size=size, replace=False)]
            elif k > 0 and rng.random() < 0.5:
                prev = course_skills[track[domain][k - 1]]
                top = max(skill_level[s] for s in prev)
                chosen = [int(rng.choice([s for s in prev if skill_level[s] == top]))]
                cross = True
            for p in sorted(chosen):
                skill_prereqs[si].append(p)
                prereq_rows.append(dict(skill_id=ids.skill(si + 1), prerequisite_skill_id=ids.skill(p + 1),
                                        min_mastery=C.PREREQ_MIN_MASTERY, effective_from=launch,
                                        is_cross_course=cross))

        # ---- content: items_per_skill per skill, the first one is always a published quiz
        items = []
        for si in mine:
            lvl = skill_level[si]
            for j in range(items_per_skill):
                idx = len(content_rows)
                fmt = "QUIZ" if j == 0 else FORMATS[int(rng.choice(len(FORMATS), p=FORMAT_WEIGHTS))]
                diff = int(np.clip(lvl + round(float(rng.normal(0, 0.7))), 1, 5))
                minutes = max(3, int(round(FORMAT_MINUTES[fmt] * float(rng.lognormal(0, 0.25)))))
                r, late = rng.random(), rng.random()
                pub_days = rng.integers(0, 60)
                late_days = rng.integers(0, 480)
                retire_days = rng.integers(150, 450)
                status, pub, ret = "PUBLISHED", launch + int(pub_days) * MIN_PER_DAY, None
                if j > 0:
                    if late < 0.15:
                        pub = int(late_days) * MIN_PER_DAY  # content added during the window
                    if r < 0.04:
                        status, pub = "DRAFT", None
                    elif r < 0.08 and pub + int(retire_days) * MIN_PER_DAY < END_MIN:
                        status, ret = "RETIRED", pub + int(retire_days) * MIN_PER_DAY
                content_rows.append(dict(
                    content_id=ids.content(idx + 1), course_id=ids.course(ci + 1),
                    title=f"{skill_rows[si]['name']} · {fmt.title()} {j + 1}", format=fmt,
                    difficulty=diff, duration_min=minutes, is_assessed=fmt in ASSESSED_FORMATS,
                    status=status, published_at=pub, retired_at=ret))
                # primary skill + optional secondary (a same-course prerequisite or sibling)
                cs_rows.append(dict(content_id=ids.content(idx + 1), skill_id=ids.skill(si + 1),
                                    role="PRIMARY", weight=1.0))
                sec = -1
                if rng.random() < 0.35:
                    pool = [p for p in skill_prereqs[si] if skill_course[p] == ci] or \
                           [s for s in mine if s != si and skill_level[s] == lvl]
                    if pool:
                        sec = int(rng.choice(pool))
                        cs_rows.append(dict(content_id=ids.content(idx + 1), skill_id=ids.skill(sec + 1),
                                            role="SECONDARY", weight=0.5))
                c_primary.append(si)
                c_secondary.append(sec)
                c_diff.append(diff)
                c_assessed.append(fmt in ASSESSED_FORMATS)
                c_minutes.append(minutes)
                c_from.append(pub)
                c_to.append(ret)
                items.append(idx)
        course_items.append(items)

    courses = pd.DataFrame(course_rows)
    courses["launched_at"] = to_ts(courses["launched_at"])
    prereqs = pd.DataFrame(prereq_rows)
    prereqs["effective_from"] = to_ts(prereqs["effective_from"])
    content = pd.DataFrame(content_rows)
    content["published_at"] = to_ts(content["published_at"])
    content["retired_at"] = to_ts(content["retired_at"])
    return Catalog(courses, pd.DataFrame(skill_rows), prereqs, content, pd.DataFrame(cs_rows),
                   skill_prereqs, course_items, c_primary, c_secondary, c_diff, c_assessed,
                   c_minutes, c_from, c_to)


# ============================================================== learners & paths
def build_learners(scale: C.Scale, seed: int):
    rng = _rng(seed, 2)
    n = scale.learners
    signup = (rng.beta(1.2, 1.5, n) * (END_MIN - 45 * MIN_PER_DAY)).astype(np.int64)
    truth = pd.DataFrame({
        "learner_id": [ids.learner(i + 1) for i in range(n)],
        "ability": rng.normal(0, 1, n).round(4),
        "learning_rate": rng.uniform(0.08, 0.20, n).round(4),
        "cold_start": rng.random(n) < C.COLD_START_SHARE,
    })
    learners = pd.DataFrame({
        "learner_id": truth["learner_id"],
        "handle": [f"learner_{i + 1:06d}" for i in range(n)],
        "signup_at": signup,
        "region": rng.choice(REGIONS, n),
        "preferred_format": rng.choice(FORMATS, n),
    })
    return learners, truth


def build_paths(learners: pd.DataFrame, cat: Catalog, seed: int) -> pd.DataFrame:
    rng = _rng(seed, 3)
    levels = cat.courses["level"].to_numpy()
    domains = cat.courses["domain"].to_numpy()
    base_w = np.array([COURSE_LEVEL_WEIGHT[l] for l in levels])
    n_courses = len(levels)
    rows = []
    for li, signup in enumerate(learners["signup_at"].to_numpy()):
        n_paths = 1 + int(rng.choice(3, p=[0.5, 0.33, 0.17]))
        t = int(signup) + int(rng.integers(0, 3 * MIN_PER_DAY))
        chosen: list[int] = []
        for p in range(n_paths):
            follow = rng.random() < 0.6
            pick = None
            if p > 0 and follow:  # continue the same track if possible
                same = [c for c in range(chosen[-1] + 1, n_courses) if domains[c] == domains[chosen[-1]]]
                if same and same[0] not in chosen:
                    pick = same[0]
            if pick is None:
                w = base_w.copy()
                w[chosen] = 0
                pick = int(rng.choice(n_courses, p=w / w.sum()))
            if t >= END_MIN - MIN_PER_DAY:
                break
            rows.append((li, pick, t))
            chosen.append(pick)
            t += int(rng.integers(10, 150)) * MIN_PER_DAY + int(rng.integers(0, MIN_PER_DAY))
    paths = pd.DataFrame(rows, columns=["learner_idx", "course_idx", "created_min"])
    paths = paths.sort_values(["created_min", "learner_idx", "course_idx"], kind="mergesort").reset_index(drop=True)
    years = to_ts(paths["created_min"]).dt.year
    paths["path_id"] = [ids.path(int(y), i + 1) for i, y in enumerate(years)]
    return paths


# ============================================================== attempt simulation
def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def simulate_attempts(scale: C.Scale, cat: Catalog, learners: pd.DataFrame, truth: pd.DataFrame,
                      paths: pd.DataFrame, seed: int):
    rng = _rng(seed, 4)
    n = len(learners)
    first_path = paths.groupby("learner_idx")["created_min"].min().reindex(range(n)).to_numpy()
    avail_days = np.maximum((END_MIN - first_path) / MIN_PER_DAY, 1.0)
    weight = rng.lognormal(0, 0.7, n) * avail_days
    weight[truth["cold_start"].to_numpy()] = 0.0
    counts = rng.multinomial(scale.attempts, weight / weight.sum())

    paths_by_learner: dict[int, list] = {}
    for pi, (li, ci, t) in enumerate(zip(paths["learner_idx"], paths["course_idx"], paths["created_min"])):
        paths_by_learner.setdefault(int(li), []).append((int(t), pi, int(ci)))

    ability = truth["ability"].to_numpy()
    lrate = truth["learning_rate"].to_numpy()
    sim = _rng(seed, 5)
    records = []        # (learner_idx, content_idx, path_idx, start, end, completed, score, duration)
    latent = []         # (learner_idx, skill_idx, knowledge at the end)
    prereqs, items_of = cat.skill_prereqs, cat.course_items
    prim, sec, diff, assessed = cat.c_primary, cat.c_secondary, cat.c_difficulty, cat.c_assessed
    minutes, c_from, c_to = cat.c_minutes, cat.c_from, cat.c_to

    for li in range(n):
        k_att = int(counts[li])
        if k_att == 0:
            continue
        my_paths = sorted(paths_by_learner[li])
        a, lr = float(ability[li]), float(lrate[li])
        base = min(max(0.10 + 0.08 * a, 0.0), 0.35)
        know: dict[int, float] = {}
        state = {pi: dict(done=set(), skipped=set(), fails={}, last=None, last_failed=False)
                 for _, pi, _ in my_paths}
        tries: dict[int, int] = {}

        u = sim.random((k_att, 6))
        z = sim.normal(size=(k_att, 2))
        gaps = sim.exponential(1.0, k_att)
        t0 = my_paths[0][0] + int(sim.integers(30, MIN_PER_DAY))
        span = (END_MIN - 600 - t0) * float(sim.uniform(0.2, 0.8))
        times = t0 + np.cumsum(gaps) / gaps.sum() * span
        prev_end = t0

        for i in range(k_att):
            t = max(int(times[i]), prev_end + 5)
            if t > END_MIN - 300:
                break
            active = [(ct, pi, ci) for ct, pi, ci in my_paths if ct <= t]
            if len(active) > 1 and u[i, 0] > 0.7:
                _, pi, ci = active[int(u[i, 1] * len(active))]
            else:
                _, pi, ci = active[-1]
            st = state[pi]
            avail = [c for c in items_of[ci]
                     if c_from[c] is not None and c_from[c] <= t and (c_to[c] is None or c_to[c] > t)]
            if not avail:
                continue

            # ---- behaviour policy: retry a failure, jump ahead, or follow the syllabus
            if st["last"] in avail and st["last_failed"] and st["fails"].get(st["last"], 0) < 2 and u[i, 2] < 0.45:
                c = st["last"]
            elif u[i, 2] > 0.92:
                c = avail[int(u[i, 3] * len(avail))]
            else:
                todo = [x for x in avail if x not in st["done"] and x not in st["skipped"]]
                if not todo:
                    st["skipped"].clear()
                    todo = [x for x in avail if x not in st["done"]]
                if todo and u[i, 3] < 0.90:
                    c = todo[0]
                else:  # review / wander
                    c = avail[int(u[i, 4] * len(avail))]

            tries[c] = tries.get(c, 0) + 1
            s = prim[c]
            ks = know.get(s, base)
            reqs = prereqs[s]
            gap = sum(1 for p in reqs if know.get(p, base) < 0.5) / len(reqs) if reqs else 0.0
            dur = max(1, int(round(minutes[c] * math.exp(0.3 * z[i, 1]))))
            abandon_p = 0.04 + (0.06 if diff[c] >= 4 else 0.0) + (0.05 if gap > 0.5 else 0.0)
            if u[i, 5] < abandon_p:
                dur = max(1, int(dur * 0.4))
                records.append((li, c, pi, t, -1, False, -1, dur))
                prev_end = t + dur
                continue

            if assessed[c]:
                logit = (1.3 + 0.6 * a + 3.5 * (ks - 0.3) - 0.5 * (diff[c] - 2.5) - 1.5 * gap
                         + 0.2 * (tries[c] - 1))
                score = int(round(min(100.0, max(0.0, 100.0 * _sigmoid(logit) + 7.0 * z[i, 0]))))
                passed = score >= C.PASS_SCORE
                gain = lr * (1.0 - ks) * (0.5 + 0.5 * score / 100.0) * (1.0 - 0.6 * gap)
                if passed:
                    st["done"].add(c)
                else:
                    st["fails"][c] = st["fails"].get(c, 0) + 1
                    if st["fails"][c] >= 2:
                        st["skipped"].add(c)
                st["last_failed"] = not passed
            else:
                score = -1
                gain = lr * 0.6 * (1.0 - ks) * (1.0 - 0.6 * gap)
                st["done"].add(c)
                st["last_failed"] = False
            st["last"] = c
            know[s] = min(1.0, ks + gain)
            if sec[c] >= 0:
                know[sec[c]] = min(1.0, know.get(sec[c], base) + 0.5 * gain)
            records.append((li, c, pi, t, t + dur, True, score, dur))
            prev_end = t + dur
        latent.extend((li, s, round(v, 4)) for s, v in know.items())

    return records, latent


# ============================================================== assemble tables
def generate(scale: C.Scale, seed: int = C.DEFAULT_SEED) -> tuple[dict, dict]:
    """Return (tables, truth). `truth` is hidden state that must never be used as input."""
    cat = build_catalog(scale, seed)
    learners, truth = build_learners(scale, seed)
    paths = build_paths(learners, cat, seed)
    records, latent = simulate_attempts(scale, cat, learners, truth, paths, seed)

    learner_ids = learners["learner_id"].to_numpy()
    content_ids = cat.content["content_id"].to_numpy()
    course_ids = cat.courses["course_id"].to_numpy()
    skill_ids = cat.skills["skill_id"].to_numpy()

    # ---- attempts
    r = pd.DataFrame(records, columns=["li", "ci", "pi", "start", "end", "completed", "score", "dur"])
    r = r.sort_values(["li", "start"], kind="mergesort").reset_index(drop=True)
    attempts = pd.DataFrame({
        "learner_id": learner_ids[r["li"]],
        "content_id": content_ids[r["ci"]],
        "path_id": paths["path_id"].to_numpy()[r["pi"]],
        "status": np.where(r["completed"], "COMPLETED", "ABANDONED"),
        "started_at": to_ts(r["start"]),
        "completed_at": to_ts(r["end"].where(r["end"] >= 0)),
        "score": r["score"].where(r["score"] >= 0).astype("Int64"),
        "duration_min": r["dur"].astype("int64"),
    })
    counter = attempts.groupby("learner_id", sort=False).cumcount() + 1
    attempts.insert(0, "attempt_id", [ids.attempt(l, n) for l, n in zip(attempts["learner_id"], counter)])
    attempts.insert(4, "attempt_no", (attempts.groupby(["learner_id", "content_id"]).cumcount() + 1).astype("int64"))

    # ---- assessment results: derived 1..2 rows per completed assessed attempt
    results = derive_assessment_results(attempts, cat.content_skill)

    # ---- learning paths + append-only event history (derived)
    path_tbl = pd.DataFrame({
        "path_id": paths["path_id"],
        "learner_id": learner_ids[paths["learner_idx"]],
        "course_id": course_ids[paths["course_idx"]],
        "created_at": to_ts(paths["created_min"]),
    })
    events = lifecycle.derive_path_events(path_tbl, attempts, results, cat.skills)
    events.insert(0, "event_id", [ids.seq("PEV", i + 1) for i in range(len(events))])
    path_tbl = path_tbl.join(lifecycle.path_state(events), on="path_id")

    # ---- learners (account status derived from attempts)
    learners = learners.copy()
    learners["signup_at"] = to_ts(learners["signup_at"])
    learners["account_status"] = lifecycle.account_status(learners, attempts)

    tables = {
        "course": cat.courses, "skill": cat.skills, "skill_prerequisite": cat.prerequisites,
        "content_item": cat.content, "content_skill": cat.content_skill, "learner": learners,
        "learning_path": path_tbl, "learning_path_event": events, "attempt": attempts,
        "assessment_result": results,
    }
    n_att = attempts.groupby("learner_id").size()
    truth = truth.copy()
    truth["attempts"] = truth["learner_id"].map(n_att).fillna(0).astype(int)
    truth["segment"] = np.select(
        [truth["attempts"] == 0, truth["attempts"] < 10, truth["attempts"] < 100],
        ["COLD_START", "LIGHT", "REGULAR"], "HEAVY")
    lat = pd.DataFrame(latent, columns=["li", "si", "latent_knowledge"])
    latent_df = pd.DataFrame({"learner_id": learner_ids[lat["li"]], "skill_id": skill_ids[lat["si"]],
                              "latent_knowledge": lat["latent_knowledge"]})
    latent_df = latent_df.sort_values(["learner_id", "skill_id"]).reset_index(drop=True)
    truth_tables = {
        "learner_truth": truth.drop(columns="cold_start")[["learner_id", "ability", "learning_rate", "attempts", "segment"]],
        "latent_skill_state": latent_df,
    }
    return tables, truth_tables


def derive_assessment_results(attempts: pd.DataFrame, content_skill: pd.DataFrame) -> pd.DataFrame:
    scored = attempts.loc[attempts["score"].notna(), ["attempt_id", "learner_id", "content_id", "score", "completed_at"]]
    res = scored.merge(content_skill, on="content_id", how="inner")
    res = res.sort_values(["attempt_id", "role"], kind="mergesort")
    suffix = np.where(res["role"] == "PRIMARY", "1", "2")
    res["result_id"] = "ASR-" + res["attempt_id"].str[4:] + "-" + suffix
    out = pd.DataFrame({
        "result_id": res["result_id"].to_numpy(),
        "attempt_id": res["attempt_id"].to_numpy(),
        "learner_id": res["learner_id"].to_numpy(),
        "skill_id": res["skill_id"].to_numpy(),
        "skill_weight": res["weight"].to_numpy(dtype=float),
        "score": res["score"].astype("int64").to_numpy(),
        "passed": (res["score"] >= C.PASS_SCORE).to_numpy(dtype=bool),
        "assessed_at": res["completed_at"].to_numpy(),
    })
    return out.sort_values("result_id").reset_index(drop=True)
