"""Single source of truth for the data model.

Every table, field, type, allowed value and validation rule is declared here.
The validator, the SQL DDL and docs/DATA_DICTIONARY.md are all generated from
these definitions, so documentation can never drift away from the code.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .ids import PATTERNS

# ------------------------------------------------------------------ enums
DOMAINS = ("PROGRAMMING", "DATA", "CLOUD", "SECURITY", "DESIGN")
COURSE_LEVELS = ("BEGINNER", "INTERMEDIATE", "ADVANCED")
FORMATS = ("VIDEO", "ARTICLE", "QUIZ", "EXERCISE", "PROJECT")
ASSESSED_FORMATS = ("QUIZ", "EXERCISE", "PROJECT")
REGIONS = ("IN-KA", "IN-MH", "IN-TN", "IN-TS", "IN-DL", "IN-WB")
ACCOUNT_STATUS = ("NEW", "ACTIVE", "DORMANT")
CONTENT_STATUS = ("DRAFT", "PUBLISHED", "RETIRED")
SKILL_ROLES = ("PRIMARY", "SECONDARY")
ATTEMPT_STATUS = ("COMPLETED", "ABANDONED")
PATH_STATUS = ("ENROLLED", "ACTIVE", "STALLED", "COMPLETED", "WITHDRAWN")
PATH_EVENT_REASONS = (
    "ENROLLED", "FIRST_ACTIVITY", "INACTIVITY_60D", "RESUMED",
    "ALL_SKILLS_PASSED", "NO_RETURN_AFTER_STALL",
)
# (from_status, to_status) pairs a learning path is allowed to make.
PATH_TRANSITIONS = {
    (None, "ENROLLED"), ("ENROLLED", "ACTIVE"), ("ENROLLED", "STALLED"),
    ("ACTIVE", "STALLED"), ("STALLED", "ACTIVE"), ("ACTIVE", "COMPLETED"),
    ("STALLED", "WITHDRAWN"),
}
OPEN_PATH_STATUS = ("ENROLLED", "ACTIVE", "STALLED")

REASON_CODES = (
    "COLD_START_ENTRY",           # no assessment evidence yet -> easy entry items
    "NEXT_FRONTIER_SKILL",        # prerequisites met, skill not yet learned
    "REINFORCE_NEAR_MASTERY",     # 0.60 <= mastery < 0.70
    "REMEDIATE_RECENT_FAIL",      # last assessed attempt on this skill failed
    "STRETCH_CHALLENGE",          # skill mastered, harder unpassed item left
    "REMEDIATE_PREREQUISITE",     # every course item blocked -> teach the blocking prerequisite
    "ESCALATE_NO_ELIGIBLE_CONTENT",   # exception: nothing eligible, even after remediation search
    "ESCALATE_LOOP_NO_PROGRESS",      # exception: learner is looping without progress
    "ESCALATE_NO_REMAINING_CONTENT",  # exception: nothing left to recommend in the path
)
EXCEPTION_REASONS = tuple(r for r in REASON_CODES if r.startswith("ESCALATE_"))
ROUTES = ("LEARNER", "INSTRUCTOR")
OUTCOMES = ("PASSED", "FAILED", "COMPLETED", "ABANDONED", "NOT_TAKEN", "NOT_APPLICABLE")

# Issue types the validator can raise. The first five are the deliberately
# injected edge cases required by the brief; the rest are generic integrity
# checks that must stay at zero on a clean dataset.
INJECTED_ISSUE_TYPES = (
    "REC_VIOLATES_PREREQUISITE",
    "ATTEMPT_TIMESTAMP_ORDER_INVALID",
    "CONTENT_MAPPED_TO_MISSING_SKILL",
    "MASTERY_USES_FUTURE_ATTEMPT",
    "PATH_LOOP_NO_PROGRESS",
)


@dataclass(frozen=True)
class Field:
    name: str
    type: str                 # string | integer | float | boolean | datetime
    required: bool
    description: str
    allowed: tuple = ()
    unit: str = ""
    unique: bool = False
    pattern: str = ""
    fk: str = ""              # "table.column"
    min: float | None = None
    max: float | None = None
    rule: str = ""            # extra rule, human readable (checked in validate.py)


@dataclass(frozen=True)
class Table:
    name: str
    entity: str
    description: str
    primary_key: tuple
    fields: tuple
    rules: tuple = field(default_factory=tuple)

    def get(self, name: str) -> Field:
        return next(f for f in self.fields if f.name == name)


F = Field  # short alias for the declarations below

TABLES: dict[str, Table] = {}


def _t(table: Table) -> None:
    TABLES[table.name] = table


# ------------------------------------------------------------------ master data
_t(Table(
    "course", "Course", "A course groups related skills and content.",
    ("course_id",),
    (
        F("course_id", "string", True, "Course ID", unique=True, pattern=PATTERNS["course"]),
        F("title", "string", True, "Course title", unique=True),
        F("domain", "string", True, "Subject domain", allowed=DOMAINS),
        F("level", "string", True, "Difficulty band of the course", allowed=COURSE_LEVELS),
        F("launched_at", "datetime", True, "When the course went live"),
        F("status", "string", True, "Lifecycle state", allowed=("ACTIVE", "ARCHIVED")),
    ),
))
_t(Table(
    "skill", "Skill", "A unit of competence. Each skill belongs to exactly one course.",
    ("skill_id",),
    (
        F("skill_id", "string", True, "Skill ID", unique=True, pattern=PATTERNS["skill"]),
        F("course_id", "string", True, "Owning course", fk="course.course_id"),
        F("name", "string", True, "Skill name", unique=True),
        F("level", "integer", True, "Depth of the skill inside its course (1 = entry)", min=1, max=4),
    ),
))
_t(Table(
    "skill_prerequisite", "SkillPrerequisite",
    "Bridge table forming the skill graph (N:M self relation on Skill). "
    "A row means: skill_id requires prerequisite_skill_id at mastery >= min_mastery.",
    ("skill_id", "prerequisite_skill_id"),
    (
        F("skill_id", "string", True, "Dependent skill", fk="skill.skill_id"),
        F("prerequisite_skill_id", "string", True, "Required skill", fk="skill.skill_id",
          rule="!= skill_id"),
        F("min_mastery", "float", True, "Mastery needed on the prerequisite", min=0, max=1),
        F("effective_from", "datetime", True, "Rule applies to decisions on/after this time"),
        F("is_cross_course", "boolean", True, "True when the prerequisite lives in another course",
          rule="== (course of prerequisite != course of skill)"),
    ),
    rules=("The prerequisite graph must be acyclic (checked with Kahn's algorithm).",),
))
_t(Table(
    "content_item", "ContentItem", "A learning activity: video, article, quiz, exercise or project.",
    ("content_id",),
    (
        F("content_id", "string", True, "Content ID", unique=True, pattern=PATTERNS["content"]),
        F("course_id", "string", True, "Owning course", fk="course.course_id"),
        F("title", "string", True, "Content title"),
        F("format", "string", True, "Content format", allowed=FORMATS),
        F("difficulty", "integer", True, "Difficulty 1 (easy) .. 5 (hard)", min=1, max=5),
        F("duration_min", "integer", True, "Expected time to finish", unit="minutes", min=1),
        F("is_assessed", "boolean", True, "Produces a score",
          rule="== format in (QUIZ, EXERCISE, PROJECT)"),
        F("status", "string", True, "Lifecycle state", allowed=CONTENT_STATUS),
        F("published_at", "datetime", False, "When it became available",
          rule="null iff status = DRAFT"),
        F("retired_at", "datetime", False, "When it stopped being available",
          rule="not null iff status = RETIRED; > published_at"),
    ),
))
_t(Table(
    "content_skill", "ContentSkill",
    "Bridge table mapping content to skills (N:M). Exactly one PRIMARY skill per content item.",
    ("content_id", "skill_id"),
    (
        F("content_id", "string", True, "Content item", fk="content_item.content_id"),
        F("skill_id", "string", True, "Skill taught/assessed", fk="skill.skill_id"),
        F("role", "string", True, "PRIMARY or SECONDARY", allowed=SKILL_ROLES),
        F("weight", "float", True, "Evidence weight (PRIMARY 1.0, SECONDARY 0.5)", min=0, max=1),
    ),
))
_t(Table(
    "learner", "Learner", "A pseudonymous learner account (no personal data).",
    ("learner_id",),
    (
        F("learner_id", "string", True, "Learner ID", unique=True, pattern=PATTERNS["learner"]),
        F("handle", "string", True, "Pseudonymous display handle", unique=True),
        F("signup_at", "datetime", True, "Account creation time"),
        F("region", "string", True, "Coarse region code", allowed=REGIONS),
        F("preferred_format", "string", True, "Self-declared favourite format", allowed=FORMATS),
        F("account_status", "string", True, "Derived from attempts", allowed=ACCOUNT_STATUS,
          rule="NEW if no attempts; ACTIVE if last attempt within 90 days of window end; else DORMANT"),
    ),
))

# ------------------------------------------------------------------ transactions / events
_t(Table(
    "learning_path", "LearningPath", "A learner's enrolment in one course. Current state is "
    "derived from learning_path_event (history is never overwritten).",
    ("path_id",),
    (
        F("path_id", "string", True, "Path ID", unique=True, pattern=PATTERNS["path"]),
        F("learner_id", "string", True, "Learner", fk="learner.learner_id"),
        F("course_id", "string", True, "Course", fk="course.course_id"),
        F("created_at", "datetime", True, "Enrolment time", rule=">= learner.signup_at"),
        F("current_status", "string", True, "Latest status", allowed=PATH_STATUS,
          rule="== to_status of the latest learning_path_event"),
        F("status_updated_at", "datetime", True, "Time of latest event"),
        F("completed_at", "datetime", False, "Completion time", rule="not null iff COMPLETED"),
    ),
    rules=("(learner_id, course_id) is unique.",),
))
_t(Table(
    "learning_path_event", "LearningPathEvent", "Append-only status history of a learning path.",
    ("event_id",),
    (
        F("event_id", "string", True, "Event ID", unique=True, pattern=PATTERNS["path_event"]),
        F("path_id", "string", True, "Path", fk="learning_path.path_id"),
        F("from_status", "string", False, "Status before (null for the first event)",
          allowed=PATH_STATUS),
        F("to_status", "string", True, "Status after", allowed=PATH_STATUS),
        F("event_at", "datetime", True, "When the change happened"),
        F("reason_code", "string", True, "Why it changed", allowed=PATH_EVENT_REASONS),
    ),
    rules=("(from_status, to_status) must be an allowed transition.",
           "Events of a path are re-derived from attempts and must match exactly."),
))
_t(Table(
    "attempt", "Attempt", "One learner interaction with one content item.",
    ("attempt_id",),
    (
        F("attempt_id", "string", True, "ATT-<learner-id>-<n>, n follows the learner's chronology",
          unique=True, pattern=PATTERNS["attempt"]),
        F("learner_id", "string", True, "Learner", fk="learner.learner_id"),
        F("content_id", "string", True, "Content attempted", fk="content_item.content_id"),
        F("path_id", "string", True, "Learning path the attempt belongs to",
          fk="learning_path.path_id", rule="path course == content course"),
        F("attempt_no", "integer", True, "n-th attempt of this learner on this content", min=1),
        F("status", "string", True, "COMPLETED or ABANDONED", allowed=ATTEMPT_STATUS),
        F("started_at", "datetime", True, "Start time", rule=">= learner.signup_at and path.created_at"),
        F("completed_at", "datetime", False, "End time",
          rule="null iff ABANDONED; >= started_at"),
        F("score", "integer", False, "Score for assessed content", unit="points", min=0, max=100,
          rule="not null iff COMPLETED and content is assessed"),
        F("duration_min", "integer", True, "Time spent", unit="minutes", min=1),
    ),
))
_t(Table(
    "assessment_result", "AssessmentResult",
    "Per-skill evidence derived from a completed assessed attempt (1 attempt : 1..2 results).",
    ("result_id",),
    (
        F("result_id", "string", True, "<attempt-id with ASR prefix>-<1 primary | 2 secondary>",
          unique=True, pattern=PATTERNS["result"]),
        F("attempt_id", "string", True, "Source attempt", fk="attempt.attempt_id"),
        F("learner_id", "string", True, "Learner (== attempt learner)", fk="learner.learner_id"),
        F("skill_id", "string", True, "Skill evidenced", fk="skill.skill_id"),
        F("skill_weight", "float", True, "Weight copied from content_skill", min=0, max=1),
        F("score", "integer", True, "== attempt.score", unit="points", min=0, max=100),
        F("passed", "boolean", True, "score >= 70"),
        F("assessed_at", "datetime", True, "== attempt.completed_at"),
    ),
))
_t(Table(
    "mastery_snapshot", "MasterySnapshot",
    "Mastery estimate used by a recommendation decision, stored for traceability.",
    ("snapshot_id",),
    (
        F("snapshot_id", "string", True, "Snapshot ID", unique=True, pattern=PATTERNS["snapshot"]),
        F("learner_id", "string", True, "Learner", fk="learner.learner_id"),
        F("skill_id", "string", True, "Skill", fk="skill.skill_id"),
        F("as_of", "datetime", True, "Decision time; only evidence strictly before it may be used"),
        F("mastery", "float", True, "Recency-weighted mastery estimate", min=0, max=1,
          rule="== recomputation from assessment_result before as_of"),
        F("evidence_count", "integer", True, "Results used", min=0),
        F("last_evidence_at", "datetime", False, "Newest evidence used", rule="< as_of"),
        F("method_version", "string", True, "Mastery method version"),
    ),
    rules=("(learner_id, skill_id, as_of) is unique.",),
))
_t(Table(
    "recommendation", "Recommendation",
    "Ranked next-activity recommendations (or an exception routed to an instructor) per decision.",
    ("recommendation_id",),
    (
        F("recommendation_id", "string", True, "Recommendation ID", unique=True,
          pattern=PATTERNS["recommendation"]),
        F("decision_id", "string", True, "Groups the ranked items of one decision",
          pattern=PATTERNS["decision"]),
        F("learner_id", "string", True, "Learner", fk="learner.learner_id"),
        F("path_id", "string", True, "Path the decision was made for", fk="learning_path.path_id"),
        F("as_of", "datetime", True, "Decision time"),
        F("rank", "integer", True, "1 = best", min=1, max=3),
        F("content_id", "string", False, "Recommended item (null for exceptions)",
          fk="content_item.content_id", rule="all prerequisites met at as_of"),
        F("target_skill_id", "string", False, "Skill the item works on / blocking skill",
          fk="skill.skill_id"),
        F("mastery_snapshot_id", "string", False, "Mastery of target skill at decision time",
          fk="mastery_snapshot.snapshot_id"),
        F("reason_code", "string", True, "Why it was recommended", allowed=REASON_CODES),
        F("rationale", "string", True, "Human-readable explanation"),
        F("predicted_success", "float", False, "Estimated pass probability", min=0, max=1),
        F("route_to", "string", True, "Who acts on it", allowed=ROUTES),
        F("engine_version", "string", True, "Recommender version"),
        F("outcome", "string", True, "What happened within 30 days", allowed=OUTCOMES),
        F("outcome_attempt_id", "string", False, "Attempt that produced the outcome",
          fk="attempt.attempt_id"),
        F("outcome_at", "datetime", False, "Start of that attempt"),
    ),
))

TABLE_ORDER = tuple(TABLES)  # parents before children

RELATIONSHIPS = (
    # parent, child, cardinality, optional?
    ("course", "skill", "1:N", "every skill has one course"),
    ("course", "content_item", "1:N", "every item has one course"),
    ("skill", "skill", "N:M via skill_prerequisite", "skills may have 0..2 prerequisites"),
    ("content_item", "skill", "N:M via content_skill", "exactly 1 primary, 0..1 secondary"),
    ("learner", "learning_path", "1:N", "a learner has 1..3 paths"),
    ("course", "learning_path", "1:N", "optional: a course may have no learners"),
    ("learning_path", "learning_path_event", "1:N", "at least one event (ENROLLED)"),
    ("learning_path", "attempt", "1:N", "optional: cold-start paths have none"),
    ("learner", "attempt", "1:N", "optional"),
    ("content_item", "attempt", "1:N", "optional: drafts never have attempts"),
    ("attempt", "assessment_result", "1:N (0..2)", "only completed assessed attempts"),
    ("learner", "mastery_snapshot", "1:N", "only for skills used in a decision"),
    ("learning_path", "recommendation", "1:N", "per decision date while the path is open"),
    ("mastery_snapshot", "recommendation", "1:N", "optional (null for exceptions)"),
    ("attempt", "recommendation", "1:0..1 via outcome_attempt_id", "optional"),
)
