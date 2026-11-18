"""Names safe to show to students and LiveKit participants."""

from .models import User


PUBLIC_TEACHER_NAME = 'Эльвина Равильевна'


def public_display_name(user: User | None) -> str:
    if user is None:
        return 'Пользователь'
    if user.role == 'admin':
        return PUBLIC_TEACHER_NAME
    return user.display_name
