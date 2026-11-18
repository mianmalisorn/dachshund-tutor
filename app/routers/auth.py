from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, func, select, or_, update
from sqlalchemy.orm import Session
from ..models import LoginAttempt, User, utc_now
from ..security import check_password, password_hash
from ..mascot import record_event
from ..web import db_session, render, require_user, user_from_request, validated_form
from ..web import require_admin
from ..models import MascotPreference, ReminderPreference
from ..personal_models import PersonalRecipient

router = APIRouter()

def home_for(user):
    return {'admin':'/admin','student':'/student','sender':'/personal'}.get(user.role,'/account')



@router.get('/')
def root(request: Request, db: Session = Depends(db_session)):
    user = user_from_request(request, db)
    return RedirectResponse('/login' if not user else home_for(user), status_code=303)


@router.get('/login')
def login_page(request: Request, db: Session = Depends(db_session)):
    current = user_from_request(request, db)
    if current:
        return RedirectResponse(home_for(current), status_code=303)
    return render(request, 'auth/login.html')


@router.post('/login')
async def login(request: Request, db: Session = Depends(db_session)):
    form = await validated_form(request)
    username = str(form.get('username', '')).strip().lower()[:60]
    password = str(form.get('password', ''))
    # Do not trust proxy headers without correctly configured trusted proxy infrastructure.
    ip = request.client.host if request.client else 'unknown'
    cutoff = utc_now() - timedelta(minutes=15)
    db.execute(delete(LoginAttempt).where(LoginAttempt.at < utc_now() - timedelta(days=2)))
    attempts = db.scalar(select(func.count()).select_from(LoginAttempt).where(
        or_(LoginAttempt.username == username, LoginAttempt.ip_address == ip), LoginAttempt.at >= cutoff)) or 0
    if attempts >= 6:
        db.commit()
        return render(request, 'auth/login.html', error='Слишком много попыток. Повторите через 15 минут.')
    user = db.scalar(select(User).where(User.username == username))
    # Execute a dummy hash on unknown account to make timing more similar.
    valid = check_password(password, user.password_hash) if user else check_password(password, DUMMY_HASH)
    if not user or not valid or not user.active:
        db.add(LoginAttempt(username=username, ip_address=ip))
        db.commit()
        return render(request, 'auth/login.html', error='Неверный логин или пароль')
    db.execute(delete(LoginAttempt).where(LoginAttempt.username == username, LoginAttempt.ip_address == ip))
    previous_login = user.last_login_at
    user.last_login_at = utc_now()
    db.commit()
    request.session.clear()
    request.session['uid'] = user.id
    request.session['version'] = user.auth_version
    record_event(request,user,'session.admin_login',returned_after_absence=bool(previous_login and utc_now()-previous_login>=timedelta(days=7)))
    return RedirectResponse('/account' if user.must_change_password else home_for(user), status_code=303)


# Precomputed valid-form hash for a dummy password to mitigate account enumeration timing.
DUMMY_HASH = 'pbkdf2_sha256$600000$AAAAAAAAAAAAAAAAAAAAAA==$fBXzcICIx-xB5dg8UdoqcwK_EsNbJRfZXLCIEcJIuCw='


@router.post('/logout')
async def logout(request: Request):
    await validated_form(request)
    request.session.clear()
    return RedirectResponse('/login?notice=logged_out', status_code=303)


@router.get('/account')
def account(request: Request, user: User = Depends(require_user), db: Session = Depends(db_session)):
    return account_page(request, user, db)


def account_page(request: Request, user: User, db: Session, error: str | None = None):
    integrations = None
    if user.role == 'admin' and not user.must_change_password:
        from ..reminders import TelegramConfig
        from ..personal_telegram import PersonalConfig
        calendar = getattr(request.app.state, 'calendar_telegram_config', None) or TelegramConfig.from_env()
        personal = getattr(request.app.state, 'personal_config', None) or PersonalConfig.from_env()
        calendar_pref = db.get(ReminderPreference, user.id)
        personal_pref = db.get(PersonalRecipient, user.id)
        calendar_ready = calendar.configured and calendar.admin_id == user.id
        personal_ready = (personal.configured and personal_pref is not None
                          and personal_pref.bot_id == personal.bot_id
                          and personal_pref.environment == personal.environment)
        integrations = {
            'calendar': ('Включены' if calendar_ready and calendar.enabled and calendar_pref and calendar_pref.enabled
                         else 'Подключено, отправка выключена' if calendar_ready else 'Не подключено'),
            'personal': ('Включены' if personal_ready and personal.enabled and personal_pref.enabled
                         else 'Подключено, отправка выключена' if personal_ready else 'Не подключено'),
        }
    return render(request, 'account.html', user=user, error=error, integrations=integrations)


@router.post('/account/password')
async def change_password(request: Request, user: User = Depends(require_user), db: Session = Depends(db_session)):
    form = await validated_form(request)
    old = str(form.get('old_password', ''))
    new = str(form.get('new_password', ''))
    if not check_password(old, user.password_hash):
        return account_page(request, user, db, error='Старый пароль неверный')
    try:
        new_hash = password_hash(new)
    except ValueError as exc:
        return account_page(request, user, db, error=str(exc))
    user.password_hash = new_hash
    user.must_change_password = False
    user.auth_version += 1
    db.commit()
    request.session['version'] = user.auth_version
    return RedirectResponse('/account?notice=password_changed', status_code=303)


@router.post('/account/comments')
async def comments(request: Request, user: User = Depends(require_admin), db: Session = Depends(db_session)):
    form=await validated_form(request)
    if set(form)-{'csrf','enabled'}:
        raise HTTPException(400, 'Доступен только переключатель комментариев')
    from ..mascot import preference
    preference(db,user.id)
    db.execute(update(MascotPreference).where(MascotPreference.user_id==user.id).values(
        enabled=form.get('enabled')=='yes',version=MascotPreference.version+1))
    db.commit()
    return RedirectResponse('/account?notice=saved',status_code=303)
