"""Relational quiz snapshots, assignments and attempts; registered with the app at integration."""
from datetime import datetime
from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, utc_now

class Subject(Base):
    __tablename__='subjects'
    id: Mapped[int]=mapped_column(primary_key=True)
    title: Mapped[str]=mapped_column(String(160))
    normalized_title: Mapped[str]=mapped_column(String(320),unique=True)
    created_at: Mapped[datetime]=mapped_column(default=utc_now)

class Quiz(Base):
    __tablename__='quizzes'
    id: Mapped[int]=mapped_column(primary_key=True)
    title: Mapped[str]=mapped_column(String(180))
    archived: Mapped[bool]=mapped_column(default=False)
    revision: Mapped[int]=mapped_column(default=1)
    created_by: Mapped[int]=mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime]=mapped_column(default=utc_now)

class QuizVersion(Base):
    __tablename__='quiz_versions'
    id: Mapped[int]=mapped_column(primary_key=True)
    quiz_id: Mapped[int]=mapped_column(ForeignKey('quizzes.id'))
    number: Mapped[int]
    state: Mapped[str]=mapped_column(String(12),default='draft')
    revision: Mapped[int]=mapped_column(default=1)
    title: Mapped[str]=mapped_column(String(180))
    description: Mapped[str]=mapped_column(Text,default='')
    subject_id: Mapped[int | None]=mapped_column(ForeignKey('subjects.id'))
    subject_title: Mapped[str]=mapped_column(String(160),default='')
    created_at: Mapped[datetime]=mapped_column(default=utc_now)
    published_at: Mapped[datetime | None]
    __table_args__=(UniqueConstraint('quiz_id','number'),CheckConstraint("state IN ('draft','published')"),CheckConstraint('number > 0 AND revision > 0'))

class QuizVersionTopic(Base):
    __tablename__='quiz_version_topics'
    version_id: Mapped[int]=mapped_column(ForeignKey('quiz_versions.id'),primary_key=True)
    topic_id: Mapped[int]=mapped_column(ForeignKey('topics.id'),primary_key=True)
    title_snapshot: Mapped[str]=mapped_column(String(180))

class QuizQuestion(Base):
    __tablename__='quiz_questions'
    id: Mapped[int]=mapped_column(primary_key=True)
    version_id: Mapped[int]=mapped_column(ForeignKey('quiz_versions.id'))
    position: Mapped[int]
    kind: Mapped[str]=mapped_column(String(12))
    body: Mapped[str]=mapped_column(Text)
    max_points: Mapped[int]  # Hundredths of a point, never binary float.
    __table_args__=(UniqueConstraint('id','version_id'),UniqueConstraint('version_id','position'),CheckConstraint("kind IN ('single','multiple','boolean','text')"),CheckConstraint('max_points BETWEEN 1 AND 1000000'),CheckConstraint('position > 0'))

class QuizOption(Base):
    __tablename__='quiz_options'
    id: Mapped[int]=mapped_column(primary_key=True)
    question_id: Mapped[int]=mapped_column(ForeignKey('quiz_questions.id'))
    position: Mapped[int]
    body: Mapped[str]=mapped_column(Text)
    correct: Mapped[bool]=mapped_column(default=False)
    __table_args__=(UniqueConstraint('id','question_id'),UniqueConstraint('question_id','position'),CheckConstraint('position > 0'))

class QuizAssignment(Base):
    __tablename__='quiz_assignments'
    id: Mapped[int]=mapped_column(primary_key=True)
    version_id: Mapped[int]=mapped_column(ForeignKey('quiz_versions.id'))
    student_id: Mapped[int]=mapped_column(ForeignKey('users.id'),index=True)
    revision: Mapped[int]=mapped_column(default=1)
    available_from: Mapped[datetime | None]
    deadline: Mapped[datetime | None]
    max_attempts: Mapped[int]=mapped_column(default=1)
    time_limit_minutes: Mapped[int | None]
    show_scores: Mapped[bool]=mapped_column(default=True)
    show_correct: Mapped[bool]=mapped_column(default=False)
    comment: Mapped[str]=mapped_column(Text,default='')
    created_by: Mapped[int]=mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime]=mapped_column(default=utc_now)
    __table_args__=(UniqueConstraint('version_id','student_id'),UniqueConstraint('id','student_id','version_id'),CheckConstraint('max_attempts BETWEEN 1 AND 100'),CheckConstraint('time_limit_minutes IS NULL OR time_limit_minutes BETWEEN 1 AND 1440'),CheckConstraint('deadline IS NULL OR available_from IS NULL OR deadline > available_from'))

class QuizAssignmentEvent(Base):
    __tablename__='quiz_assignment_events'
    id: Mapped[int]=mapped_column(primary_key=True)
    assignment_id: Mapped[int]=mapped_column(ForeignKey('quiz_assignments.id'))
    revision: Mapped[int]
    actor_id: Mapped[int]=mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime]=mapped_column(default=utc_now)
    reason: Mapped[str]=mapped_column(Text)
    available_from: Mapped[datetime | None]
    deadline: Mapped[datetime | None]
    max_attempts: Mapped[int]
    time_limit_minutes: Mapped[int | None]
    show_scores: Mapped[bool]
    show_correct: Mapped[bool]
    comment: Mapped[str]=mapped_column(Text)
    __table_args__=(UniqueConstraint('assignment_id','revision'),)

class QuizAttempt(Base):
    __tablename__='quiz_attempts'
    id: Mapped[int]=mapped_column(primary_key=True)
    assignment_id: Mapped[int]
    student_id: Mapped[int]=mapped_column(ForeignKey('users.id'))
    version_id: Mapped[int]=mapped_column(ForeignKey('quiz_versions.id'))
    number: Mapped[int]
    revision: Mapped[int]=mapped_column(default=1)
    assignment_revision: Mapped[int]
    state: Mapped[str]=mapped_column(String(12),default='in_progress')
    started_at: Mapped[datetime]=mapped_column(default=utc_now)
    deadline_snapshot: Mapped[datetime | None]
    time_limit_snapshot: Mapped[int | None]
    expires_at: Mapped[datetime | None]=mapped_column(index=True)
    submitted_at: Mapped[datetime | None]
    finalized_at: Mapped[datetime | None]
    checked_at: Mapped[datetime | None]
    timed_out: Mapped[bool]=mapped_column(default=False)
    total_points: Mapped[int | None]
    max_points: Mapped[int]
    __table_args__=(ForeignKeyConstraint(['assignment_id','student_id','version_id'],['quiz_assignments.id','quiz_assignments.student_id','quiz_assignments.version_id']),
        ForeignKeyConstraint(['assignment_id','assignment_revision'],['quiz_assignment_events.assignment_id','quiz_assignment_events.revision']),
        UniqueConstraint('id','version_id'),UniqueConstraint('assignment_id','number'),CheckConstraint("state IN ('in_progress','submitted','checked')"),CheckConstraint('number > 0 AND revision > 0 AND max_points > 0'),CheckConstraint('total_points IS NULL OR total_points BETWEEN 0 AND max_points'),
        Index('uq_quiz_open_attempt','assignment_id',unique=True,sqlite_where=text("state = 'in_progress'")),Index('ix_quiz_attempt_student_state','student_id','state'))

class QuizAnswer(Base):
    __tablename__='quiz_answers'
    id: Mapped[int]=mapped_column(primary_key=True)
    attempt_id: Mapped[int]
    version_id: Mapped[int]
    question_id: Mapped[int]
    text_answer: Mapped[str]=mapped_column(Text,default='')
    revision: Mapped[int]=mapped_column(default=1)
    saved_at: Mapped[datetime | None]
    points: Mapped[int | None]
    grade_comment: Mapped[str]=mapped_column(Text,default='')
    __table_args__=(ForeignKeyConstraint(['attempt_id','version_id'],['quiz_attempts.id','quiz_attempts.version_id']),ForeignKeyConstraint(['question_id','version_id'],['quiz_questions.id','quiz_questions.version_id']),UniqueConstraint('attempt_id','question_id'),UniqueConstraint('id','question_id'),CheckConstraint('points IS NULL OR points >= 0'),CheckConstraint('revision > 0'))

class QuizAnswerOption(Base):
    __tablename__='quiz_answer_options'
    answer_id: Mapped[int]=mapped_column(primary_key=True)
    option_id: Mapped[int]=mapped_column(primary_key=True)
    question_id: Mapped[int]
    __table_args__=(ForeignKeyConstraint(['answer_id','question_id'],['quiz_answers.id','quiz_answers.question_id']),ForeignKeyConstraint(['option_id','question_id'],['quiz_options.id','quiz_options.question_id']))

class QuizGradeEvent(Base):
    __tablename__='quiz_grade_events'
    id: Mapped[int]=mapped_column(primary_key=True)
    answer_id: Mapped[int]=mapped_column(ForeignKey('quiz_answers.id'),index=True)
    actor_id: Mapped[int]=mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime]=mapped_column(default=utc_now)
    old_points: Mapped[int | None]
    new_points: Mapped[int]
    old_comment: Mapped[str]=mapped_column(Text)
    new_comment: Mapped[str]=mapped_column(Text)
    reason: Mapped[str]=mapped_column(Text)
    __table_args__=(CheckConstraint('new_points >= 0'),)

class QuizRequest(Base):
    __tablename__='quiz_requests'
    id: Mapped[int]=mapped_column(primary_key=True)
    request_key: Mapped[str]=mapped_column(String(32),unique=True)
    request_hash: Mapped[str]=mapped_column(String(64))
    actor_id: Mapped[int]=mapped_column(ForeignKey('users.id'))
    action: Mapped[str]=mapped_column(String(32))
    result_id: Mapped[int]
    created_at: Mapped[datetime]=mapped_column(default=utc_now)

class QuizExtensionEvent(Base):
    __tablename__='quiz_extension_events'
    id: Mapped[int]=mapped_column(primary_key=True)
    attempt_id: Mapped[int]=mapped_column(ForeignKey('quiz_attempts.id'),index=True)
    actor_id: Mapped[int]=mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime]=mapped_column(default=utc_now)
    old_expires_at: Mapped[datetime]
    new_expires_at: Mapped[datetime]
    reason: Mapped[str]=mapped_column(Text)
    __table_args__=(CheckConstraint('new_expires_at > old_expires_at'),)
