"""Lifecycle changes never delete the account or its work."""
from fastapi import HTTPException
from sqlalchemy import update
from .models import StudentStatusEvent, User

STATUS_LABELS = {'active': 'Активен', 'paused': 'На паузе', 'archived': 'В архиве'}


def change_status(db, student, status, access, actor_id, expected_version):
    if status not in STATUS_LABELS:
        raise HTTPException(400, 'Неизвестный статус ученика')
    result = db.execute(update(User).where(
        User.id == student.id, User.role == 'student', User.auth_version == expected_version
    ).values(learning_status=status, active=access, auth_version=User.auth_version + 1))
    if result.rowcount != 1:
        raise HTTPException(409, 'Карточка уже изменена. Обновите страницу.')
    db.add(StudentStatusEvent(student_id=student.id, status=status,
                              access_enabled=access, changed_by=actor_id))
