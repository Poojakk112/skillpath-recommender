-- Generated from skillpath/schema.py. Load with: python -m skillpath load-sqlite
PRAGMA foreign_keys = ON;

CREATE TABLE course (
  course_id TEXT NOT NULL,
  title TEXT NOT NULL,
  domain TEXT NOT NULL CHECK (domain IN ('PROGRAMMING', 'DATA', 'CLOUD', 'SECURITY', 'DESIGN')),
  level TEXT NOT NULL CHECK (level IN ('BEGINNER', 'INTERMEDIATE', 'ADVANCED')),
  launched_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'ARCHIVED')),
  PRIMARY KEY (course_id)
);

CREATE TABLE skill (
  skill_id TEXT NOT NULL,
  course_id TEXT NOT NULL,
  name TEXT NOT NULL,
  level INTEGER NOT NULL CHECK (level >= 1) CHECK (level <= 4),
  PRIMARY KEY (skill_id),
  FOREIGN KEY (course_id) REFERENCES course(course_id)
);

CREATE TABLE skill_prerequisite (
  skill_id TEXT NOT NULL,
  prerequisite_skill_id TEXT NOT NULL,
  min_mastery REAL NOT NULL CHECK (min_mastery >= 0) CHECK (min_mastery <= 1),
  effective_from TEXT NOT NULL,
  is_cross_course INTEGER NOT NULL,
  PRIMARY KEY (skill_id, prerequisite_skill_id),
  FOREIGN KEY (skill_id) REFERENCES skill(skill_id),
  FOREIGN KEY (prerequisite_skill_id) REFERENCES skill(skill_id)
);

CREATE TABLE content_item (
  content_id TEXT NOT NULL,
  course_id TEXT NOT NULL,
  title TEXT NOT NULL,
  format TEXT NOT NULL CHECK (format IN ('VIDEO', 'ARTICLE', 'QUIZ', 'EXERCISE', 'PROJECT')),
  difficulty INTEGER NOT NULL CHECK (difficulty >= 1) CHECK (difficulty <= 5),
  duration_min INTEGER NOT NULL CHECK (duration_min >= 1),
  is_assessed INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('DRAFT', 'PUBLISHED', 'RETIRED')),
  published_at TEXT,
  retired_at TEXT,
  PRIMARY KEY (content_id),
  FOREIGN KEY (course_id) REFERENCES course(course_id)
);

CREATE TABLE content_skill (
  content_id TEXT NOT NULL,
  skill_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('PRIMARY', 'SECONDARY')),
  weight REAL NOT NULL CHECK (weight >= 0) CHECK (weight <= 1),
  PRIMARY KEY (content_id, skill_id),
  FOREIGN KEY (content_id) REFERENCES content_item(content_id),
  FOREIGN KEY (skill_id) REFERENCES skill(skill_id)
);

CREATE TABLE learner (
  learner_id TEXT NOT NULL,
  handle TEXT NOT NULL,
  signup_at TEXT NOT NULL,
  region TEXT NOT NULL CHECK (region IN ('IN-KA', 'IN-MH', 'IN-TN', 'IN-TS', 'IN-DL', 'IN-WB')),
  preferred_format TEXT NOT NULL CHECK (preferred_format IN ('VIDEO', 'ARTICLE', 'QUIZ', 'EXERCISE', 'PROJECT')),
  account_status TEXT NOT NULL CHECK (account_status IN ('NEW', 'ACTIVE', 'DORMANT')),
  PRIMARY KEY (learner_id)
);

CREATE TABLE learning_path (
  path_id TEXT NOT NULL,
  learner_id TEXT NOT NULL,
  course_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  current_status TEXT NOT NULL CHECK (current_status IN ('ENROLLED', 'ACTIVE', 'STALLED', 'COMPLETED', 'WITHDRAWN')),
  status_updated_at TEXT NOT NULL,
  completed_at TEXT,
  PRIMARY KEY (path_id),
  FOREIGN KEY (learner_id) REFERENCES learner(learner_id),
  FOREIGN KEY (course_id) REFERENCES course(course_id)
);

CREATE TABLE learning_path_event (
  event_id TEXT NOT NULL,
  path_id TEXT NOT NULL,
  from_status TEXT CHECK (from_status IN ('ENROLLED', 'ACTIVE', 'STALLED', 'COMPLETED', 'WITHDRAWN')),
  to_status TEXT NOT NULL CHECK (to_status IN ('ENROLLED', 'ACTIVE', 'STALLED', 'COMPLETED', 'WITHDRAWN')),
  event_at TEXT NOT NULL,
  reason_code TEXT NOT NULL CHECK (reason_code IN ('ENROLLED', 'FIRST_ACTIVITY', 'INACTIVITY_60D', 'RESUMED', 'ALL_SKILLS_PASSED', 'NO_RETURN_AFTER_STALL')),
  PRIMARY KEY (event_id),
  FOREIGN KEY (path_id) REFERENCES learning_path(path_id)
);

CREATE TABLE attempt (
  attempt_id TEXT NOT NULL,
  learner_id TEXT NOT NULL,
  content_id TEXT NOT NULL,
  path_id TEXT NOT NULL,
  attempt_no INTEGER NOT NULL CHECK (attempt_no >= 1),
  status TEXT NOT NULL CHECK (status IN ('COMPLETED', 'ABANDONED')),
  started_at TEXT NOT NULL,
  completed_at TEXT,
  score INTEGER CHECK (score >= 0) CHECK (score <= 100),
  duration_min INTEGER NOT NULL CHECK (duration_min >= 1),
  PRIMARY KEY (attempt_id),
  FOREIGN KEY (learner_id) REFERENCES learner(learner_id),
  FOREIGN KEY (content_id) REFERENCES content_item(content_id),
  FOREIGN KEY (path_id) REFERENCES learning_path(path_id)
);

CREATE TABLE assessment_result (
  result_id TEXT NOT NULL,
  attempt_id TEXT NOT NULL,
  learner_id TEXT NOT NULL,
  skill_id TEXT NOT NULL,
  skill_weight REAL NOT NULL CHECK (skill_weight >= 0) CHECK (skill_weight <= 1),
  score INTEGER NOT NULL CHECK (score >= 0) CHECK (score <= 100),
  passed INTEGER NOT NULL,
  assessed_at TEXT NOT NULL,
  PRIMARY KEY (result_id),
  FOREIGN KEY (attempt_id) REFERENCES attempt(attempt_id),
  FOREIGN KEY (learner_id) REFERENCES learner(learner_id),
  FOREIGN KEY (skill_id) REFERENCES skill(skill_id)
);

CREATE TABLE mastery_snapshot (
  snapshot_id TEXT NOT NULL,
  learner_id TEXT NOT NULL,
  skill_id TEXT NOT NULL,
  as_of TEXT NOT NULL,
  mastery REAL NOT NULL CHECK (mastery >= 0) CHECK (mastery <= 1),
  evidence_count INTEGER NOT NULL CHECK (evidence_count >= 0),
  last_evidence_at TEXT,
  method_version TEXT NOT NULL,
  PRIMARY KEY (snapshot_id),
  FOREIGN KEY (learner_id) REFERENCES learner(learner_id),
  FOREIGN KEY (skill_id) REFERENCES skill(skill_id)
);

CREATE TABLE recommendation (
  recommendation_id TEXT NOT NULL,
  decision_id TEXT NOT NULL,
  learner_id TEXT NOT NULL,
  path_id TEXT NOT NULL,
  as_of TEXT NOT NULL,
  rank INTEGER NOT NULL CHECK (rank >= 1) CHECK (rank <= 3),
  content_id TEXT,
  target_skill_id TEXT,
  mastery_snapshot_id TEXT,
  reason_code TEXT NOT NULL CHECK (reason_code IN ('COLD_START_ENTRY', 'NEXT_FRONTIER_SKILL', 'REINFORCE_NEAR_MASTERY', 'REMEDIATE_RECENT_FAIL', 'STRETCH_CHALLENGE', 'REMEDIATE_PREREQUISITE', 'ESCALATE_NO_ELIGIBLE_CONTENT', 'ESCALATE_LOOP_NO_PROGRESS', 'ESCALATE_NO_REMAINING_CONTENT')),
  rationale TEXT NOT NULL,
  predicted_success REAL CHECK (predicted_success >= 0) CHECK (predicted_success <= 1),
  route_to TEXT NOT NULL CHECK (route_to IN ('LEARNER', 'INSTRUCTOR')),
  engine_version TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ('PASSED', 'FAILED', 'COMPLETED', 'ABANDONED', 'NOT_TAKEN', 'NOT_APPLICABLE')),
  outcome_attempt_id TEXT,
  outcome_at TEXT,
  PRIMARY KEY (recommendation_id),
  FOREIGN KEY (learner_id) REFERENCES learner(learner_id),
  FOREIGN KEY (path_id) REFERENCES learning_path(path_id),
  FOREIGN KEY (content_id) REFERENCES content_item(content_id),
  FOREIGN KEY (target_skill_id) REFERENCES skill(skill_id),
  FOREIGN KEY (mastery_snapshot_id) REFERENCES mastery_snapshot(snapshot_id),
  FOREIGN KEY (outcome_attempt_id) REFERENCES attempt(attempt_id)
);
