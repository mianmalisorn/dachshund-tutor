"""Схема БД. Текущая версия начальная; для эволюции нужна Alembic-миграция."""
from datetime import datetime, timezone
from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String,
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
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    auth_version: Mapped[int] = mapped_column(Integer, default=1)
    folder_id: Mapped[int | None] = mapped_column(ForeignKey('student_folders.id', ondelete='SET NULL'))
    folder: Mapped[StudentFolder | None] = relationship()
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    __table_args__ = (CheckConstraint("role IN ('admin', 'student')", name='role_check'),)


class Assignment(Base):
    __tablename__ = 'assignments'
    id: Mapped[int] = mapped_column(primary_key=True)
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
    review_state: Mapped[str] = mapped_column(String(12), default='pending')
    review_comment: Mapped[str] = mapped_column(Text, default='')
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    attachments: Mapped[list['Attachment']] = relationship(
        primaryjoin="and_(Submission.id==Attachment.submission_id, Attachment.assignment_id.is_(None))",
        viewonly=True,
    )
    __table_args__ = (CheckConstraint("review_state IN ('pending','checked','revision')", name='review_state_check'),)


class Attachment(Base):
    __tablename__ = 'attachments'
    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int | None] = mapped_column(ForeignKey('assignments.id', ondelete='CASCADE'))
    submission_id: Mapped[int | None] = mapped_column(ForeignKey('submissions.id', ondelete='CASCADE'))
    filename: Mapped[str] = mapped_column(String(255))
    storage_name: Mapped[str] = mapped_column(String(64), unique=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    __table_args__ = (CheckConstraint(
        '(assignment_id IS NOT NULL AND submission_id IS NULL) OR '
        '(assignment_id IS NULL AND submission_id IS NOT NULL)', name='one_attachment_owner'
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
    __table_args__ = (CheckConstraint('duration_minutes BETWEEN 15 AND 240', name='valid_lesson_duration'),)


class LoginAttempt(Base):
    __tablename__ = 'login_attempts'
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(60), index=True)
    ip_address: Mapped[str] = mapped_column(String(100))
    at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    __table_args__ = (Index('ix_login_attempt_user_time', 'username', 'at'),)
