# Data dictionary

_Generated from `skillpath/schema.py` by `python -m skillpath docs` — do not edit by hand._

## Relationships

| Parent | Child | Cardinality | Notes |
|---|---|---|---|
| course | skill | 1:N | every skill has one course |
| course | content_item | 1:N | every item has one course |
| skill | skill | N:M via skill_prerequisite | skills may have 0..2 prerequisites |
| content_item | skill | N:M via content_skill | exactly 1 primary, 0..1 secondary |
| learner | learning_path | 1:N | a learner has 1..3 paths |
| course | learning_path | 1:N | optional: a course may have no learners |
| learning_path | learning_path_event | 1:N | at least one event (ENROLLED) |
| learning_path | attempt | 1:N | optional: cold-start paths have none |
| learner | attempt | 1:N | optional |
| content_item | attempt | 1:N | optional: drafts never have attempts |
| attempt | assessment_result | 1:N (0..2) | only completed assessed attempts |
| learner | mastery_snapshot | 1:N | only for skills used in a decision |
| learning_path | recommendation | 1:N | per decision date while the path is open |
| mastery_snapshot | recommendation | 1:N | optional (null for exceptions) |
| attempt | recommendation | 1:0..1 via outcome_attempt_id | optional |

## `course` (Course)

A course groups related skills and content.

Primary key: `course_id`

| Field | Type | Req. | Allowed / range | Unit | Unique | FK | Validation rule | Description |
|---|---|---|---|---|---|---|---|---|
| `course_id` | string | yes |  |  | yes |  | pattern `^CRS-\d{6}$` | Course ID |
| `title` | string | yes |  |  | yes |  |  | Course title |
| `domain` | string | yes | PROGRAMMING, DATA, CLOUD, SECURITY, DESIGN |  |  |  |  | Subject domain |
| `level` | string | yes | BEGINNER, INTERMEDIATE, ADVANCED |  |  |  |  | Difficulty band of the course |
| `launched_at` | datetime | yes |  |  |  |  |  | When the course went live |
| `status` | string | yes | ACTIVE, ARCHIVED |  |  |  |  | Lifecycle state |

## `skill` (Skill)

A unit of competence. Each skill belongs to exactly one course.

Primary key: `skill_id`

| Field | Type | Req. | Allowed / range | Unit | Unique | FK | Validation rule | Description |
|---|---|---|---|---|---|---|---|---|
| `skill_id` | string | yes |  |  | yes |  | pattern `^SKL-\d{6}$` | Skill ID |
| `course_id` | string | yes |  |  |  | course.course_id |  | Owning course |
| `name` | string | yes |  |  | yes |  |  | Skill name |
| `level` | integer | yes | 1 .. 4 |  |  |  |  | Depth of the skill inside its course (1 = entry) |

## `skill_prerequisite` (SkillPrerequisite)

Bridge table forming the skill graph (N:M self relation on Skill). A row means: skill_id requires prerequisite_skill_id at mastery >= min_mastery.

Primary key: `skill_id, prerequisite_skill_id`

| Field | Type | Req. | Allowed / range | Unit | Unique | FK | Validation rule | Description |
|---|---|---|---|---|---|---|---|---|
| `skill_id` | string | yes |  |  |  | skill.skill_id |  | Dependent skill |
| `prerequisite_skill_id` | string | yes |  |  |  | skill.skill_id | != skill_id | Required skill |
| `min_mastery` | float | yes | 0 .. 1 |  |  |  |  | Mastery needed on the prerequisite |
| `effective_from` | datetime | yes |  |  |  |  |  | Rule applies to decisions on/after this time |
| `is_cross_course` | boolean | yes |  |  |  |  | == (course of prerequisite != course of skill) | True when the prerequisite lives in another course |

Table rules:
- The prerequisite graph must be acyclic (checked with Kahn's algorithm).

## `content_item` (ContentItem)

A learning activity: video, article, quiz, exercise or project.

Primary key: `content_id`

| Field | Type | Req. | Allowed / range | Unit | Unique | FK | Validation rule | Description |
|---|---|---|---|---|---|---|---|---|
| `content_id` | string | yes |  |  | yes |  | pattern `^CNT-\d{6}$` | Content ID |
| `course_id` | string | yes |  |  |  | course.course_id |  | Owning course |
| `title` | string | yes |  |  |  |  |  | Content title |
| `format` | string | yes | VIDEO, ARTICLE, QUIZ, EXERCISE, PROJECT |  |  |  |  | Content format |
| `difficulty` | integer | yes | 1 .. 5 |  |  |  |  | Difficulty 1 (easy) .. 5 (hard) |
| `duration_min` | integer | yes | 1 ..  | minutes |  |  |  | Expected time to finish |
| `is_assessed` | boolean | yes |  |  |  |  | == format in (QUIZ, EXERCISE, PROJECT) | Produces a score |
| `status` | string | yes | DRAFT, PUBLISHED, RETIRED |  |  |  |  | Lifecycle state |
| `published_at` | datetime | no |  |  |  |  | null iff status = DRAFT | When it became available |
| `retired_at` | datetime | no |  |  |  |  | not null iff status = RETIRED; > published_at | When it stopped being available |

## `content_skill` (ContentSkill)

Bridge table mapping content to skills (N:M). Exactly one PRIMARY skill per content item.

Primary key: `content_id, skill_id`

| Field | Type | Req. | Allowed / range | Unit | Unique | FK | Validation rule | Description |
|---|---|---|---|---|---|---|---|---|
| `content_id` | string | yes |  |  |  | content_item.content_id |  | Content item |
| `skill_id` | string | yes |  |  |  | skill.skill_id |  | Skill taught/assessed |
| `role` | string | yes | PRIMARY, SECONDARY |  |  |  |  | PRIMARY or SECONDARY |
| `weight` | float | yes | 0 .. 1 |  |  |  |  | Evidence weight (PRIMARY 1.0, SECONDARY 0.5) |

## `learner` (Learner)

A pseudonymous learner account (no personal data).

Primary key: `learner_id`

| Field | Type | Req. | Allowed / range | Unit | Unique | FK | Validation rule | Description |
|---|---|---|---|---|---|---|---|---|
| `learner_id` | string | yes |  |  | yes |  | pattern `^LRN-\d{6}$` | Learner ID |
| `handle` | string | yes |  |  | yes |  |  | Pseudonymous display handle |
| `signup_at` | datetime | yes |  |  |  |  |  | Account creation time |
| `region` | string | yes | IN-KA, IN-MH, IN-TN, IN-TS, IN-DL, IN-WB |  |  |  |  | Coarse region code |
| `preferred_format` | string | yes | VIDEO, ARTICLE, QUIZ, EXERCISE, PROJECT |  |  |  |  | Self-declared favourite format |
| `account_status` | string | yes | NEW, ACTIVE, DORMANT |  |  |  | NEW if no attempts; ACTIVE if last attempt within 90 days of window end; else DORMANT | Derived from attempts |

## `learning_path` (LearningPath)

A learner's enrolment in one course. Current state is derived from learning_path_event (history is never overwritten).

Primary key: `path_id`

| Field | Type | Req. | Allowed / range | Unit | Unique | FK | Validation rule | Description |
|---|---|---|---|---|---|---|---|---|
| `path_id` | string | yes |  |  | yes |  | pattern `^PATH-\d{4}-\d{6}$` | Path ID |
| `learner_id` | string | yes |  |  |  | learner.learner_id |  | Learner |
| `course_id` | string | yes |  |  |  | course.course_id |  | Course |
| `created_at` | datetime | yes |  |  |  |  | >= learner.signup_at | Enrolment time |
| `current_status` | string | yes | ENROLLED, ACTIVE, STALLED, COMPLETED, WITHDRAWN |  |  |  | == to_status of the latest learning_path_event | Latest status |
| `status_updated_at` | datetime | yes |  |  |  |  |  | Time of latest event |
| `completed_at` | datetime | no |  |  |  |  | not null iff COMPLETED | Completion time |

Table rules:
- (learner_id, course_id) is unique.

## `learning_path_event` (LearningPathEvent)

Append-only status history of a learning path.

Primary key: `event_id`

| Field | Type | Req. | Allowed / range | Unit | Unique | FK | Validation rule | Description |
|---|---|---|---|---|---|---|---|---|
| `event_id` | string | yes |  |  | yes |  | pattern `^PEV-\d{8}$` | Event ID |
| `path_id` | string | yes |  |  |  | learning_path.path_id |  | Path |
| `from_status` | string | no | ENROLLED, ACTIVE, STALLED, COMPLETED, WITHDRAWN |  |  |  |  | Status before (null for the first event) |
| `to_status` | string | yes | ENROLLED, ACTIVE, STALLED, COMPLETED, WITHDRAWN |  |  |  |  | Status after |
| `event_at` | datetime | yes |  |  |  |  |  | When the change happened |
| `reason_code` | string | yes | ENROLLED, FIRST_ACTIVITY, INACTIVITY_60D, RESUMED, ALL_SKILLS_PASSED, NO_RETURN_AFTER_STALL |  |  |  |  | Why it changed |

Table rules:
- (from_status, to_status) must be an allowed transition.
- Events of a path are re-derived from attempts and must match exactly.

## `attempt` (Attempt)

One learner interaction with one content item.

Primary key: `attempt_id`

| Field | Type | Req. | Allowed / range | Unit | Unique | FK | Validation rule | Description |
|---|---|---|---|---|---|---|---|---|
| `attempt_id` | string | yes |  |  | yes |  | pattern `^ATT-LRN-\d{6}-\d{6}$` | ATT-<learner-id>-<n>, n follows the learner's chronology |
| `learner_id` | string | yes |  |  |  | learner.learner_id |  | Learner |
| `content_id` | string | yes |  |  |  | content_item.content_id |  | Content attempted |
| `path_id` | string | yes |  |  |  | learning_path.path_id | path course == content course | Learning path the attempt belongs to |
| `attempt_no` | integer | yes | 1 ..  |  |  |  |  | n-th attempt of this learner on this content |
| `status` | string | yes | COMPLETED, ABANDONED |  |  |  |  | COMPLETED or ABANDONED |
| `started_at` | datetime | yes |  |  |  |  | >= learner.signup_at and path.created_at | Start time |
| `completed_at` | datetime | no |  |  |  |  | null iff ABANDONED; >= started_at | End time |
| `score` | integer | no | 0 .. 100 | points |  |  | not null iff COMPLETED and content is assessed | Score for assessed content |
| `duration_min` | integer | yes | 1 ..  | minutes |  |  |  | Time spent |

## `assessment_result` (AssessmentResult)

Per-skill evidence derived from a completed assessed attempt (1 attempt : 1..2 results).

Primary key: `result_id`

| Field | Type | Req. | Allowed / range | Unit | Unique | FK | Validation rule | Description |
|---|---|---|---|---|---|---|---|---|
| `result_id` | string | yes |  |  | yes |  | pattern `^ASR-LRN-\d{6}-\d{6}-[12]$` | <attempt-id with ASR prefix>-<1 primary | 2 secondary> |
| `attempt_id` | string | yes |  |  |  | attempt.attempt_id |  | Source attempt |
| `learner_id` | string | yes |  |  |  | learner.learner_id |  | Learner (== attempt learner) |
| `skill_id` | string | yes |  |  |  | skill.skill_id |  | Skill evidenced |
| `skill_weight` | float | yes | 0 .. 1 |  |  |  |  | Weight copied from content_skill |
| `score` | integer | yes | 0 .. 100 | points |  |  |  | == attempt.score |
| `passed` | boolean | yes |  |  |  |  |  | score >= 70 |
| `assessed_at` | datetime | yes |  |  |  |  |  | == attempt.completed_at |

## `mastery_snapshot` (MasterySnapshot)

Mastery estimate used by a recommendation decision, stored for traceability.

Primary key: `snapshot_id`

| Field | Type | Req. | Allowed / range | Unit | Unique | FK | Validation rule | Description |
|---|---|---|---|---|---|---|---|---|
| `snapshot_id` | string | yes |  |  | yes |  | pattern `^MSN-\d{8}$` | Snapshot ID |
| `learner_id` | string | yes |  |  |  | learner.learner_id |  | Learner |
| `skill_id` | string | yes |  |  |  | skill.skill_id |  | Skill |
| `as_of` | datetime | yes |  |  |  |  |  | Decision time; only evidence strictly before it may be used |
| `mastery` | float | yes | 0 .. 1 |  |  |  | == recomputation from assessment_result before as_of | Recency-weighted mastery estimate |
| `evidence_count` | integer | yes | 0 ..  |  |  |  |  | Results used |
| `last_evidence_at` | datetime | no |  |  |  |  | < as_of | Newest evidence used |
| `method_version` | string | yes |  |  |  |  |  | Mastery method version |

Table rules:
- (learner_id, skill_id, as_of) is unique.

## `recommendation` (Recommendation)

Ranked next-activity recommendations (or an exception routed to an instructor) per decision.

Primary key: `recommendation_id`

| Field | Type | Req. | Allowed / range | Unit | Unique | FK | Validation rule | Description |
|---|---|---|---|---|---|---|---|---|
| `recommendation_id` | string | yes |  |  | yes |  | pattern `^REC-\d{8}$` | Recommendation ID |
| `decision_id` | string | yes |  |  |  |  | pattern `^DEC-\d{8}$` | Groups the ranked items of one decision |
| `learner_id` | string | yes |  |  |  | learner.learner_id |  | Learner |
| `path_id` | string | yes |  |  |  | learning_path.path_id |  | Path the decision was made for |
| `as_of` | datetime | yes |  |  |  |  |  | Decision time |
| `rank` | integer | yes | 1 .. 3 |  |  |  |  | 1 = best |
| `content_id` | string | no |  |  |  | content_item.content_id | all prerequisites met at as_of | Recommended item (null for exceptions) |
| `target_skill_id` | string | no |  |  |  | skill.skill_id |  | Skill the item works on / blocking skill |
| `mastery_snapshot_id` | string | no |  |  |  | mastery_snapshot.snapshot_id |  | Mastery of target skill at decision time |
| `reason_code` | string | yes | COLD_START_ENTRY, NEXT_FRONTIER_SKILL, REINFORCE_NEAR_MASTERY, REMEDIATE_RECENT_FAIL, STRETCH_CHALLENGE, REMEDIATE_PREREQUISITE, ESCALATE_NO_ELIGIBLE_CONTENT, ESCALATE_LOOP_NO_PROGRESS, ESCALATE_NO_REMAINING_CONTENT |  |  |  |  | Why it was recommended |
| `rationale` | string | yes |  |  |  |  |  | Human-readable explanation |
| `predicted_success` | float | no | 0 .. 1 |  |  |  |  | Estimated pass probability |
| `route_to` | string | yes | LEARNER, INSTRUCTOR |  |  |  |  | Who acts on it |
| `engine_version` | string | yes |  |  |  |  |  | Recommender version |
| `outcome` | string | yes | PASSED, FAILED, COMPLETED, ABANDONED, NOT_TAKEN, NOT_APPLICABLE |  |  |  |  | What happened within 30 days |
| `outcome_attempt_id` | string | no |  |  |  | attempt.attempt_id |  | Attempt that produced the outcome |
| `outcome_at` | datetime | no |  |  |  |  |  | Start of that attempt |
