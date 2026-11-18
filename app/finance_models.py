"""Private, append-only finance journals. Money is integer kopecks, RUB only."""
from datetime import date, datetime
from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, UniqueConstraint, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, utc_now

class FinanceAccount(Base):
    __tablename__='finance_accounts'
    student_id: Mapped[int]=mapped_column(ForeignKey('users.id'),primary_key=True)
    version: Mapped[int]=mapped_column(default=0)
    __table_args__=(CheckConstraint('version >= 0'),)

class FinanceOperation(Base):
    __tablename__='finance_operations'
    id: Mapped[int]=mapped_column(primary_key=True)
    student_id: Mapped[int]=mapped_column(ForeignKey('users.id'),index=True)
    actor_id: Mapped[int]=mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime]=mapped_column(default=utc_now)
    effective_date: Mapped[date]
    kind: Mapped[str]=mapped_column(String(32))
    reason: Mapped[str]=mapped_column(Text)
    request_key: Mapped[str]=mapped_column(String(32),unique=True)
    request_hash: Mapped[str]=mapped_column(String(64))
    result_version: Mapped[int]
    summary: Mapped[str]=mapped_column(Text,default='')
    __table_args__=(UniqueConstraint('id','student_id'),Index('ix_finance_date_kind','effective_date','kind'),)

class StudentRate(Base):
    __tablename__='student_rates'
    id: Mapped[int]=mapped_column(primary_key=True)
    student_id: Mapped[int]=mapped_column(ForeignKey('users.id'),index=True)
    operation_id: Mapped[int]=mapped_column(ForeignKey('finance_operations.id'),unique=True)
    amount: Mapped[int]
    duration_minutes: Mapped[int | None]
    starts_on: Mapped[date]
    __table_args__=(CheckConstraint('amount > 0 AND amount <= 100000000000'),CheckConstraint('duration_minutes IS NULL OR duration_minutes BETWEEN 1 AND 1440'),)

class BillingPolicy(Base):
    __tablename__='billing_policies'
    id: Mapped[int]=mapped_column(primary_key=True)
    student_id: Mapped[int]=mapped_column(ForeignKey('users.id'),index=True)
    operation_id: Mapped[int]=mapped_column(ForeignKey('finance_operations.id'),unique=True)
    outcome: Mapped[str]=mapped_column(String(24))
    recommendation: Mapped[str]=mapped_column(String(12))
    __table_args__=(CheckConstraint("recommendation IN ('charge','free','manual')"),)

class LessonPackage(Base):
    __tablename__='lesson_packages'
    id: Mapped[int]=mapped_column(primary_key=True)
    student_id: Mapped[int]=mapped_column(ForeignKey('users.id'),index=True)
    operation_id: Mapped[int]=mapped_column(ForeignKey('finance_operations.id'),unique=True)
    title: Mapped[str]=mapped_column(String(160))
    quantity: Mapped[int]
    price: Mapped[int]
    purchased_on: Mapped[date]
    expires_on: Mapped[date | None]
    duration_minutes: Mapped[int | None]
    status: Mapped[str]=mapped_column(String(12),default='active')
    __table_args__=(UniqueConstraint('id','student_id'),CheckConstraint('quantity BETWEEN 1 AND 10000'),CheckConstraint('price > 0 AND price <= 100000000000'),CheckConstraint("status IN ('active','closed')"),CheckConstraint('expires_on IS NULL OR expires_on >= purchased_on'),CheckConstraint('duration_minutes IS NULL OR duration_minutes BETWEEN 1 AND 1440'),)

class FinancialEntry(Base):
    __tablename__='financial_entries'
    id: Mapped[int]=mapped_column(primary_key=True)
    student_id: Mapped[int]=mapped_column(ForeignKey('users.id'),index=True)
    operation_id: Mapped[int]=mapped_column(ForeignKey('finance_operations.id'),index=True)
    kind: Mapped[str]=mapped_column(String(16))
    amount: Mapped[int]
    currency: Mapped[str]=mapped_column(String(3),default='RUB')
    original_id: Mapped[int | None]=mapped_column(ForeignKey('financial_entries.id'))
    lesson_id: Mapped[int | None]
    package_id: Mapped[int | None]
    __table_args__=(UniqueConstraint('id','student_id'),
        ForeignKeyConstraint(['operation_id','student_id'],['finance_operations.id','finance_operations.student_id']),
        ForeignKeyConstraint(['lesson_id','student_id'],['lessons.id','lessons.student_id']),
        ForeignKeyConstraint(['package_id','student_id'],['lesson_packages.id','lesson_packages.student_id']),
        ForeignKeyConstraint(['original_id','student_id'],['financial_entries.id','financial_entries.student_id']),
        CheckConstraint('amount > 0 AND amount <= 100000000000'),CheckConstraint("currency = 'RUB'"),
        CheckConstraint("kind IN ('charge','payment','refund','credit','payment_void')"),
        CheckConstraint("(kind IN ('charge','payment') AND original_id IS NULL) OR (kind IN ('refund','credit','payment_void') AND original_id IS NOT NULL)"))

class PaymentAllocation(Base):
    __tablename__='payment_allocations'
    id: Mapped[int]=mapped_column(primary_key=True)
    student_id: Mapped[int]=mapped_column(ForeignKey('users.id'))
    operation_id: Mapped[int]=mapped_column(ForeignKey('finance_operations.id'))
    payment_id: Mapped[int]
    charge_id: Mapped[int]
    amount: Mapped[int]  # Positive allocates; negative releases. Never delete a prior allocation.
    __table_args__=(ForeignKeyConstraint(['payment_id','student_id'],['financial_entries.id','financial_entries.student_id']),ForeignKeyConstraint(['charge_id','student_id'],['financial_entries.id','financial_entries.student_id']),CheckConstraint('amount != 0'),Index('ix_alloc_payment_charge','payment_id','charge_id'),)

class LessonSettlement(Base):
    __tablename__='lesson_settlements'
    id: Mapped[int]=mapped_column(primary_key=True)
    student_id: Mapped[int]=mapped_column(ForeignKey('users.id'))
    lesson_id: Mapped[int]
    operation_id: Mapped[int]=mapped_column(ForeignKey('finance_operations.id'),unique=True)
    active: Mapped[bool]=mapped_column(default=True)
    method: Mapped[str]=mapped_column(String(12))
    charge_id: Mapped[int | None]=mapped_column(ForeignKey('financial_entries.id'))
    package_id: Mapped[int | None]=mapped_column(ForeignKey('lesson_packages.id'))
    rate_id: Mapped[int | None]=mapped_column(ForeignKey('student_rates.id'))
    outcome: Mapped[str]=mapped_column(String(24))
    actual_minutes: Mapped[int | None]
    planned_minutes: Mapped[int]
    starts_at: Mapped[datetime]
    amount_snapshot: Mapped[int | None]
    __table_args__=(ForeignKeyConstraint(['lesson_id','student_id'],['lessons.id','lessons.student_id']),
        ForeignKeyConstraint(['charge_id','student_id'],['financial_entries.id','financial_entries.student_id']),
        ForeignKeyConstraint(['package_id','student_id'],['lesson_packages.id','lesson_packages.student_id']),
        CheckConstraint("(method = 'charge' AND charge_id IS NOT NULL AND package_id IS NULL AND amount_snapshot >= 0) OR (method = 'package' AND package_id IS NOT NULL AND charge_id IS NULL AND amount_snapshot IS NULL) OR (method IN ('free','defer') AND charge_id IS NULL AND package_id IS NULL AND amount_snapshot IS NULL)"),
        CheckConstraint("method IN ('charge','package','free','defer')"),
        Index('ix_settlement_student_active','student_id','active'),
        Index('uq_active_lesson_settlement','lesson_id',unique=True,sqlite_where=__import__('sqlalchemy').text('active = 1')))

class PackageEntry(Base):
    __tablename__='package_entries'
    id: Mapped[int]=mapped_column(primary_key=True)
    student_id: Mapped[int]=mapped_column(ForeignKey('users.id'))
    package_id: Mapped[int]
    operation_id: Mapped[int]=mapped_column(ForeignKey('finance_operations.id'),index=True)
    settlement_id: Mapped[int | None]=mapped_column(ForeignKey('lesson_settlements.id'))
    kind: Mapped[str]=mapped_column(String(12))
    quantity: Mapped[int]
    __table_args__=(Index('ix_package_entries_package','package_id','id'),ForeignKeyConstraint(['package_id','student_id'],['lesson_packages.id','lesson_packages.student_id']),CheckConstraint('quantity != 0'),CheckConstraint("kind IN ('grant','use','return','adjust')"),)
