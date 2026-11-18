from uuid import uuid4
from sqlalchemy.exc import IntegrityError
from datetime import timedelta, date, datetime, time, timezone
from pathlib import Path
import re
from urllib.parse import urlencode
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response, FileResponse
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload
from ..config import ROOT
from ..logic import from_form_datetime, latest_submission, meeting_url, status
from ..models import (Assignment, AssignmentFolder, AssignmentTarget, Attachment,
                      Lesson, StudentFolder, StudentStatusEvent, Submission, User, utc_now)
from ..students import STATUS_LABELS as STUDENT_STATUSES, change_status
from ..security import password_hash
from ..mascot import record_event
from ..homework import claim_version
from ..models import ReviewEvent, DeadlineEvent
from ..uploading import cleanup_records, save_uploads
from ..web import clean_text, db_session, parse_id, render, require_admin, validated_form

router = APIRouter(prefix='/admin')

@router.get('/cat/widget/{key}')
def widget_asset(key: str, user: User = Depends(require_admin)):
    from ..cat_assets import APPROVED_WIDGET_ASSETS, asset_path
    path = asset_path(key) if key in APPROVED_WIDGET_ASSETS else None
    if path is None:
        raise HTTPException(404)
    media_type = {'.png':'image/png', '.webp':'image/webp'}.get(path.suffix.lower())
    if not media_type:
        raise HTTPException(404)
    return FileResponse(path, media_type=media_type, headers={'Cache-Control':'private, no-store'})

@router.get('/cat/placeholder.svg')
def cat_placeholder(user: User = Depends(require_admin)):
    return Response((ROOT / 'app/private_assets/placeholder.svg').read_text(encoding='utf-8'),
                    media_type='image/svg+xml', headers={'Cache-Control': 'private, no-store'})


def go(path, notice='saved'):
    return RedirectResponse(f'{path}{"&" if "?" in path else "?"}notice={notice}', status_code=303)


def active_students(db):
    return list(db.scalars(select(User).where(User.role == 'student', User.active.is_(True), User.learning_status == 'active').order_by(User.display_name)))


def get_target(db, assignment_id: int, student_id: int):
    target = db.scalar(select(AssignmentTarget).options(
        selectinload(AssignmentTarget.assignment),
        selectinload(AssignmentTarget.submissions),
    ).where(AssignmentTarget.assignment_id == assignment_id, AssignmentTarget.student_id == student_id))
    if not target:
        raise HTTPException(404, 'Работа ученика не найдена')
    return target


@router.get('')
def dashboard(request: Request, db: Session = Depends(db_session), user: User = Depends(require_admin)):
    from ..scheduling import refresh_all
    from ..attendance import LABELS as ATTENDANCE_LABELS
    from ..attendance_models import LessonAttendance
    from ..quiz_models import QuizAttempt, QuizVersion
    from ..quizzes import finalize_scope
    from ..analytics import teacher_actions
    from zoneinfo import ZoneInfo
    from ..logic import to_local

    now = utc_now()
    tz_name = request.app.state.settings.timezone
    local_today = now.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(tz_name)).date()
    weekdays = ('понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье')
    months = ('января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
              'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря')
    today_caption = f'{weekdays[local_today.weekday()].capitalize()}, {local_today.day} {months[local_today.month - 1]} {local_today.year}'
    day_start = datetime.combine(local_today, time.min, ZoneInfo(tz_name)).astimezone(timezone.utc).replace(tzinfo=None)
    day_end = datetime.combine(local_today + timedelta(days=1), time.min, ZoneInfo(tz_name)).astimezone(timezone.utc).replace(tzinfo=None)
    week_end = day_end + timedelta(days=6)

    schedule_errors=refresh_all(db)
    # Finalization may commit and expire the session, so do it before loading
    # presentation rows to keep their eager-loaded relations warm.
    finalize_scope(db, user, now)
    db.commit()
    targets = list(db.scalars(select(AssignmentTarget).options(
        selectinload(AssignmentTarget.assignment), selectinload(AssignmentTarget.student),
        selectinload(AssignmentTarget.submissions)
    )))
    to_review = sorted((t for t in targets if status(t, now) == 'pending'),
                       key=lambda t: (t.assignment.due_at, t.student.display_name.casefold()))
    actionable = [(t, status(t, now)) for t in targets if status(t, now) in {'overdue', 'revision'}]
    actionable.sort(key=lambda pair: (0 if pair[1] == 'overdue' else 1, pair[0].assignment.due_at))
    deadlines = [t for t in targets if status(t, now) == 'todo' and day_start <= t.assignment.due_at < week_end]
    deadlines.sort(key=lambda t: t.assignment.due_at)

    # The dashboard calls the same expiry/finalization service used by the quiz pages.
    # It displays only submitted attempts; scoring rules remain in the quiz service.
    quiz_reviews = list(db.execute(
        select(QuizAttempt, User.display_name, QuizVersion.title)
        .join(User, User.id == QuizAttempt.student_id)
        .join(QuizVersion, QuizVersion.id == QuizAttempt.version_id)
        .where(QuizAttempt.state == 'submitted')
        .order_by(QuizAttempt.submitted_at, QuizAttempt.id)
        .limit(8)
    ).all())
    quiz_review_count = db.scalar(select(func.count()).select_from(QuizAttempt).where(QuizAttempt.state == 'submitted')) or 0

    today_rows = db.execute(
        select(Lesson, LessonAttendance.outcome)
        .options(selectinload(Lesson.student))
        .outerjoin(LessonAttendance, LessonAttendance.lesson_id == Lesson.id)
        .where(Lesson.starts_at >= day_start, Lesson.starts_at < day_end, Lesson.state != 'suppressed')
        .order_by(Lesson.starts_at, Lesson.id)
    ).all()
    next_today_id = next((lesson.id for lesson, _ in today_rows
                          if lesson.state == 'scheduled' and lesson.starts_at >= now), None)
    today_lessons = [{
        'lesson': lesson,
        'outcome': outcome,
        'outcome_label': ATTENDANCE_LABELS.get(outcome, 'Отметить результат'),
        'local_day': to_local(lesson.starts_at, tz_name, '%Y-%m-%d'),
        'is_next': lesson.id == next_today_id,
        'finished': lesson.starts_at + timedelta(minutes=lesson.duration_minutes) <= now,
    } for lesson, outcome in today_rows]
    next_lesson = db.scalar(
        select(Lesson).options(selectinload(Lesson.student)).where(
            Lesson.state == 'scheduled', Lesson.starts_at >= now, Lesson.starts_at >= day_end
        ).order_by(Lesson.starts_at, Lesson.id).limit(1)
    )
    attention = teacher_actions(db, now, include_homework=False, include_quizzes=False)

    return render(request, 'admin/dashboard.html', user=user,
                  today=local_today, today_caption=today_caption, now=now,
                  today_lessons=today_lessons, next_lesson=next_lesson,
                  to_review=to_review[:5], review_count=len(to_review),
                  actionable=actionable[:4], actionable_count=len(actionable),
                  quiz_reviews=quiz_reviews, quiz_review_count=quiz_review_count,
                  missing_attendance=attention['missing_attendance'][:5],
                  missing_attendance_count=len(attention['missing_attendance']),
                  repeated_absences=attention['repeated_absences'][:5],
                  repeated_absence_count=len(attention['repeated_absences']),
                  deadlines=deadlines[:5], schedule_errors=schedule_errors,
                  student_count=db.scalar(select(func.count()).select_from(User).where(
                      User.role == 'student', User.active.is_(True), User.learning_status == 'active')) or 0)


@router.get('/students')
def student_list(request: Request, db: Session = Depends(db_session), user: User = Depends(require_admin)):
    folder_id = parse_id(request.query_params.get('folder'), 'папки')
    query = select(User).options(selectinload(User.folder)).where(User.role == 'student').order_by(User.display_name)
    selected_status = request.query_params.get('status', 'active')
    if selected_status not in {*STUDENT_STATUSES, 'all'}:
        raise HTTPException(400, 'Неизвестный статус')
    if selected_status != 'all':
        query = query.where(User.learning_status == selected_status)
    search = request.query_params.get('q', '').strip()[:120]
    students = list(db.scalars(query))
    if search:
        students = [s for s in students if search.casefold() in s.display_name.casefold() or search.casefold() in s.username.casefold()]
    if folder_id:
        students = [s for s in students if s.folder_id == folder_id]
    has_any_students = bool(db.scalar(select(func.count()).select_from(User).where(User.role == 'student')))
    return render(request, 'admin/students.html', user=user, students=students,
                  selected_status=selected_status, search=search, student_statuses=STUDENT_STATUSES,
                  folders=list(db.scalars(select(StudentFolder).order_by(StudentFolder.name))), folder_id=folder_id,
                  has_any_students=has_any_students)


@router.get('/students/new')
def student_new(request: Request, db: Session = Depends(db_session), user: User = Depends(require_admin)):
    return render(request, 'admin/student_form.html', user=user, student=None,
                  folders=list(db.scalars(select(StudentFolder).order_by(StudentFolder.name))))


@router.post('/students')
async def student_create(request: Request, db: Session = Depends(db_session), user: User = Depends(require_admin)):
    form = await validated_form(request)
    name = clean_text(form.get('display_name'), 120, 'Имя ученика')
    username = clean_text(form.get('username'), 60, 'Логин').lower()
    if not re.fullmatch(r'[a-z0-9_.-]{3,60}', username):
        raise HTTPException(400, 'Логин: 3–60 символов, латиница, цифры, точка, дефис или подчёркивание')
    if db.scalar(select(User).where(User.username == username)):
        raise HTTPException(400, 'Логин занят')
    folder_id = parse_id(form.get('folder_id'), 'папки')
    if folder_id and not db.get(StudentFolder, folder_id):
        raise HTTPException(400, 'Нет такой папки')
    try:
        pwd = password_hash(str(form.get('password', '')))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    new = User(username=username, display_name=name, password_hash=pwd,
               role='student', folder_id=folder_id, must_change_password=True)
    db.add(new)
    db.flush()
    db.add(StudentStatusEvent(student_id=new.id, status='active', access_enabled=True, changed_by=user.id))
    db.commit()
    record_event(request,user,'student.created')
    return go('/admin/students', 'student_created')


@router.get('/students/{student_id}/edit')
def student_edit(request: Request, student_id: int, db: Session = Depends(db_session), user: User = Depends(require_admin)):
    student = db.get(User, student_id)
    if not student or student.role != 'student':
        raise HTTPException(404)
    return render(request, 'admin/student_form.html', user=user, student=student,
                  student_statuses=STUDENT_STATUSES,
                  events=list(db.scalars(select(StudentStatusEvent).where(StudentStatusEvent.student_id == student.id).order_by(StudentStatusEvent.id.desc()))),
                  targets=list(db.scalars(select(AssignmentTarget).options(selectinload(AssignmentTarget.assignment), selectinload(AssignmentTarget.submissions)).where(AssignmentTarget.student_id == student.id))),
                  folders=list(db.scalars(select(StudentFolder).order_by(StudentFolder.name))))


@router.post('/students/{student_id}/edit')
async def student_update(request: Request, student_id: int, db: Session = Depends(db_session), user: User = Depends(require_admin)):
    form = await validated_form(request)
    student = db.get(User, student_id)
    if not student or student.role != 'student':
        raise HTTPException(404)
    name = clean_text(form.get('display_name'), 120, 'Имя ученика')
    folder_id = parse_id(form.get('folder_id'), 'папки')
    if folder_id and not db.get(StudentFolder, folder_id):
        raise HTTPException(400, 'Нет такой папки')
    notes = clean_text(form.get('internal_notes'), 4000, 'Личные заметки', required=False)
    changed = db.execute(update(User).where(User.id == student_id,
        User.profile_version == parse_id(form.get('profile_version'), 'версии профиля')).values(
        display_name=name, folder_id=folder_id, internal_notes=notes, profile_version=User.profile_version + 1))
    if changed.rowcount != 1:
        raise HTTPException(409, 'Профиль изменён в другой вкладке. Обновите карточку перед сохранением.')
    db.commit()
    return go('/admin/students', 'updated')


@router.post('/students/{student_id}/reset-password')
async def reset_student_password(request: Request, student_id: int, db: Session = Depends(db_session), user: User = Depends(require_admin)):
    form = await validated_form(request)
    student = db.get(User, student_id)
    if not student or student.role != 'student':
        raise HTTPException(404)
    try:
        student.password_hash = password_hash(str(form.get('password', '')))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    student.must_change_password = True
    student.auth_version += 1
    db.commit()
    return go(f'/admin/students/{student_id}/edit', 'password_reset')


@router.post('/students/{student_id}/status')
async def student_status(request: Request, student_id: int, db: Session = Depends(db_session), user: User = Depends(require_admin)):
    form = await validated_form(request)
    student = db.get(User, student_id)
    if not student or student.role != 'student':
        raise HTTPException(404)
    state = str(form.get('status', ''))
    access = state == 'active' or form.get('allow_access') == 'yes'
    previous_status = student.learning_status
    change_status(db, student, state, access, user.id, parse_id(form.get('version'), 'версии'))
    db.commit()
    if state != previous_status:
        trigger = {'paused':'student.paused','archived':'student.archived','active':'student.restored'}[state]
        record_event(request,user,trigger)
    return go(f'/admin/students/{student_id}/edit', 'updated')


@router.get('/folders')
def folders(request: Request, db: Session = Depends(db_session), user: User = Depends(require_admin)):
    return render(request, 'admin/folders.html', user=user,
                  student_folders=list(db.scalars(select(StudentFolder).order_by(StudentFolder.name))),
                  assignment_folders=list(db.scalars(select(AssignmentFolder).order_by(AssignmentFolder.name))))


@router.post('/folders')
async def folder_create(request: Request, db: Session = Depends(db_session), user: User = Depends(require_admin)):
    form = await validated_form(request)
    name = clean_text(form.get('name'), 80, 'Название папки')
    kind = str(form.get('kind', ''))
    model = {'students': StudentFolder, 'assignments': AssignmentFolder}.get(kind)
    if not model:
        raise HTTPException(400, 'Неверный тип папки')
    if db.scalar(select(model).where(model.name == name)):
        raise HTTPException(400, 'Такая папка уже есть')
    db.add(model(name=name))
    db.commit()
    return go('/admin/folders')


@router.post('/folders/{kind}/{folder_id}/delete')
async def folder_delete(request: Request, kind: str, folder_id: int, db: Session = Depends(db_session), user: User = Depends(require_admin)):
    await validated_form(request)
    model = {'students': StudentFolder, 'assignments': AssignmentFolder}.get(kind)
    if not model:
        raise HTTPException(404)
    folder = db.get(model, folder_id)
    if not folder:
        raise HTTPException(404)
    count_model, fk = (User, User.folder_id) if kind == 'students' else (Assignment, Assignment.folder_id)
    if db.scalar(select(func.count()).select_from(count_model).where(fk == folder_id)):
        raise HTTPException(400, 'Сначала переместите элементы из папки')
    db.delete(folder)
    db.commit()
    return go('/admin/folders', 'deleted')


def assignment_query():
    return select(Assignment).options(
        selectinload(Assignment.folder),
        selectinload(Assignment.targets).selectinload(AssignmentTarget.student),
        selectinload(Assignment.targets).selectinload(AssignmentTarget.submissions),
        selectinload(Assignment.attachments),
    )


@router.get('/assignments')
def assignments(request: Request, db: Session = Depends(db_session), user: User = Depends(require_admin)):
    folder_id = parse_id(request.query_params.get('folder'), 'папки')
    student_id = parse_id(request.query_params.get('student'), 'ученика')
    selected_status = request.query_params.get('status','')
    if selected_status not in {'','todo','overdue','pending','checked','revision'}:
        raise HTTPException(400,'Неизвестный статус')
    search=request.query_params.get('q','').strip()[:180]
    date_from=request.query_params.get('from','')
    date_to=request.query_params.get('to','')
    try:
        start=date.fromisoformat(date_from) if date_from else None
        end=date.fromisoformat(date_to) if date_to else None
        if start and end and start>end:
            raise ValueError()
    except ValueError:
        raise HTTPException(400,'Проверьте диапазон дат')
    request.session['assignment_filters']=urlencode({key:value for key,value in {
        'folder':folder_id,'student':student_id,'status':selected_status,'q':search,
        'from':date_from,'to':date_to}.items() if value})
    query = assignment_query().order_by(Assignment.due_at)
    if folder_id:
        query = query.where(Assignment.folder_id == folder_id)
    items=list(db.scalars(query))
    from ..logic import to_local
    def matches(a):
        targets=[t for t in a.targets if not student_id or t.student_id==student_id]
        if (student_id or selected_status) and not any(not selected_status or status(t)==selected_status for t in targets):
            return False
        local_date=date.fromisoformat(to_local(a.due_at,request.app.state.settings.timezone,'%Y-%m-%d'))
        return (not start or local_date>=start) and (not end or local_date<=end) and (not search or
            search.casefold() in (a.title+' '+a.description+' '+' '.join(f.filename for f in a.attachments)).casefold())
    matched = [a for a in items if matches(a)]
    groups = {key: [] for key in ('pending', 'overdue', 'revision', 'todo', 'checked')}
    now = utc_now()
    for assignment in matched:
        for target in assignment.targets:
            code = status(target, now)
            if (not student_id or target.student_id == student_id) and (not selected_status or code == selected_status):
                groups[code].append(target)
    return render(request, 'admin/assignments.html', user=user, assignments=matched, target_groups=groups,
                  folders=list(db.scalars(select(AssignmentFolder).order_by(AssignmentFolder.name))), folder_id=folder_id,
                  students=list(db.scalars(select(User).where(User.role=='student').order_by(User.display_name))),
                  student_id=student_id, selected_status=selected_status, search=search, date_from=date_from,date_to=date_to)


@router.get('/assignments/new')
def assignment_new(request: Request, db: Session = Depends(db_session), user: User = Depends(require_admin)):
    return render(request, 'admin/assignment_form.html', user=user, assignment=None, creation_key=uuid4().hex,
                  students=active_students(db), folders=list(db.scalars(select(AssignmentFolder).order_by(AssignmentFolder.name))))


@router.post('/assignments')
async def assignment_create(request: Request, db: Session = Depends(db_session), user: User = Depends(require_admin)):
    form = await validated_form(request)
    creation_key = str(form.get('creation_key', ''))
    if not re.fullmatch('[a-f0-9]{32}', creation_key):
        raise HTTPException(409, 'Обновите форму создания задания: отсутствует идентификатор отправки.')
    existing = db.scalar(select(Assignment).where(Assignment.creation_key == creation_key, Assignment.created_by == user.id))
    if existing:
        return go(f'/admin/assignments/{existing.id}', 'assignment_created')
    try:
        title = clean_text(form.get('title'), 180, 'Название')
        description = clean_text(form.get('description'), 10000, 'Описание', required=False)
        try:
            due_at = from_form_datetime(str(form.get('due_at', '')), request.app.state.settings.timezone)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        student_ids = set()
        for raw in form.getlist('student_ids'):
            student_ids.add(parse_id(raw, 'ученика'))
        if not student_ids:
            raise HTTPException(400, 'Выберите хотя бы одного ученика')
        valid_ids = set(db.scalars(select(User.id).where(User.role == 'student', User.active.is_(True), User.learning_status == 'active', User.id.in_(student_ids))))
        if valid_ids != student_ids:
            raise HTTPException(400, 'Выбран несуществующий или отключённый ученик')
        folder_id = parse_id(form.get('folder_id'), 'папки заданий')
        if folder_id and not db.get(AssignmentFolder, folder_id):
            raise HTTPException(400, 'Папка не найдена')
        uploads = await save_uploads(form.getlist('files'), request.app.state.settings)
        try:
            assignment = Assignment(creation_key=creation_key, title=title, description=description, due_at=due_at, folder_id=folder_id, created_by=user.id)
            db.add(assignment)
            db.flush()
            for sid in sorted(student_ids):
                db.add(AssignmentTarget(assignment_id=assignment.id, student_id=sid))
            for item in uploads:
                item.assignment_id = assignment.id
                db.add(item)
            db.commit()
            aid = assignment.id
        except IntegrityError:
            db.rollback()
            cleanup_records(uploads, request.app.state.settings)
            existing = db.scalar(select(Assignment).where(Assignment.creation_key == creation_key, Assignment.created_by == user.id))
            if existing:
                return go(f'/admin/assignments/{existing.id}', 'assignment_created')
            raise HTTPException(409, 'Данные изменились. Обновите форму задания.')
        except BaseException:
            db.rollback()
            cleanup_records(uploads, request.app.state.settings)
            raise
        record_event(request,user,'assignment.created',has_files=bool(uploads))
        return go(f'/admin/assignments/{aid}', 'assignment_created')
    except HTTPException as exc:
        if exc.status_code not in (400,413):
            raise
        db.rollback()
        draft={key:str(form.get(key,''))[:limit] for key,limit in
               [('title',180),('description',10000),('due_at',40),('folder_id',20)]}
        draft['student_ids']=[str(value) for value in form.getlist('student_ids')][:100]
        response=render(request,'admin/assignment_form.html',user=user,assignment=None,draft=draft,
                        form_error=str(exc.detail), creation_key=creation_key, students=active_students(db),
                        folders=list(db.scalars(select(AssignmentFolder).order_by(AssignmentFolder.name))))
        response.status_code=exc.status_code
        return response


@router.get('/assignments/{assignment_id}')
def assignment_detail(request: Request, assignment_id: int, db: Session = Depends(db_session), user: User = Depends(require_admin)):
    assignment = db.scalar(assignment_query().where(Assignment.id == assignment_id))
    if not assignment:
        raise HTTPException(404)
    return assignment_page(request,db,user,assignment)


def assignment_page(request,db,user,assignment,review_target=None,queue_count=0):
    return render(request, 'admin/review_queue.html' if review_target else 'admin/assignment_detail.html', user=user, assignment=assignment,
                  display_targets=[review_target] if review_target else assignment.targets,
                  in_review_queue=review_target is not None, queue_count=queue_count,
                  assignment_list_url='/admin/assignments'+('?' + request.session['assignment_filters'] if request.session.get('assignment_filters') else ''),
                  folders=list(db.scalars(select(AssignmentFolder).order_by(AssignmentFolder.name))),
                  deadline_events=list(db.scalars(select(DeadlineEvent).where(DeadlineEvent.assignment_id==assignment.id).order_by(DeadlineEvent.id.desc()))))


@router.get('/reviews')
def review_queue(request: Request, db: Session = Depends(db_session), user: User = Depends(require_admin)):
    assignments=list(db.scalars(assignment_query()))
    pending=[t for a in assignments for t in a.targets if status(t)=='pending']
    pending.sort(key=lambda t:(latest_submission(t).created_at,t.id))
    requested=parse_id(request.query_params.get('target'),'работы')
    target=next((t for t in pending if t.id==requested),pending[0] if pending else None)
    if target is None:
        return render(request,'admin/review_empty.html',user=user)
    return assignment_page(request,db,user,target.assignment,target,len(pending))


@router.post('/assignments/{assignment_id}/edit')
async def assignment_edit(request: Request, assignment_id: int, db: Session = Depends(db_session), user: User = Depends(require_admin)):
    form = await validated_form(request)
    assignment = db.get(Assignment, assignment_id)
    if not assignment:
        raise HTTPException(404)
    previous_due_at = assignment.due_at
    title = clean_text(form.get('title'), 180, 'Название')
    description = clean_text(form.get('description'), 10000, 'Описание', required=False)
    try:
        due_at = from_form_datetime(str(form.get('due_at', '')), request.app.state.settings.timezone)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    folder_id = parse_id(form.get('folder_id'), 'папки')
    if folder_id and not db.get(AssignmentFolder, folder_id):
        raise HTTPException(400, 'Папка не найдена')
    uploads = await save_uploads(form.getlist('files'), request.app.state.settings)
    try:
        claim_version(db, Assignment, assignment_id, parse_id(form.get('version'), 'версии'))
        assignment.title, assignment.description, assignment.due_at = title, description, due_at
        assignment.folder_id = folder_id
        if due_at != previous_due_at:
            db.add(DeadlineEvent(assignment_id=assignment_id, previous_due_at=previous_due_at,
                                 due_at=due_at, actor_id=user.id))
        for item in uploads:
            item.assignment_id = assignment_id
            db.add(item)
        db.commit()
    except BaseException:
        db.rollback()
        cleanup_records(uploads, request.app.state.settings)
        raise
    record_event(request,user,'assignment.deadline_changed' if assignment.due_at != previous_due_at else 'assignment.updated')
    return go(f'/admin/assignments/{assignment_id}', 'updated')


@router.post('/assignments/{assignment_id}/review/{student_id}')
async def review(request: Request, assignment_id: int, student_id: int, db: Session = Depends(db_session), user: User = Depends(require_admin)):
    form = await validated_form(request)
    request.state.review_recovery={
        'comment':str(form.get('review_comment',''))[:2000],
        'url':f'/admin/assignments/{assignment_id}#student-{student_id}'}
    state = str(form.get('review_state', ''))
    if state not in {'checked', 'revision'}:
        raise HTTPException(400, 'Неизвестное решение')
    target = get_target(db, assignment_id, student_id)
    claim_version(db, AssignmentTarget, target.id, parse_id(form.get('target_version'), 'версии'))
    latest = latest_submission(target)
    if not latest:
        raise HTTPException(400, 'Ученик ещё не сдал работу')
    if parse_id(form.get('submission_id'), 'отправки') != latest.id:
        raise HTTPException(409, 'Ученик отправил новую версию работы. Обновите страницу перед проверкой.')
    latest.review_state = state
    latest.review_comment = clean_text(form.get('review_comment'), 2000, 'Комментарий', required=False)
    latest.reviewed_at = utc_now()
    latest.reviewed_by = user.id
    db.add(ReviewEvent(submission_id=latest.id, state=state, comment=latest.review_comment,
                       created_at=latest.reviewed_at, actor_id=user.id))
    db.commit()
    record_event(request,user,'submission.reviewed' if state=='checked' else 'submission.revision_requested',has_comment=bool(latest.review_comment))
    destination='/admin/reviews' if form.get('return_to_queue')=='yes' else f'/admin/assignments/{assignment_id}'
    return go(destination, 'reviewed' if state=='checked' else 'revision_requested')
