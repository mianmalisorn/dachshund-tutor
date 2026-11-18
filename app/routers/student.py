from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from ..logic import latest_submission, status
from ..models import Assignment, AssignmentTarget, Lesson, Submission, VideoMeeting
from ..uploading import cleanup_records, save_uploads
from ..web import db_session, render, require_student, validated_form
from ..models import User
from ..homework import claim_version
from ..web import parse_id

router = APIRouter(prefix='/student')


def active_video_meeting(db: Session, student_id: int) -> VideoMeeting | None:
    return db.scalar(select(VideoMeeting).where(
        VideoMeeting.lease_slot == 1, VideoMeeting.student_id == student_id,
        VideoMeeting.state == 'active'))


def student_targets(db, student_id):
    return list(db.scalars(select(AssignmentTarget).options(
        selectinload(AssignmentTarget.assignment).selectinload(Assignment.folder),
        selectinload(AssignmentTarget.assignment).selectinload(Assignment.attachments),
        selectinload(AssignmentTarget.submissions).selectinload(Submission.attachments),
    ).where(AssignmentTarget.student_id == student_id).order_by(AssignmentTarget.id.desc())))


@router.get('')
def dashboard(request: Request, user: User = Depends(require_student), db: Session = Depends(db_session)):
    video_meeting = active_video_meeting(db, user.id)
    video_history = list(db.scalars(select(VideoMeeting).where(
        VideoMeeting.student_id == user.id, VideoMeeting.state == 'ended'
    ).order_by(VideoMeeting.ended_at.desc(), VideoMeeting.id.desc()).limit(10)))
    targets = student_targets(db, user.id)
    filter_status = request.query_params.get('status', '')
    if filter_status in {'todo', 'overdue', 'pending', 'checked', 'revision'}:
        targets = [t for t in targets if status(t) == filter_status]
    targets.sort(key=lambda t: t.assignment.due_at)
    return render(request, 'student/dashboard.html', user=user, targets=targets,
                  selected_status=filter_status, video_meeting=video_meeting,
                  video_history=video_history)


def owned_target(db, student_id, assignment_id):
    target = db.scalar(select(AssignmentTarget).options(
        selectinload(AssignmentTarget.assignment).selectinload(Assignment.attachments),
        selectinload(AssignmentTarget.submissions).selectinload(Submission.attachments),
    ).where(AssignmentTarget.student_id == student_id, AssignmentTarget.assignment_id == assignment_id))
    if not target:
        raise HTTPException(404, 'Задание не найдено')
    return target


@router.get('/assignments/{assignment_id}')
def detail(request: Request, assignment_id: int, user: User = Depends(require_student), db: Session = Depends(db_session)):
    target = owned_target(db, user.id, assignment_id)
    success = request.session.pop('submission_success', None)
    message = None
    if success and success.get('assignment_id') == assignment_id:
        message = ('Новая версия отправлена. Предыдущая сохранилась.' if success.get('repeat') else 'Работа отправлена. Работа на месте. Телепатическая отправка не потребовалась.')
    return render(request, 'student/assignment_detail.html', user=user, target=target, assignment=target.assignment, submission_message=message)


@router.post('/assignments/{assignment_id}/submit')
async def submit(request: Request, assignment_id: int, user: User = Depends(require_student), db: Session = Depends(db_session)):
    form = await validated_form(request)
    target = owned_target(db, user.id, assignment_id)
    previous = latest_submission(target)
    if previous and previous.review_state == 'checked':
        raise HTTPException(400, 'Работа уже проверена. Уточните у преподавательницы, если нужно пересдать.')
    try:
        uploads = await save_uploads(form.getlist('files'), request.app.state.settings)
        if not uploads:
            raise HTTPException(400, 'Прикрепите хотя бы один файл')
    except HTTPException as exc:
        response=render(request,'student/assignment_detail.html',user=user,target=target,
                        assignment=target.assignment,form_error=str(exc.detail))
        response.status_code=exc.status_code
        return response
    try:
        claim_version(db, Assignment, assignment_id, target.assignment.version, advance=False)
        claim_version(db, AssignmentTarget, target.id, parse_id(form.get('target_version'), 'версии'))
        submission = Submission(target_id=target.id, due_at_snapshot=target.assignment.due_at)
        db.add(submission)
        db.flush()
        for item in uploads:
            item.submission_id = submission.id
            db.add(item)
        db.commit()
    except BaseException:
        db.rollback()
        cleanup_records(uploads, request.app.state.settings)
        raise
    request.session['submission_success'] = {'assignment_id': assignment_id, 'repeat': previous is not None}
    return RedirectResponse(f'/student/assignments/{assignment_id}?notice=submitted', status_code=303)


@router.get('/lessons')
def lessons(request: Request, user: User = Depends(require_student), db: Session = Depends(db_session)):
    items = list(db.scalars(select(Lesson).where(Lesson.student_id == user.id, Lesson.state == 'scheduled').order_by(Lesson.starts_at.desc()).limit(100)))
    return render(request, 'student/lessons.html', user=user, lessons=items,
                  video_meeting=active_video_meeting(db, user.id))
