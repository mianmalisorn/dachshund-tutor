"""Small transactional primitives shared by submission and review routes."""
from fastapi import HTTPException
from sqlalchemy import update
from .models import AssignmentTarget, Assignment


def claim_version(db, model, object_id, expected, *, advance=True):
    if model not in (AssignmentTarget, Assignment) or expected is None:
        raise HTTPException(409, 'Обновите страницу: отсутствует версия формы.')
    result = db.execute(update(model).where(model.id == object_id, model.version == expected)
                        .values(version=model.version + (1 if advance else 0)),
                        execution_options={'synchronize_session': False})
    if result.rowcount != 1:
        raise HTTPException(409, 'Данные уже изменились или форма отправлена повторно. Обновите страницу; сохранённая работа не потеряна.')
