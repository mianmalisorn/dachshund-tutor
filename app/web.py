"""Shared dependencies and template helpers."""
from fastapi import Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from .config import ROOT
from .logic import STATUS_LABELS, is_late, latest_submission, status, to_input, to_local
from .models import User
from .security import get_csrf_token, require_csrf

TEMPLATES = Jinja2Templates(directory=str(ROOT / 'app' / 'templates'))


def db_session(request: Request):
    with request.app.state.sessionmaker() as session:
        yield session


def user_from_request(request: Request, db: Session) -> User | None:
    user_id = request.session.get('uid')
    if not isinstance(user_id, int):
        return None
    user = db.get(User, user_id)
    if (not user or not user.active or user.auth_version != request.session.get('version')):
        request.session.clear()
        return None
    return user


def require_user(request: Request, db: Session = Depends(db_session)) -> User:
    user = user_from_request(request, db)
    if not user:
        raise HTTPException(401, 'Пожалуйста, войдите')
    return user


def require_admin(request: Request, user: User = Depends(require_user)) -> User:
    if user.must_change_password:
        raise HTTPException(423, 'Сначала смените временный пароль')
    if user.role != 'admin':
        raise HTTPException(403, 'Только для преподавательницы')
    return user


def require_student(request: Request, user: User = Depends(require_user)) -> User:
    if user.must_change_password:
        raise HTTPException(423, 'Сначала смените временный пароль')
    if user.role != 'student':
        raise HTTPException(403, 'Только для ученика')
    return user


async def validated_form(request: Request):
    form = await request.form(max_files=request.app.state.settings.max_files_per_upload, max_fields=100)
    request.scope['parsed_form'] = form
    require_csrf(request, str(form.get('csrf', '')))
    return form


def render(request: Request, template_name: str, user: User | None = None, **kwargs):
    from .mascot import view_for
    tz_name = request.app.state.settings.timezone
    context = {
        'request': request, 'user': user, 'csrf': get_csrf_token(request),
        'public_brand': '/static/brand-approved.png' if (ROOT / 'app/static/brand-approved.png').is_file() else None,
        'localtime': lambda dt, fmt='%d.%m.%Y %H:%M': to_local(dt, tz_name, fmt),
        'inputtime': lambda dt: to_input(dt, tz_name),
        'status_of': status, 'status_labels': STATUS_LABELS,
        'latest_of': latest_submission, 'is_late': is_late,
        'notice': request.query_params.get('notice', ''),
        'upload_accept': ','.join(request.app.state.settings.allowed_extensions),
        'mascot': view_for(request,user) if template_name != 'error.html' and not kwargs.get('form_error') else None,
    }
    context.update(kwargs)
    if template_name=='account.html' and user and user.role=='admin' and not user.must_change_password:
        from .models import MascotPreference
        try:
            with request.app.state.sessionmaker() as db:
                prefs=db.get(MascotPreference,user.id)
                context['comments_enabled']=prefs.enabled if prefs else True
        except Exception:
            context['comments_enabled']=None
    return TEMPLATES.TemplateResponse(request=request, name=template_name, context=context)


def clean_text(raw, maximum: int, field: str, required: bool = True) -> str:
    val = str(raw or '').strip()
    if (required and not val) or len(val) > maximum:
        raise HTTPException(400, f'Поле «{field}»: требуется от {1 if required else 0} до {maximum} символов')
    return val


def parse_id(raw, field='ID') -> int | None:
    if raw in ('', None):
        return None
    try:
        value = int(raw)
        if value < 1:
            raise ValueError
        return value
    except (ValueError, TypeError):
        raise HTTPException(400, f'Некорректный {field}')
