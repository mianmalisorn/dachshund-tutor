"""Attendance is independent of pedagogical notes and never implies a payment."""
from datetime import datetime
from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, utc_now

OUTCOMES = ('unknown', 'conducted', 'student_absent', 'teacher_absent', 'cancelled_student', 'cancelled_teacher', 'other')
CHECK = "outcome IN ('unknown','conducted','student_absent','teacher_absent','cancelled_student','cancelled_teacher','other')"

class LessonAttendance(Base):
    __tablename__ = 'lesson_attendance'
    lesson_id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    outcome: Mapped[str] = mapped_column(String(24))
    actual_minutes: Mapped[int | None] = mapped_column(Integer)
    comment: Mapped[str] = mapped_column(Text, default='')
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now)
    updated_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    __table_args__ = (
        ForeignKeyConstraint(['lesson_id','student_id'], ['lessons.id','lessons.student_id']),
        CheckConstraint(CHECK, name='attendance_outcome'),
        CheckConstraint('version >= 1', name='attendance_version'),
        CheckConstraint("actual_minutes IS NULL OR (outcome = 'conducted' AND actual_minutes BETWEEN 1 AND 1440)", name='attendance_minutes'),
        Index('ix_attendance_student_outcome','student_id','outcome'),
    )

class AttendanceEvent(Base):
    __tablename__ = 'attendance_events'
    id: Mapped[int] = mapped_column(primary_key=True)
    lesson_id: Mapped[int] = mapped_column(ForeignKey('lesson_attendance.lesson_id'))
    actor_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    request_key: Mapped[str] = mapped_column(String(32), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    before_outcome: Mapped[str] = mapped_column(String(24))
    after_outcome: Mapped[str] = mapped_column(String(24))
    before_minutes: Mapped[int | None] = mapped_column(Integer)
    after_minutes: Mapped[int | None] = mapped_column(Integer)
    before_comment: Mapped[str] = mapped_column(Text)
    after_comment: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(String(500))
    result_version: Mapped[int] = mapped_column(Integer)
    __table_args__ = (Index('ix_attendance_events_lesson','lesson_id','id'),)
