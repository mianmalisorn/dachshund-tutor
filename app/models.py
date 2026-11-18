"""Текущая схема БД; изменения применяются через Alembic."""
from datetime import date, time, datetime, timezone
from sqlalchemy import (
    Boolean, CheckConstraint, Date, Time, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, String,
    Text, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now():
    """Naive UTC to support SQLite consistently; convert at presentation boundaries."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class StudentFolder(Base):
    __tablename__ = 'student_folders'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)


class AssignmentFolder(Base):
    __tablename__ = 'assignment_folders'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)


class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(250))
    role: Mapped[str] = mapped_column(String(12))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    learning_status: Mapped[str] = mapped_column(String(12), CheckConstraint("learning_status IN ('active','paused','archived')", name='learning_status_check'), default='active', server_default='active', index=True)
    internal_notes: Mapped[str] = mapped_column(Text, default='', server_default='')
    learning_goal: Mapped[str] = mapped_column(Text, default='', server_default='')
    learning_materials: Mapped[str] = mapped_column(Text, default='', server_default='')
    learning_position: Mapped[str] = mapped_column(Text, default='', server_default='')
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    auth_version: Mapped[int] = mapped_column(Integer, default=1)
    profile_version: Mapped[int] = mapped_column(Integer, default=1, server_default='1')
    folder_id: Mapped[int | None] = mapped_column(ForeignKey('student_folders.id', ondelete='SET NULL'))
    folder: Mapped[StudentFolder | None] = relationship()
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    __table_args__ = (CheckConstraint("role IN ('admin', 'student', 'sender')", name='role_check'),)


class VideoMeeting(Base):
    __tablename__ = 'video_meetings'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    room_name: Mapped[str] = mapped_column(String(80), unique=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    student_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    state: Mapped[str] = mapped_column(String(12))
    lease_slot: Mapped[int | None] = mapped_column(Integer, unique=True)
    start_key: Mapped[str | None] = mapped_column(String(80), unique=True)
    revocations_complete: Mapped[bool] = mapped_column(Boolean, default=False, server_default='0')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (
        CheckConstraint("state IN ('starting','active','ending','ended')", name='video_meeting_state'),
        CheckConstraint("(state = 'ended' AND lease_slot IS NULL) OR "
                        "(state != 'ended' AND lease_slot = 1)", name='video_single_slot'),
    )


class VideoMessage(Base):
    __tablename__ = 'video_messages'
    id: Mapped[int] = mapped_column(primary_key=True)
    meeting_id: Mapped[str] = mapped_column(ForeignKey('video_meetings.id'))
    author_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    text: Mapped[str] = mapped_column(Text)
    client_key: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    __table_args__ = (
        UniqueConstraint('meeting_id', 'author_id', 'client_key', name='uq_video_message_retry'),
        Index('ix_video_messages_meeting_order', 'meeting_id', 'id'),
    )


class VideoMaterial(Base):
    __tablename__ = 'video_materials'
    id: Mapped[int] = mapped_column(primary_key=True)
    meeting_id: Mapped[str] = mapped_column(ForeignKey('video_meetings.id'))
    kind: Mapped[str] = mapped_column(String(8))
    title: Mapped[str] = mapped_column(String(255))
    author_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    attachment_id: Mapped[int | None] = mapped_column(ForeignKey('attachments.id'), unique=True)
    url: Mapped[str | None] = mapped_column(String(2048))
    __table_args__ = (
        CheckConstraint("(kind = 'file' AND attachment_id IS NOT NULL AND url IS NULL) OR "
                        "(kind = 'link' AND attachment_id IS NULL AND url IS NOT NULL)",
                        name='video_material_kind'),
        Index('ix_video_materials_meeting_order', 'meeting_id', 'id'),
    )


class StudentStatusEvent(Base):
    __tablename__ = 'student_status_events'
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    status: Mapped[str] = mapped_column(String(12))
    access_enabled: Mapped[bool] = mapped_column(Boolean)
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    changed_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    __table_args__ = (CheckConstraint("status IN ('active','paused','archived')", name='event_status_check'),)


class MascotPreference(Base):
    """Private decoration state; no students, homework text or quotes stored here."""
    __tablename__ = 'mascot_preferences'
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    frequency: Mapped[str] = mapped_column(String(12), default='normal')
    tone: Mapped[str] = mapped_column(String(12), default='medium')
    rare_care: Mapped[bool] = mapped_column(Boolean, default=True)
    easter_eggs: Mapped[bool] = mapped_column(Boolean, default=False)
    animations: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    state_json: Mapped[str] = mapped_column(Text, default='{}')
    __table_args__ = (
        CheckConstraint("frequency IN ('rare','normal','frequent')", name='mascot_frequency_check'),
        CheckConstraint("tone IN ('soft','medium','spicy')", name='mascot_tone_check'),
    )


class Assignment(Base):
    __tablename__ = 'assignments'
    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default='1')
    creation_key: Mapped[str | None] = mapped_column(String(32))
    __table_args__ = (Index('uq_assignments_creation_key', 'creation_key', unique=True),)
    title: Mapped[str] = mapped_column(String(180))
    description: Mapped[str] = mapped_column(Text, default='')
    due_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    folder_id: Mapped[int | None] = mapped_column(ForeignKey('assignment_folders.id', ondelete='SET NULL'))
    folder: Mapped[AssignmentFolder | None] = relationship()
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    targets: Mapped[list['AssignmentTarget']] = relationship(back_populates='assignment', cascade='all, delete-orphan')
    attachments: Mapped[list['Attachment']] = relationship(
        primaryjoin="and_(Assignment.id==Attachment.assignment_id, Attachment.submission_id.is_(None))",
        viewonly=True,
    )


class AssignmentTarget(Base):
    __tablename__ = 'assignment_targets'
    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default='1')
    assignment_id: Mapped[int] = mapped_column(ForeignKey('assignments.id', ondelete='CASCADE'))
    student_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    assignment: Mapped[Assignment] = relationship(back_populates='targets')
    student: Mapped[User] = relationship()
    submissions: Mapped[list['Submission']] = relationship(
        back_populates='target', cascade='all, delete-orphan', order_by='Submission.created_at.desc()'
    )
    __table_args__ = (UniqueConstraint('assignment_id', 'student_id', name='unique_target'),)


class Submission(Base):
    __tablename__ = 'submissions'
    id: Mapped[int] = mapped_column(primary_key=True)
    target_id: Mapped[int] = mapped_column(ForeignKey('assignment_targets.id', ondelete='CASCADE'))
    target: Mapped[AssignmentTarget] = relationship(back_populates='submissions')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    due_at_snapshot: Mapped[datetime | None] = mapped_column(DateTime)
    review_state: Mapped[str] = mapped_column(String(12), default='pending')
    review_comment: Mapped[str] = mapped_column(Text, default='')
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    attachments: Mapped[list['Attachment']] = relationship(
        primaryjoin="and_(Submission.id==Attachment.submission_id, Attachment.assignment_id.is_(None))",
        viewonly=True,
    )
    __table_args__ = (CheckConstraint("review_state IN ('pending','checked','revision')", name='review_state_check'),)
    reviews: Mapped[list['ReviewEvent']] = relationship(order_by='ReviewEvent.id', lazy='selectin')


class ReviewEvent(Base):
    __tablename__ = 'review_events'
    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey('submissions.id'), index=True)
    state: Mapped[str] = mapped_column(String(12))
    comment: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    __table_args__ = (CheckConstraint("state IN ('checked','revision')", name='review_event_state'),)


class DeadlineEvent(Base):
    __tablename__ = 'deadline_events'
    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey('assignments.id'), index=True)
    previous_due_at: Mapped[datetime] = mapped_column(DateTime)
    due_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    actor_id: Mapped[int] = mapped_column(ForeignKey('users.id'))


class Attachment(Base):
    __tablename__ = 'attachments'
    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int | None] = mapped_column(ForeignKey('assignments.id', ondelete='CASCADE'))
    submission_id: Mapped[int | None] = mapped_column(ForeignKey('submissions.id', ondelete='CASCADE'))
    meeting_id: Mapped[str | None] = mapped_column(ForeignKey('video_meetings.id'))
    filename: Mapped[str] = mapped_column(String(255))
    storage_name: Mapped[str] = mapped_column(String(64), unique=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    __table_args__ = (CheckConstraint(
        '(assignment_id IS NOT NULL AND submission_id IS NULL AND meeting_id IS NULL) OR '
        '(assignment_id IS NULL AND submission_id IS NOT NULL AND meeting_id IS NULL) OR '
        '(assignment_id IS NULL AND submission_id IS NULL AND meeting_id IS NOT NULL)',
        name='one_attachment_owner'
    ),)


class Lesson(Base):
    __tablename__ = 'lessons'
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    student: Mapped[User] = relationship()
    starts_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=60)
    meeting_url: Mapped[str] = mapped_column(String(500))
    note: Mapped[str] = mapped_column(String(500), default='')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    series_id: Mapped[int | None] = mapped_column(ForeignKey('lesson_series.id'),index=True)
    occurrence_date: Mapped[date | None] = mapped_column(Date)
    timezone_name: Mapped[str | None] = mapped_column(String(80))
    original_starts_at: Mapped[datetime | None] = mapped_column(DateTime)
    state: Mapped[str] = mapped_column(String(12),default='scheduled',server_default='scheduled')
    is_exception: Mapped[bool] = mapped_column(Boolean,default=False,server_default='0')
    version: Mapped[int] = mapped_column(Integer,default=1,server_default='1')
    creation_key: Mapped[str | None] = mapped_column(String(32),unique=True)
    __table_args__ = (CheckConstraint('duration_minutes BETWEEN 15 AND 240', name='valid_lesson_duration'),
                     CheckConstraint("state IN ('scheduled','cancelled','suppressed')",name='lesson_state'),
                     UniqueConstraint('series_id','occurrence_date',name='uq_series_occurrence'),
                     Index('uq_lesson_student','id','student_id',unique=True))


class LoginAttempt(Base):
    __tablename__ = 'login_attempts'
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(60), index=True)
    ip_address: Mapped[str] = mapped_column(String(100))
    at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    __table_args__ = (Index('ix_login_attempt_user_time', 'username', 'at'),)


class LessonRecord(Base):
    """Private record of a conducted lesson, independent from a scheduled meeting."""
    __tablename__ = 'lesson_records'
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    lesson_date: Mapped[date] = mapped_column(Date)
    lesson_time: Mapped[time | None] = mapped_column(Time)
    timezone_name: Mapped[str] = mapped_column(String(80))
    topic: Mapped[str] = mapped_column(Text, default='')
    completed: Mapped[str] = mapped_column(Text, default='')
    stopped_at: Mapped[str] = mapped_column(Text, default='')
    difficulties: Mapped[str] = mapped_column(Text, default='')
    next_step: Mapped[str] = mapped_column(Text, default='')
    creation_key: Mapped[str] = mapped_column(String(32), unique=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (Index('uq_lesson_record_owner', 'id', 'student_id', unique=True), Index('ix_lesson_records_student_date', 'student_id', 'deleted_at', 'lesson_date', 'lesson_time', 'id'),)


class StudentNote(Base):
    __tablename__ = 'student_notes'
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    body: Mapped[str] = mapped_column(Text)
    creation_key: Mapped[str] = mapped_column(String(32), unique=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (Index('ix_student_notes_student_created', 'student_id', 'deleted_at', 'created_at', 'id'),)


class TeachingRevision(Base):
    """Immutable previous values for recovery; not a second source of current data."""
    __tablename__ = 'teaching_revisions'
    id: Mapped[int] = mapped_column(primary_key=True)
    lesson_record_id: Mapped[int | None] = mapped_column(ForeignKey('lesson_records.id'), index=True)
    note_id: Mapped[int | None] = mapped_column(ForeignKey('student_notes.id'), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    action: Mapped[str] = mapped_column(String(12))
    snapshot: Mapped[str] = mapped_column(Text)
    __table_args__ = (CheckConstraint('(lesson_record_id IS NOT NULL AND note_id IS NULL) OR (lesson_record_id IS NULL AND note_id IS NOT NULL)', name='one_teaching_revision_owner'),)


class Topic(Base):
    __tablename__ = 'topics'
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(180))
    category: Mapped[str] = mapped_column(String(80), default='')
    normalized_title: Mapped[str] = mapped_column(String(360))
    normalized_category: Mapped[str] = mapped_column(String(160), default='')
    creation_key: Mapped[str] = mapped_column(String(32), unique=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    __table_args__ = (UniqueConstraint('normalized_title','normalized_category',name='uq_topic_name_category'),)


class StudentTopic(Base):
    __tablename__ = 'student_topics'
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    topic_id: Mapped[int] = mapped_column(ForeignKey('topics.id'))
    topic: Mapped[Topic] = relationship()
    status: Mapped[str] = mapped_column(String(16), default='unmarked')
    note: Mapped[str] = mapped_column(Text, default='')
    creation_key: Mapped[str] = mapped_column(String(32), unique=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    status_changed_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    __table_args__ = (
        UniqueConstraint('student_id','topic_id',name='uq_student_topic'),
        UniqueConstraint('id','student_id',name='uq_student_topic_owner'),
        CheckConstraint("status IN ('unmarked','studying','practicing','repeat','confident')",name='student_topic_status'),
        Index('ix_student_topics_student_status','student_id','status','updated_at'),
    )


class LessonTopic(Base):
    __tablename__ = 'lesson_topics'
    lesson_record_id: Mapped[int] = mapped_column(primary_key=True)
    student_topic_id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    __table_args__ = (
        ForeignKeyConstraint(['lesson_record_id','student_id'],['lesson_records.id','lesson_records.student_id']),
        ForeignKeyConstraint(['student_topic_id','student_id'],['student_topics.id','student_topics.student_id']),
        Index('ix_lesson_topics_progress','student_topic_id','lesson_record_id'),
    )


class TopicEvent(Base):
    """Append-only snapshots. No current state is inferred from these events."""
    __tablename__ = 'topic_events'
    id: Mapped[int] = mapped_column(primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey('topics.id'))
    student_topic_id: Mapped[int | None] = mapped_column(ForeignKey('student_topics.id'))
    lesson_record_id: Mapped[int | None] = mapped_column(ForeignKey('lesson_records.id'))
    actor_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    kind: Mapped[str] = mapped_column(String(24))
    title_snapshot: Mapped[str] = mapped_column(String(180))
    before_json: Mapped[str] = mapped_column(Text, default='{}')
    after_json: Mapped[str] = mapped_column(Text, default='{}')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    __table_args__ = (Index('ix_topic_events_progress','student_topic_id','id'),Index('ix_topic_events_catalog','topic_id','id'),)


class UserDraft(Base):
    """Private, expiring form drafts. Never represent published domain data."""
    __tablename__ = 'user_drafts'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    draft_key: Mapped[str] = mapped_column(String(80))
    payload_json: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    __table_args__ = (UniqueConstraint('user_id','kind','draft_key',name='uq_user_draft_owner_key'),)


class UserNotificationState(Base):
    """Per-user read marker for a bounded, derived notification stream."""
    __tablename__ = 'user_notification_states'
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    notification_key: Mapped[str] = mapped_column(String(120), primary_key=True)
    read_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class LessonSeries(Base):
    __tablename__ = 'lesson_series'
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    student: Mapped[User] = relationship()
    weekdays: Mapped[str] = mapped_column(String(20))
    local_time: Mapped[time] = mapped_column(Time)
    timezone_name: Mapped[str] = mapped_column(String(80))
    interval_weeks: Mapped[int] = mapped_column(Integer,default=1)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    duration_minutes: Mapped[int] = mapped_column(Integer,default=60)
    meeting_url: Mapped[str] = mapped_column(String(500),default='')
    note: Mapped[str] = mapped_column(String(500),default='')
    state: Mapped[str] = mapped_column(String(12),default='active')
    version: Mapped[int] = mapped_column(Integer,default=1)
    creation_key: Mapped[str] = mapped_column(String(32),unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime,default=utc_now)
    __table_args__ = (CheckConstraint("state IN ('active','paused','ended')",name='series_state'),CheckConstraint('interval_weeks IN (1,2)',name='series_interval'),CheckConstraint('duration_minutes BETWEEN 15 AND 240',name='series_duration'),Index('ix_series_student_state','student_id','state'))


class ScheduleLock(Base):
    __tablename__ = 'schedule_lock'
    id: Mapped[int] = mapped_column(primary_key=True)
    revision: Mapped[int] = mapped_column(Integer,default=0)


class ScheduleEvent(Base):
    __tablename__ = 'schedule_events'
    id: Mapped[int] = mapped_column(primary_key=True)
    lesson_id: Mapped[int | None] = mapped_column(ForeignKey('lessons.id'),index=True)
    series_id: Mapped[int | None] = mapped_column(ForeignKey('lesson_series.id'),index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    kind: Mapped[str] = mapped_column(String(30))
    before_json: Mapped[str] = mapped_column(Text,default='{}')
    after_json: Mapped[str] = mapped_column(Text,default='{}')
    created_at: Mapped[datetime] = mapped_column(DateTime,default=utc_now)


class LessonCompletion(Base):
    __tablename__ = 'lesson_completions'
    lesson_id: Mapped[int] = mapped_column(primary_key=True)
    record_id: Mapped[int] = mapped_column(unique=True)
    student_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    __table_args__ = (ForeignKeyConstraint(['lesson_id','student_id'],['lessons.id','lessons.student_id']),ForeignKeyConstraint(['record_id','student_id'],['lesson_records.id','lesson_records.student_id']))


class ReminderPreference(Base):
    __tablename__ = 'reminder_preferences'
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'),primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean,default=False)
    minutes_before: Mapped[int] = mapped_column(Integer,default=30)
    message_format: Mapped[str] = mapped_column(String(12),default='compact')
    version: Mapped[int] = mapped_column(Integer,default=1)
    __table_args__ = (CheckConstraint('minutes_before IN (10,30,60)',name='reminder_minutes'),CheckConstraint("message_format IN ('compact','links')",name='reminder_format'))


class ReminderDelivery(Base):
    __tablename__ = 'reminder_deliveries'
    id: Mapped[int] = mapped_column(primary_key=True)
    lesson_id: Mapped[int] = mapped_column(ForeignKey('lessons.id'))
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    lesson_version: Mapped[int] = mapped_column(Integer)
    preference_version: Mapped[int] = mapped_column(Integer)
    due_at: Mapped[datetime] = mapped_column(DateTime,index=True)
    state: Mapped[str] = mapped_column(String(12),default='pending')
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    message_id: Mapped[str | None] = mapped_column(String(40))
    attempts: Mapped[int] = mapped_column(Integer,default=0)
    __table_args__ = (UniqueConstraint('lesson_id','user_id','lesson_version','preference_version',name='uq_reminder_version'),Index('ix_reminder_state_due','state','due_at'))

# Register separate private-message tables with the shared metadata.
from . import personal_models  # noqa: E402,F401

from . import attendance_models  # noqa: E402,F401

from . import finance_models  # noqa: E402,F401

from . import quiz_models  # noqa: E402,F401
