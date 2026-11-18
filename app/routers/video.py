"""Authenticated Tutor endpoints for one LiveKit Cloud meeting at a time."""
import asyncio
import logging
import re
import time
from datetime import timedelta
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..livekit_provider import TOKEN_TTL_SECONDS
from ..models import User, VideoMeeting, VideoMessage, utc_now
from ..public_identity import PUBLIC_TEACHER_NAME, public_display_name
from ..web import db_session, parse_id, render, require_admin, require_user, validated_form

router = APIRouter(prefix='/api/video/meetings')
page_router = APIRouter(prefix='/video/meetings')
logger = logging.getLogger(__name__)
START_TIMEOUT = timedelta(minutes=2)
PROVIDER_TIMEOUT_SECONDS = 12


def _provider(request: Request):
    settings = request.app.state.settings
    ws = urlsplit(settings.livekit_ws_url)
    http = urlsplit(settings.livekit_http_url)
    if (request.app.state.video_provider is None or ws.scheme != 'wss' or http.scheme != 'https'
            or not ws.hostname or ws.hostname != http.hostname or ws.username or http.username
            or ws.password or http.password or ws.query or http.query or ws.fragment or http.fragment):
        raise HTTPException(503, 'Видеозвонки пока не настроены')
    return request.app.state.video_provider


def _view(meeting: VideoMeeting) -> dict:
    return {'id': meeting.id, 'student_id': meeting.student_id, 'status': meeting.state,
            'created_at': meeting.created_at.isoformat() + 'Z'}


def _allowed(meeting: VideoMeeting, user: User) -> bool:
    return ((user.role == 'admin' and user.id == meeting.teacher_id)
            or (user.role == 'student' and user.id == meeting.student_id
                and user.learning_status != 'archived'))


def _meeting(db: Session, meeting_id: str, user: User) -> VideoMeeting:
    if user.must_change_password:
        raise HTTPException(423, 'Сначала смените временный пароль')
    meeting = db.get(VideoMeeting, meeting_id, populate_existing=True)
    if not meeting or not _allowed(meeting, user):
        raise HTTPException(404, 'Встреча не найдена')
    return meeting


def _chat_meeting(db: Session, meeting_id: str, user: User) -> VideoMeeting:
    if user.must_change_password:
        raise HTTPException(423, 'Сначала смените временный пароль')
    meeting = db.get(VideoMeeting, meeting_id, populate_existing=True)
    if not meeting or not ((user.role == 'admin' and user.id == meeting.teacher_id)
                           or (user.role == 'student' and user.id == meeting.student_id)):
        raise HTTPException(404, 'Встреча не найдена')
    return meeting


def _message_view(message: VideoMessage, author_name: str) -> dict:
    return {'id': message.id, 'author_id': message.author_id, 'author_name': author_name,
            'text': message.text, 'created_at': message.created_at.isoformat() + 'Z'}


@page_router.get('/history')
def meeting_history(request: Request, db: Session = Depends(db_session),
                    user: User = Depends(require_user)):
    if user.must_change_password or user.role not in ('admin', 'student'):
        raise HTTPException(403, 'Нет доступа к истории видеозанятий')
    page = parse_id(request.query_params.get('page', '1'), 'страницы')
    if page is None or page > 10000:
        raise HTTPException(400, 'Некорректная страница')
    student_id = None
    query = select(VideoMeeting).where(VideoMeeting.state == 'ended')
    if user.role == 'admin':
        query = query.where(VideoMeeting.teacher_id == user.id)
        student_id = parse_id(request.query_params.get('student_id'), 'ученика')
        if student_id is not None:
            query = query.where(VideoMeeting.student_id == student_id)
    else:
        query = query.where(VideoMeeting.student_id == user.id)
    rows = list(db.scalars(query.order_by(VideoMeeting.ended_at.desc(), VideoMeeting.id.desc())
                           .offset((page - 1) * 20).limit(21)))
    student_ids = {meeting.student_id for meeting in rows[:20]}
    student_names = {student.id: student.display_name for student in db.scalars(
        select(User).where(User.id.in_(student_ids)))} if student_ids else {}
    return render(request, 'video/history.html', user=user, meetings=rows[:20],
                  page=page, has_next=len(rows) > 20, student_id=student_id,
                  student_names=student_names)


@page_router.get('/{meeting_id}')
def meeting_page(request: Request, meeting_id: str, db: Session = Depends(db_session),
                 user: User = Depends(require_user)):
    meeting = _chat_meeting(db, meeting_id, user)
    student = db.get(User, meeting.student_id)
    counterpart = student if user.role == 'admin' else db.get(User, meeting.teacher_id)
    teacher = db.get(User, meeting.teacher_id)
    counterpart_name = (public_display_name(counterpart).strip() if counterpart else
                        PUBLIC_TEACHER_NAME if user.role == 'student' else None)
    chat_writable = (meeting.state == 'active' and student is not None and student.active
                     and student.role == 'student' and student.learning_status != 'archived')
    return render(request, 'video/meeting.html', user=user, meeting=meeting,
                  counterpart_name=counterpart_name,
                  teacher_name=public_display_name(teacher) if teacher else PUBLIC_TEACHER_NAME,
                  student_name=student.display_name if student else 'Ученик',
                  chat_writable=chat_writable)


@router.get('/{meeting_id}/messages')
def messages(meeting_id: str, request: Request, db: Session = Depends(db_session),
             user: User = Depends(require_user)):
    _chat_meeting(db, meeting_id, user)
    params = request.query_params
    if 'before_id' in params and 'after_id' in params:
        raise HTTPException(400, 'Выберите один курсор сообщений')
    try:
        limit = int(params.get('limit', '30'))
        before_id = int(params['before_id']) if 'before_id' in params else None
        after_id = int(params['after_id']) if 'after_id' in params else None
    except (TypeError, ValueError):
        raise HTTPException(400, 'Некорректный курсор сообщений') from None
    if not 1 <= limit <= 100 or (before_id is not None and before_id < 1) or (after_id is not None and after_id < 0):
        raise HTTPException(400, 'Некорректная страница сообщений')

    query = (select(VideoMessage, User)
             .join(User, User.id == VideoMessage.author_id)
             .where(VideoMessage.meeting_id == meeting_id))
    if before_id is not None:
        query = query.where(VideoMessage.id < before_id)
    elif after_id is not None:
        query = query.where(VideoMessage.id > after_id)
    descending = after_id is None
    query = query.order_by(VideoMessage.id.desc() if descending else VideoMessage.id.asc()).limit(limit + 1)
    rows = db.execute(query).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    if descending:
        rows.reverse()
    return {'messages': [_message_view(message, public_display_name(author)) for message, author in rows],
            'has_more': has_more}


@router.post('/{meeting_id}/messages')
async def send_message(request: Request, meeting_id: str, db: Session = Depends(db_session),
                       user: User = Depends(require_user)):
    form = await validated_form(request)
    raw_text = form.get('text')
    client_key = form.get('client_key')
    if not isinstance(raw_text, str) or len(raw_text) > 2000:
        raise HTTPException(400, 'Сообщение должно содержать от 1 до 2000 символов')
    body = raw_text.replace('\r\n', '\n').strip()
    if not body or '\x00' in body:
        raise HTTPException(400, 'Сообщение должно содержать от 1 до 2000 символов')
    if not isinstance(client_key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', client_key):
        raise HTTPException(400, 'Некорректный ключ отправки сообщения')

    db.commit()
    return await run_in_threadpool(
        _store_message, request.app.state.sessionmaker, meeting_id, user.id,
        request.session.get('version'), body, client_key)


def _store_message(sessionmaker, meeting_id: str, author_id: int, auth_version: int,
                   body: str, client_key: str) -> dict:
    with sessionmaker() as db:
        # A token refresh can hold SQLite's writer lock during a 12-second
        # Cloud call. Wait in this worker thread without blocking the event loop.
        db.execute(text('PRAGMA busy_timeout=13000'))
        try:
            return _store_message_transaction(db, meeting_id, author_id, auth_version, body, client_key)
        finally:
            db.rollback()
            db.execute(text('PRAGMA busy_timeout=5000'))
            db.commit()


def _store_message_transaction(db: Session, meeting_id: str, author_id: int, auth_version: int,
                               body: str, client_key: str) -> dict:

    # Serialize the active-state check and insert against teacher end requests.
    db.execute(text('BEGIN IMMEDIATE'))
    try:
        author = db.get(User, author_id, populate_existing=True)
        if (not author or not author.active or author.auth_version != auth_version):
            raise HTTPException(401, 'Пожалуйста, войдите')
        meeting = _chat_meeting(db, meeting_id, author)
        prior = db.scalar(select(VideoMessage).where(
            VideoMessage.meeting_id == meeting_id, VideoMessage.author_id == author.id,
            VideoMessage.client_key == client_key))
        if prior:
            if prior.text != body:
                raise HTTPException(409, 'Ключ отправки уже использован для другого сообщения')
            result = _message_view(prior, public_display_name(author))
            db.commit()
            return result
        student = db.get(User, meeting.student_id, populate_existing=True)
        if (meeting.state != 'active' or meeting.lease_slot != 1 or not student or not student.active
                or student.role != 'student' or student.learning_status == 'archived'):
            raise HTTPException(409, 'Встреча завершена или сообщения больше недоступны')
        message = VideoMessage(meeting_id=meeting_id, author_id=author.id,
                               text=body, client_key=client_key, created_at=utc_now())
        db.add(message)
        db.commit()
        return _message_view(message, public_display_name(author))
    except Exception:
        db.rollback()
        raise


async def _cloud(awaitable, stage: str):
    try:
        return await asyncio.wait_for(awaitable, PROVIDER_TIMEOUT_SECONDS)
    except Exception as exc:
        logger.warning('LiveKit operation failed stage=%s error_type=%s', stage, type(exc).__name__)
        raise HTTPException(503, 'Видеосервис временно недоступен. Повторите запрос.') from None


async def _finish_ending(db: Session, provider, meeting: VideoMeeting) -> VideoMeeting:
    db.refresh(meeting)
    if meeting.state == 'ended':
        return meeting
    if meeting.state != 'ending':
        raise HTTPException(409, 'Встреча ещё не завершается')
    if not meeting.revocations_complete:
        cutoff = int(time.time()) + 30
        for identity in (f'tutor-admin-{meeting.teacher_id}', f'tutor-student-{meeting.student_id}'):
            await _cloud(provider.revoke_participant_token(
                meeting.room_name, identity, revoke_token_ts=cutoff), 'revoke')
        meeting.revocations_complete = True
        meeting.updated_at = utc_now()
        db.commit()
    if await _cloud(provider.room_exists(meeting.room_name), 'inspect'):
        try:
            await _cloud(provider.delete_room(meeting.room_name), 'delete')
        except HTTPException:
            if await _cloud(provider.room_exists(meeting.room_name), 'verify_delete'):
                raise
    if await _cloud(provider.room_exists(meeting.room_name), 'verify_delete'):
        raise HTTPException(503, 'Завершение видеозвонка ещё не подтверждено. Повторите запрос.')
    meeting.state = 'ended'
    meeting.lease_slot = None
    meeting.ended_at = utc_now()
    meeting.updated_at = meeting.ended_at
    db.commit()
    return meeting


@router.post('')
async def start(request: Request, db: Session = Depends(db_session), teacher: User = Depends(require_admin)):
    form = await validated_form(request)
    student_id = parse_id(form.get('student_id'), 'ученика')
    if student_id is None:
        raise HTTPException(400, 'Выберите ученика')
    student = db.get(User, student_id)
    if not student or student.role != 'student' or not student.active or student.learning_status != 'active':
        raise HTTPException(404, 'Активный ученик не найден')
    provider = _provider(request)
    start_key = request.headers.get('Idempotency-Key')
    if start_key is not None and (not 1 <= len(start_key) <= 80 or not start_key.isascii()):
        raise HTTPException(400, 'Некорректный ключ повтора')
    if start_key:
        prior = db.scalar(select(VideoMeeting).where(VideoMeeting.start_key == start_key))
        if prior:
            if prior.teacher_id != teacher.id or prior.student_id != student_id:
                raise HTTPException(409, 'Ключ повтора уже использован')
            return _view(prior)
    held = db.scalar(select(VideoMeeting).where(VideoMeeting.lease_slot == 1))
    if held and held.state == 'starting' and utc_now() - held.updated_at > START_TIMEOUT:
        held.state = 'ending'
        held.updated_at = utc_now()
        db.commit()
        await _finish_ending(db, provider, held)
        held = None
    if held and held.state == 'ending':
        await _finish_ending(db, provider, held)
        held = None
    if held:
        if held.state == 'active' and held.student_id == student_id and held.teacher_id == teacher.id:
            return _view(held)
        raise HTTPException(409, 'Уже идёт другой видеозвонок')

    meeting_id = str(uuid4())
    meeting = VideoMeeting(id=meeting_id, room_name='tutor_' + uuid4().hex,
                           teacher_id=teacher.id, student_id=student_id,
                           state='starting', lease_slot=1, start_key=start_key,
                           created_at=utc_now(), updated_at=utc_now())
    db.add(meeting)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if start_key:
            prior = db.scalar(select(VideoMeeting).where(VideoMeeting.start_key == start_key))
            if prior:
                if prior.teacher_id == teacher.id and prior.student_id == student_id:
                    return _view(prior)
                raise HTTPException(409, 'Ключ повтора уже использован') from None
        held = db.scalar(select(VideoMeeting).where(VideoMeeting.lease_slot == 1))
        if held and held.state == 'active' and held.student_id == student_id and held.teacher_id == teacher.id:
            return _view(held)
        raise HTTPException(409, 'Уже идёт другой видеозвонок') from None
    try:
        await _cloud(provider.ensure_room(meeting.room_name), 'create')
    except HTTPException:
        meeting.state = 'ending'
        meeting.updated_at = utc_now()
        db.commit()
        try:
            await _finish_ending(db, provider, meeting)
        except HTTPException:
            pass
        raise
    db.refresh(meeting)
    if meeting.state != 'starting':
        raise HTTPException(409, 'Состояние встречи изменилось')
    meeting.state = 'active'
    meeting.updated_at = utc_now()
    db.commit()
    return _view(meeting)


@router.get('/current')
def current(request: Request, db: Session = Depends(db_session), user: User = Depends(require_user)):
    if user.must_change_password or user.role not in ('admin', 'student'):
        raise HTTPException(403, 'Нет доступа к видеозвонкам')
    meeting = db.scalar(select(VideoMeeting).where(VideoMeeting.lease_slot == 1))
    return {'meeting': _view(meeting) if meeting and _allowed(meeting, user) else None}


@router.get('/{meeting_id}')
def state(meeting_id: str, db: Session = Depends(db_session), user: User = Depends(require_user)):
    return _view(_meeting(db, meeting_id, user))


@router.post('/{meeting_id}/token')
@router.post('/{meeting_id}/token/refresh')
async def token(request: Request, meeting_id: str, db: Session = Depends(db_session),
                user: User = Depends(require_user)):
    await validated_form(request)
    if user.must_change_password:
        raise HTTPException(423, 'Сначала смените временный пароль')
    provider = _provider(request)
    meeting = _meeting(db, meeting_id, user)
    if meeting.state != 'active':
        raise HTTPException(409, 'Встреча уже недоступна')
    # SQLite reserves the writer lock before Cloud room reconciliation and
    # signing. End cannot delete a room while token issuance recreates it.
    db.commit()
    db.execute(text('BEGIN IMMEDIATE'))
    try:
        meeting = _meeting(db, meeting_id, user)
        fresh_user = db.get(User, user.id, populate_existing=True)
        student = db.get(User, meeting.student_id, populate_existing=True)
        if (meeting.state != 'active' or meeting.lease_slot != 1 or not fresh_user.active
                or fresh_user.must_change_password or not _allowed(meeting, fresh_user)
                or not student or not student.active or student.role != 'student'
                or student.learning_status == 'archived'):
            raise HTTPException(409, 'Встреча уже недоступна')
        await _cloud(provider.ensure_room(meeting.room_name), 'ensure')
        identity = f'tutor-{fresh_user.role}-{fresh_user.id}'
        try:
            jwt = provider.participant_token(meeting.room_name, identity, public_display_name(fresh_user))
        except Exception as exc:
            logger.warning('LiveKit token signing failed error_type=%s', type(exc).__name__)
            raise HTTPException(503, 'Не удалось подключиться к видеосервису. Повторите запрос.') from None
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {'token': jwt, 'url': request.app.state.settings.livekit_ws_url,
            'expires_in': TOKEN_TTL_SECONDS}


@router.post('/{meeting_id}/end')
async def end(request: Request, meeting_id: str, db: Session = Depends(db_session),
              teacher: User = Depends(require_admin)):
    await validated_form(request)
    provider = _provider(request)
    meeting = _meeting(db, meeting_id, teacher)
    if meeting.state == 'ended':
        return _view(meeting)
    if meeting.state == 'starting':
        raise HTTPException(409, 'Встреча ещё создаётся. Повторите запрос.')
    if meeting.state == 'active':
        meeting.state = 'ending'
        meeting.updated_at = utc_now()
        db.commit()
    meeting = await _finish_ending(db, provider, meeting)
    return _view(meeting)
