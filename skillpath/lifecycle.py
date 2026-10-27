"""Derived state: learning-path lifecycle and learner account status.

These are *derived* from attempts and assessment results by documented rules
(never generated at random), and the validator re-derives them to prove the
stored values reconcile.

Path lifecycle rules
  ENROLLED  when the path is created
  ACTIVE    at the first attempt (FIRST_ACTIVITY) or the first attempt after a stall (RESUMED)
  STALLED   60 days after the last activity if nothing happened in between
  COMPLETED when every skill of the course has at least one passed assessment
            in this path (later attempts are reviews and change nothing)
  WITHDRAWN 60 days after STALLED if the learner still has not come back
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C

NS_PER_DAY = 86_400 * 10**9


def completion_times(paths: pd.DataFrame, attempts: pd.DataFrame, results: pd.DataFrame,
                     skills: pd.DataFrame) -> pd.Series:
    """path_id -> time the last required skill was first passed (only completed paths)."""
    passed = results.loc[results["passed"], ["attempt_id", "skill_id", "assessed_at"]]
    passed = passed.merge(attempts[["attempt_id", "path_id"]], on="attempt_id")
    passed = passed.merge(skills[["skill_id", "course_id"]], on="skill_id")
    passed = passed.merge(paths[["path_id", "course_id"]].rename(columns={"course_id": "path_course"}),
                          on="path_id")
    passed = passed[passed["course_id"] == passed["path_course"]]
    first = passed.groupby(["path_id", "skill_id"])["assessed_at"].min().reset_index()
    per_path = first.groupby("path_id").agg(n=("skill_id", "size"), done_at=("assessed_at", "max"))
    needed = paths.set_index("path_id")["course_id"].map(skills.groupby("course_id").size())
    per_path = per_path.join(needed.rename("needed"))
    return per_path.loc[per_path["n"] == per_path["needed"], "done_at"]


def derive_path_events(paths: pd.DataFrame, attempts: pd.DataFrame, results: pd.DataFrame,
                       skills: pd.DataFrame, window_end=C.WINDOW_END) -> pd.DataFrame:
    """Rebuild the full status history of every path. Returns events without IDs."""
    stall = C.STALL_DAYS * NS_PER_DAY
    withdraw = C.WITHDRAW_AFTER_STALL_DAYS * NS_PER_DAY
    end = pd.Timestamp(window_end).value
    done = completion_times(paths, attempts, results, skills)
    done_ns = {p: t.value for p, t in done.items()}

    att = attempts[["path_id", "started_at"]].sort_values(["path_id", "started_at"], kind="mergesort")
    starts = {p: g.to_numpy(dtype="datetime64[ns]").astype(np.int64)
              for p, g in att.groupby("path_id", sort=False)["started_at"]}

    rows = []
    for pid, created in zip(paths["path_id"], paths["created_at"]):
        status = None

        def add(to, at, reason):
            nonlocal status
            rows.append((pid, status, to, at, reason))
            status = to

        add("ENROLLED", created.value, "ENROLLED")
        last = created.value
        completed_at = done_ns.get(pid)
        for t in starts.get(pid, ()):
            t = int(t)
            if completed_at is not None and t > completed_at:
                break  # reviews after completion do not change the lifecycle
            if t - last >= stall:
                add("STALLED", last + stall, "INACTIVITY_60D")
            if status in ("ENROLLED", "STALLED"):
                add("ACTIVE", t, "FIRST_ACTIVITY" if status == "ENROLLED" else "RESUMED")
            last = t
        if completed_at is not None:
            add("COMPLETED", completed_at, "ALL_SKILLS_PASSED")
        else:
            if end - last >= stall and status != "STALLED":
                add("STALLED", last + stall, "INACTIVITY_60D")
            if end - last >= stall + withdraw:
                add("WITHDRAWN", last + stall + withdraw, "NO_RETURN_AFTER_STALL")

    ev = pd.DataFrame(rows, columns=["path_id", "from_status", "to_status", "event_at", "reason_code"])
    ev["seq"] = np.arange(len(ev))
    ev["event_at"] = pd.to_datetime(ev["event_at"])
    ev = ev.sort_values(["path_id", "event_at", "seq"], kind="mergesort").drop(columns="seq")
    return ev.reset_index(drop=True)


def path_state(events: pd.DataFrame) -> pd.DataFrame:
    """Current status per path = its latest event."""
    last = events.groupby("path_id", sort=False).tail(1).set_index("path_id")
    state = pd.DataFrame({
        "current_status": last["to_status"],
        "status_updated_at": last["event_at"],
    })
    state["completed_at"] = state["status_updated_at"].where(state["current_status"] == "COMPLETED")
    return state


def status_as_of(events: pd.DataFrame, when) -> pd.Series:
    """path_id -> status from the latest event strictly before `when`."""
    ev = events[events["event_at"] < pd.Timestamp(when)]
    return ev.groupby("path_id", sort=False).tail(1).set_index("path_id")["to_status"]


def account_status(learners: pd.DataFrame, attempts: pd.DataFrame, window_end=C.WINDOW_END) -> pd.Series:
    last = attempts.groupby("learner_id")["started_at"].max()
    last = learners["learner_id"].map(last)
    cutoff = pd.Timestamp(window_end) - pd.Timedelta(days=C.DORMANT_DAYS)
    status = np.where(last.isna(), "NEW", np.where(last >= cutoff, "ACTIVE", "DORMANT"))
    return pd.Series(status, index=learners.index)
