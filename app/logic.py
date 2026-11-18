"""Pure business logic: timezone conversion, statuses and input validation."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit

from .models import AssignmentTarget, utc_now


def from_form_datetime(value: str, tz_name: str) -> datetime:
    try:
        naive = datetime.strptime(value, '%Y-%m-%dT%H:%M')
        return naive.replace(tzinfo=ZoneInfo(tz_name)).astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, KeyError) as exc:
        raise ValueError('Проверьте дату и время (формат: ГГГГ-ММ-ДД ЧЧ:ММ)') from exc


def to_local(dt: datetime | None, tz_name: str, fmt: str = '%d.%m.%Y %H:%M') -> str:
    if dt is None:
        return '—'
    return dt.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(tz_name)).strftime(fmt)


def to_input(dt: datetime, tz_name: str) -> str:
    return to_local(dt, tz_name, '%Y-%m-%dT%H:%M')


def latest_submission(target: AssignmentTarget):
    return max(target.submissions, key=lambda x: (x.created_at, x.id), default=None)


def status(target: AssignmentTarget, now: datetime | None = None) -> str:
    now = now or utc_now()
    latest = latest_submission(target)
    if not latest:
        return 'overdue' if target.assignment.due_at < now else 'todo'
    if latest.review_state == 'pending':
        return 'pending'
    if latest.review_state == 'checked':
        return 'checked'
    return 'revision'


STATUS_LABELS = {
    'todo': 'Нужно выполнить', 'overdue': 'Просрочено',
    'pending': 'Ждёт проверки', 'checked': 'Проверено',
    'revision': 'На доработке',
}


def is_late(target: AssignmentTarget) -> bool:
    latest = latest_submission(target)
    return bool(latest and latest.due_at_snapshot and latest.created_at > latest.due_at_snapshot)


def meeting_url(value: str) -> str:
    value = value.strip()
    parsed = urlsplit(value)
    if (parsed.scheme not in {'https', 'http'} or not parsed.hostname or parsed.username or parsed.password
            or len(value) > 500 or any(c.isspace() for c in value)):
        raise ValueError('Укажите корректную ссылку на видеовстречу (https://...)')
    if parsed.scheme != 'https':
        raise ValueError('Для видеозвонка нужна ссылка с https://')
    return value
