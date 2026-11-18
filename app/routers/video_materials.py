"""Meeting-scoped teaching materials in Tutor's existing private upload store."""
import re
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..models import Attachment, User, VideoMaterial, utc_now
from ..public_identity import public_display_name
from ..uploading import cleanup_paths, cleanup_records, save_uploads
from ..web import db_session, require_admin, require_user, validated_form
from .video import _chat_meeting

router = APIRouter(prefix='/api/video/meetings')
STORAGE_NAME = re.compile(r'[a-f0-9]{32}\Z')


def _material_view(material: VideoMaterial, author_name: str, size_bytes: int | None = None) -> dict:
    return {
        'id': material.id, 'kind': material.kind, 'title': material.title,
        'author_name': author_name, 'created_at': material.created_at.isoformat() + 'Z',
        'size_bytes': size_bytes if material.kind == 'file' else None,
        'url': material.url if material.kind == 'link' else None,
    }


def _safe_url(raw: object) -> str:
    if not isinstance(raw, str):
        raise HTTPException(400, 'Укажите ссылку HTTP или HTTPS')
    url = raw.strip()
    if (not url or len(url) > 2048 or '\\' in url
            or any(character.isspace() or ord(character) < 33 for character in url)):
        raise HTTPException(400, 'Укажите корректную ссылку HTTP или HTTPS')
    try:
        parsed = urlsplit(url)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.port is not None and not 1 <= parsed.port <= 65535):
            raise ValueError('unsafe URL')
    except ValueError:
        raise HTTPException(400, 'Укажите корректную ссылку HTTP или HTTPS') from None
    return url


def _safe_upload_path(request: Request, attachment: Attachment, *, require_exists: bool = True) -> Path:
    name = attachment.storage_name
    if not STORAGE_NAME.fullmatch(name or ''):
        raise HTTPException(404, 'Файл не найден')
    root = request.app.state.settings.upload_root.resolve()
    path = (root / name).resolve()
    if path.parent != root or path.name != name or (require_exists and not path.is_file()):
        raise HTTPException(404, 'Файл не найден')
    return path


def _teacher_active(db: Session, meeting_id: str, author_id: int, auth_version: int) -> User:
    author = db.get(User, author_id, populate_existing=True)
    if not author or not author.active or author.auth_version != auth_version:
        raise HTTPException(401, 'Пожалуйста, войдите')
    if author.must_change_password:
        raise HTTPException(423, 'Сначала смените временный пароль')
    if author.role != 'admin':
        raise HTTPException(403, 'Только преподаватель может добавлять материалы')
    meeting = _chat_meeting(db, meeting_id, author)
    student = db.get(User, meeting.student_id, populate_existing=True)
    if (meeting.state != 'active' or meeting.lease_slot != 1 or not student or not student.active
            or student.role != 'student' or student.learning_status == 'archived'):
        raise HTTPException(409, 'Материалы можно изменять только во время активной встречи')
    return author


def _write(sessionmaker, action):
    with sessionmaker() as db:
        # A token refresh can hold the SQLite writer lock while Cloud responds.
        db.execute(text('PRAGMA busy_timeout=13000'))
        try:
            db.execute(text('BEGIN IMMEDIATE'))
            return action(db)
        finally:
            db.rollback()
            db.execute(text('PRAGMA busy_timeout=5000'))
            db.commit()


@router.get('/{meeting_id}/materials')
def materials(request: Request, meeting_id: str, db: Session = Depends(db_session),
              user: User = Depends(require_user)):
    _chat_meeting(db, meeting_id, user)
    try:
        after_id = int(request.query_params.get('after_id', '0'))
        limit = int(request.query_params.get('limit', '100'))
    except (ValueError, TypeError):
        raise HTTPException(400, 'Некорректный курсор материалов') from None
    if after_id < 0 or not 1 <= limit <= 100:
        raise HTTPException(400, 'Некорректная страница материалов')
    rows = db.execute(
        select(VideoMaterial, User, Attachment.size_bytes)
        .join(User, User.id == VideoMaterial.author_id)
        .outerjoin(Attachment, Attachment.id == VideoMaterial.attachment_id)
        .where(VideoMaterial.meeting_id == meeting_id, VideoMaterial.id > after_id)
        .order_by(VideoMaterial.id.asc()).limit(limit + 1)
    ).all()
    return {'materials': [_material_view(material, public_display_name(author), size) for material, author, size in rows[:limit]],
            'has_more': len(rows) > limit}


@router.post('/{meeting_id}/materials/link')
async def add_link(request: Request, meeting_id: str, db: Session = Depends(db_session),
                   teacher: User = Depends(require_admin)):
    form = await validated_form(request)
    raw_title = form.get('title')
    title = raw_title.strip() if isinstance(raw_title, str) else ''
    if not title or len(title) > 200 or '\x00' in title:
        raise HTTPException(400, 'Название должно содержать от 1 до 200 символов')
    url = _safe_url(form.get('url'))
    teacher_id, auth_version = teacher.id, request.session.get('version')
    db.commit()

    def insert(session: Session):
        author = _teacher_active(session, meeting_id, teacher_id, auth_version)
        material = VideoMaterial(meeting_id=meeting_id, kind='link', title=title,
                                 author_id=author.id, created_at=utc_now(), url=url)
        session.add(material)
        session.commit()
        return _material_view(material, public_display_name(author))

    return await run_in_threadpool(_write, request.app.state.sessionmaker, insert)


@router.post('/{meeting_id}/materials/file')
async def add_file(request: Request, meeting_id: str, db: Session = Depends(db_session),
                   teacher: User = Depends(require_admin)):
    form = await validated_form(request)
    files = form.getlist('file')
    if len(files) != 1:
        raise HTTPException(400, 'Выберите один файл')
    _chat_meeting(db, meeting_id, teacher)
    db.commit()
    uploads = await save_uploads(files, request.app.state.settings)
    if len(uploads) != 1:
        raise HTTPException(400, 'Выберите один файл')
    upload = uploads[0]
    teacher_id, auth_version = teacher.id, request.session.get('version')

    def insert(session: Session):
        author = _teacher_active(session, meeting_id, teacher_id, auth_version)
        upload.meeting_id = meeting_id
        session.add(upload)
        session.flush()
        material = VideoMaterial(meeting_id=meeting_id, kind='file', title=upload.filename[:200],
                                 author_id=author.id, created_at=utc_now(), attachment_id=upload.id)
        session.add(material)
        session.commit()
        return _material_view(material, public_display_name(author), upload.size_bytes)

    try:
        return await run_in_threadpool(_write, request.app.state.sessionmaker, insert)
    except BaseException:
        cleanup_records(uploads, request.app.state.settings)
        raise


@router.get('/{meeting_id}/materials/{material_id}/download')
def download(request: Request, meeting_id: str, material_id: int,
             db: Session = Depends(db_session), user: User = Depends(require_user)):
    _chat_meeting(db, meeting_id, user)
    material = db.get(VideoMaterial, material_id)
    if not material or material.meeting_id != meeting_id or material.kind != 'file':
        raise HTTPException(404, 'Файл не найден')
    attachment = db.get(Attachment, material.attachment_id)
    if not attachment or attachment.meeting_id != meeting_id:
        raise HTTPException(404, 'Файл не найден')
    path = _safe_upload_path(request, attachment)
    return FileResponse(path, filename=attachment.filename, media_type='application/octet-stream', headers={
        'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, no-store',
    })


@router.delete('/{meeting_id}/materials/{material_id}')
async def remove(request: Request, meeting_id: str, material_id: int,
                 db: Session = Depends(db_session), teacher: User = Depends(require_admin)):
    await validated_form(request)
    teacher_id, auth_version = teacher.id, request.session.get('version')
    db.commit()

    def delete(session: Session):
        _teacher_active(session, meeting_id, teacher_id, auth_version)
        material = session.get(VideoMaterial, material_id)
        if not material or material.meeting_id != meeting_id:
            raise HTTPException(404, 'Материал не найден')
        attachment = session.get(Attachment, material.attachment_id) if material.attachment_id else None
        if material.kind == 'file' and (not attachment or attachment.meeting_id != meeting_id):
            raise HTTPException(404, 'Материал не найден')
        path = _safe_upload_path(request, attachment, require_exists=False) if attachment else None
        session.delete(material)
        session.flush()
        if attachment:
            session.delete(attachment)
        session.commit()
        return path

    path = await run_in_threadpool(_write, request.app.state.sessionmaker, delete)
    if path:
        cleanup_paths([path])
    return {'deleted': True}
