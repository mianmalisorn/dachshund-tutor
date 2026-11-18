"""File downloads enforce authorization per assignment and per submission."""
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from ..models import AssignmentTarget, Attachment, Submission, User
from ..web import db_session, require_user

router = APIRouter()


@router.get('/files/{attachment_id}')
def download(request: Request, attachment_id: int, db: Session = Depends(db_session), user: User = Depends(require_user)):
    if user.must_change_password:
        raise HTTPException(423, 'Сначала смените временный пароль')
    if user.role not in ('admin','student'):
        raise HTTPException(403, 'Учебные файлы недоступны')
    attachment = db.get(Attachment, attachment_id)
    if not attachment or attachment.meeting_id is not None:
        raise HTTPException(404, 'Файл не найден')
    if user.role != 'admin':
        if attachment.assignment_id is not None:
            target = db.query(AssignmentTarget).filter_by(
                student_id=user.id, assignment_id=attachment.assignment_id).first()
        else:
            submission = db.get(Submission, attachment.submission_id)
            target = db.get(AssignmentTarget, submission.target_id) if submission else None
            if target and target.student_id != user.id:
                target = None
        if not target:
            raise HTTPException(404, 'Файл не найден')
    root = request.app.state.settings.upload_root.resolve()
    path = (root / attachment.storage_name).resolve()
    if path.parent != root:
        raise HTTPException(404, 'Файл не найден')
    if not path.is_file():
        raise HTTPException(404, 'Файл отсутствует на сервере')
    return FileResponse(path, filename=attachment.filename, media_type='application/octet-stream', headers={
        'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, no-store'
    })
