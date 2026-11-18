"""Personal messages are deliberately unrelated to pupils, lessons and teaching revisions."""
from datetime import datetime
from sqlalchemy import String, Text, DateTime, Boolean, ForeignKey, UniqueConstraint, Index, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, utc_now

class PersonalRecipient(Base):
    __tablename__ = 'personal_recipients'
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), primary_key=True)
    environment: Mapped[str] = mapped_column(String(20))
    bot_id: Mapped[str] = mapped_column(String(30))
    chat_id: Mapped[str] = mapped_column(String(30))
    verified_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(default=1)
    consented_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (UniqueConstraint('environment','bot_id','chat_id',name='uq_personal_binding'),)

class PersonalGrant(Base):
    __tablename__ = 'personal_grants'
    recipient_id: Mapped[int] = mapped_column(ForeignKey('users.id'), primary_key=True)
    sender_id: Mapped[int] = mapped_column(ForeignKey('users.id'), primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(default=1)

class PersonalReminder(Base):
    __tablename__ = 'personal_reminders'
    id: Mapped[int] = mapped_column(primary_key=True)
    sender_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    recipient_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    chat_id: Mapped[str] = mapped_column(String(30))
    bot_id: Mapped[str] = mapped_column(String(30))
    environment: Mapped[str] = mapped_column(String(20))
    body: Mapped[str] = mapped_column(Text)
    due_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    timezone_name: Mapped[str] = mapped_column(String(60))
    state: Mapped[str] = mapped_column(String(20), default='pending')
    version: Mapped[int] = mapped_column(default=1)
    recipient_version: Mapped[int] = mapped_column()
    grant_version: Mapped[int] = mapped_column()
    creation_key: Mapped[str] = mapped_column(String(32), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    __table_args__ = (CheckConstraint('length(body) BETWEEN 1 AND 1000',name='personal_body_length'),)

class PersonalDelivery(Base):
    __tablename__ = 'personal_deliveries'
    id: Mapped[int] = mapped_column(primary_key=True)
    reminder_id: Mapped[int] = mapped_column(ForeignKey('personal_reminders.id'), index=True)
    reminder_version: Mapped[int] = mapped_column()
    due_at: Mapped[datetime] = mapped_column(DateTime)
    state: Mapped[str] = mapped_column(String(20), default='pending')
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    message_id: Mapped[str | None] = mapped_column(String(60))
    attempts: Mapped[int] = mapped_column(default=0)
    __table_args__ = (UniqueConstraint('reminder_id','reminder_version',name='uq_personal_delivery'),Index('ix_personal_queue','state','due_at'))

class PersonalEvent(Base):
    __tablename__ = 'personal_events'
    id: Mapped[int] = mapped_column(primary_key=True)
    reminder_id: Mapped[int] = mapped_column(ForeignKey('personal_reminders.id'), index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    kind: Mapped[str] = mapped_column(String(30))
    snapshot_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

class TelegramLink(Base):
    __tablename__ = 'telegram_links'
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    environment: Mapped[str] = mapped_column(String(20))
    bot_id: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)

class TelegramCursor(Base):
    __tablename__ = 'telegram_cursors'
    scope: Mapped[str] = mapped_column(String(80), primary_key=True)
    update_id: Mapped[int] = mapped_column(default=-1)

class PersonalInstallation(Base):
    __tablename__ = 'personal_installation'
    id: Mapped[int] = mapped_column(primary_key=True)
    environment: Mapped[str] = mapped_column(String(20))
    nonce: Mapped[str] = mapped_column(String(64))
