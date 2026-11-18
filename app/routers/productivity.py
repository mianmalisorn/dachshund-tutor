"""Role-checked productivity tools: local search, private drafts and notifications."""
import json
import re
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import or_, select, delete, update, cast, String, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (Assignment, AssignmentTarget, Lesson, Submission, Topic, User,
                      UserDraft, UserNotificationState, utc_now)
from ..quiz_models import Quiz, QuizAttempt, QuizVersion
from ..web import db_session, render, require_admin, validated_form

router = APIRouter(prefix='/admin')
DRAFT_KINDS = {'homework', 'quiz', 'teaching'}
DRAFT_TTL = timedelta(days=30)
SECRET_NAME = re.compile(r'(password|token|secret|csrf|auth|api[_-]?key|credential)', re.I)


def normalized(value: str) -> str:
    return ' '.join(value.casefold().split())


def literal_like_pattern(value: str) -> str:
    escaped = value.casefold().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return f'%{escaped}%'


@router.get('/productivity/search')
def search(request: Request, q: str = '', db: Session = Depends(db_session), user: User = Depends(require_admin)):
    query = q.strip()[:100]
    groups = {'homework': [], 'tests': [], 'topics': [], 'students': [], 'lessons': []}
    if query:
        pattern = literal_like_pattern(query)
        def contains(column):
            return func.unicode_casefold(column).like(pattern, escape='\\')

        groups['homework'] = [
            {'title': row.title, 'url': f'/admin/assignments/{row.id}', 'detail': 'Домашнее задание'}
            for row in db.scalars(select(Assignment).where(contains(Assignment.title)).order_by(Assignment.created_at.desc()).limit(8))]
        groups['tests'] = [
            {'title': row.title, 'url': f'/admin/quizzes/versions/{row.id}', 'detail': 'Тест · ' + row.state}
            for row in db.execute(select(QuizVersion.id, QuizVersion.title, QuizVersion.state).join(Quiz, Quiz.id == QuizVersion.quiz_id)
                                  .where(Quiz.archived.is_(False), contains(QuizVersion.title)).order_by(QuizVersion.created_at.desc()).limit(8))]
        groups['topics'] = [
            {'title': row.title, 'url': '/admin/topics', 'detail': row.category or 'Тема'}
            for row in db.scalars(select(Topic).where(or_(contains(Topic.title), contains(Topic.category))).order_by(Topic.title).limit(8))]
        groups['students'] = [
            {'title': row.display_name, 'url': f'/admin/students/{row.id}', 'detail': 'Ученик'}
            for row in db.scalars(select(User).where(User.role == 'student', User.active.is_(True),
                                 or_(contains(User.display_name), contains(User.username)))
                                 .order_by(User.display_name).limit(8))]
        groups['lessons'] = [
            {'title': row.student.display_name, 'url': f'/admin/lessons/{row.id}/edit',
             'detail': 'Занятие · ' + row.starts_at.strftime('%d.%m.%Y %H:%M')}
            for row in db.scalars(select(Lesson).join(User, User.id == Lesson.student_id)
                                  .where(or_(contains(User.display_name), contains(User.username),
                                             contains(cast(Lesson.starts_at, String))))
                                  .order_by(Lesson.starts_at.desc(), Lesson.id.desc()).limit(8))]
    return JSONResponse({'query': query, 'groups': groups})


@router.get('/productivity/next-lesson')
def next_lesson(db: Session = Depends(db_session), user: User = Depends(require_admin)):
    lesson = db.scalar(select(Lesson).where(Lesson.state == 'scheduled', Lesson.starts_at >= utc_now())
                        .order_by(Lesson.starts_at, Lesson.id).limit(1))
    return {'url': f'/admin/lessons/{lesson.id}/edit' if lesson else '/admin/lessons',
            'title': lesson.student.display_name if lesson else 'Занятий пока нет'}


@router.get('/productivity/drafts/{kind}/{draft_key}')
def get_draft(kind: str, draft_key: str, db: Session = Depends(db_session), user: User = Depends(require_admin)):
    if kind not in DRAFT_KINDS or len(draft_key) > 80:
        raise HTTPException(400, 'Не удалось открыть черновик.')
    now = utc_now()
    db.execute(delete(UserDraft).where(UserDraft.expires_at <= now))
    row = db.scalar(select(UserDraft).where(UserDraft.user_id == user.id, UserDraft.kind == kind,
                                            UserDraft.draft_key == draft_key, UserDraft.expires_at > now))
    db.commit()
    if not row:
        return {'draft': None}
    return {'draft': json.loads(row.payload_json), 'revision': row.revision,
            'updated_at': row.updated_at.isoformat(), 'expires_at': row.expires_at.isoformat()}


@router.post('/productivity/drafts/{kind}/{draft_key}')
async def save_draft(kind: str, draft_key: str, request: Request, db: Session = Depends(db_session), user: User = Depends(require_admin)):
    form = await validated_form(request)
    if kind not in DRAFT_KINDS or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', draft_key):
        raise HTTPException(400, 'Не удалось сохранить черновик.')
    try:
        payload = json.loads(str(form.get('payload', '{}')))
        expected = int(str(form.get('revision', '0')))
    except (ValueError, TypeError, json.JSONDecodeError):
        raise HTTPException(400, 'Черновик содержит некорректные данные. Обновите форму.')
    if not isinstance(payload, dict) or len(json.dumps(payload, ensure_ascii=False)) > 48_000:
        raise HTTPException(400, 'Черновик слишком большой. Удалите часть текста и повторите попытку.')
    safe = {}
    for name, value in payload.items():
        if not isinstance(name, str) or SECRET_NAME.search(name) or not re.fullmatch(r'[\w.-]{1,80}', name):
            continue
        if isinstance(value, (str, bool, int)) or value is None:
            safe[name] = value[:12_000] if isinstance(value, str) else value
        elif isinstance(value, list) and len(value) <= 100 and all(isinstance(item, (str, int)) for item in value):
            safe[name] = [str(item)[:500] for item in value]
    now = utc_now()
    db.execute(delete(UserDraft).where(UserDraft.expires_at <= now))
    serialized = json.dumps(safe, ensure_ascii=False)
    if expected:
        changed = db.execute(update(UserDraft).where(UserDraft.user_id == user.id, UserDraft.kind == kind,
            UserDraft.draft_key == draft_key, UserDraft.revision == expected, UserDraft.expires_at > now)
            .values(payload_json=serialized, revision=UserDraft.revision + 1, updated_at=now, expires_at=now + DRAFT_TTL))
        if changed.rowcount != 1:
            db.rollback()
            raise HTTPException(409, 'Черновик обновлён в другой вкладке или удалён. Скопируйте изменения перед обновлением страницы.')
        next_revision = expected + 1
    elif not db.scalar(select(UserDraft.id).where(UserDraft.user_id == user.id, UserDraft.kind == kind, UserDraft.draft_key == draft_key)):
        row = UserDraft(user_id=user.id, kind=kind, draft_key=draft_key,
                        payload_json=serialized, revision=1,
                        updated_at=now, expires_at=now + DRAFT_TTL)
        db.add(row)
        next_revision = 1
    else:
        db.rollback()
        raise HTTPException(409, 'Черновик уже создан в другой вкладке. Обновите страницу перед сохранением.')
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'Черновик создан в другой вкладке. Обновите страницу перед сохранением.')
    return {'revision': next_revision, 'updated_at': now.isoformat(), 'expires_at': (now + DRAFT_TTL).isoformat()}


@router.delete('/productivity/drafts/{kind}/{draft_key}')
async def delete_draft(kind: str, draft_key: str, request: Request, db: Session = Depends(db_session), user: User = Depends(require_admin)):
    form = await validated_form(request)
    if kind not in DRAFT_KINDS:
        raise HTTPException(400, 'Неизвестный тип черновика.')
    try: revision = int(str(form.get('revision','')))
    except ValueError: raise HTTPException(400, 'Обновите страницу перед удалением черновика.')
    changed = db.execute(delete(UserDraft).where(UserDraft.user_id == user.id, UserDraft.kind == kind,
        UserDraft.draft_key == draft_key, UserDraft.revision == revision))
    if changed.rowcount != 1:
        db.rollback()
        raise HTTPException(409, 'Черновик изменён в другой вкладке. Обновите страницу и проверьте последнюю версию.')
    db.commit()
    return {'deleted': True}


def active_notifications(db, user_id: int):
    now = utc_now()
    items = []
    submissions = db.execute(select(Submission.id, Assignment.title, User.display_name, Submission.created_at)
        .join(AssignmentTarget, AssignmentTarget.id == Submission.target_id)
        .join(Assignment, Assignment.id == AssignmentTarget.assignment_id)
        .join(User, User.id == AssignmentTarget.student_id)
        .where(Submission.review_state == 'pending').order_by(Submission.created_at.desc()).limit(30)).all()
    for row in submissions:
        key = f'homework:{row.id}'
        items.append({'key': key, 'kind': 'event', 'title': f'Новая сдача: {row.title}',
                      'detail': row.display_name, 'at': row.created_at, 'url': '/admin/reviews'})
    pending = db.execute(select(QuizAttempt.id, QuizVersion.title, User.display_name, QuizAttempt.submitted_at)
        .join(QuizVersion, QuizVersion.id == QuizAttempt.version_id).join(User, User.id == QuizAttempt.student_id)
        .where(QuizAttempt.state == 'submitted').order_by(QuizAttempt.submitted_at.desc()).limit(30)).all()
    for row in pending:
        key = f'quiz:{row.id}'
        items.append({'key': key, 'kind': 'task', 'title': f'Тест ждёт оценки: {row.title}',
                      'detail': row.display_name, 'at': row.submitted_at, 'url': f'/admin/quizzes/attempts/{row.id}'})
    upcoming = db.execute(select(Lesson.id, Lesson.starts_at, User.display_name).join(User, User.id == Lesson.student_id)
        .where(Lesson.state == 'scheduled', Lesson.starts_at >= now, Lesson.starts_at <= now + timedelta(days=1))
        .order_by(Lesson.starts_at).limit(20)).all()
    for row in upcoming:
        key = f'lesson:{row.id}:{row.starts_at.isoformat()}'
        items.append({'key': key, 'kind': 'event', 'title': 'Предстоящее занятие', 'detail': row.display_name,
                      'at': row.starts_at, 'url': f'/admin/lessons/{row.id}/edit'})
    active_keys = [item['key'] for item in items]
    if active_keys:
        db.execute(delete(UserNotificationState).where(UserNotificationState.user_id == user_id,
            ~UserNotificationState.notification_key.in_(active_keys)))
    else:
        db.execute(delete(UserNotificationState).where(UserNotificationState.user_id == user_id))
    states = {row.notification_key for row in db.scalars(select(UserNotificationState).where(UserNotificationState.user_id == user_id))}
    db.commit()
    items.sort(key=lambda item: item['at'] or now, reverse=True)
    items = items[:100]
    for item in items:
        item['read'] = item['key'] in states
        item['at'] = item['at'].isoformat() if item['at'] else None
    return items


@router.get('/history')
def history(request: Request, db: Session = Depends(db_session), user: User = Depends(require_admin)):
    """One index over existing append-only records; never synthesizes past events."""
    from ..attendance_models import AttendanceEvent, LessonAttendance, OUTCOMES
    from ..models import (DeadlineEvent, ReviewEvent, ScheduleEvent, StudentStatusEvent,
                          TopicEvent, LessonRecord, StudentNote, TeachingRevision)
    from ..quiz_models import QuizAssignmentEvent, QuizGradeEvent, QuizAnswer
    from sqlalchemy import func

    events = []
    actors = {row.id: row.display_name for row in db.scalars(select(User))}
    students = {row.id: row.display_name for row in db.scalars(select(User).where(User.role == 'student'))}
    # Safe schedule fields only; meeting URLs and private lesson notes are intentionally omitted.
    for event, lesson, actor in db.execute(select(ScheduleEvent, Lesson, User)
            .outerjoin(Lesson, Lesson.id == ScheduleEvent.lesson_id).outerjoin(User, User.id == ScheduleEvent.actor_id)
            .order_by(ScheduleEvent.created_at.desc(), ScheduleEvent.id.desc()).limit(50)):
        def schedule_fields(raw):
            try: data = json.loads(raw or '{}')
            except (ValueError, TypeError): return {}
            return {key: data[key] for key in ('starts_at','duration_minutes','state') if key in data}
        events.append({'at':event.created_at, 'actor':actor.display_name if actor else '—',
            'student':students.get(lesson.student_id, 'Занятие') if lesson else 'Серия занятий',
            'what':'Изменение расписания · '+event.kind,
            'before':schedule_fields(event.before_json), 'after':schedule_fields(event.after_json),
            'url':f'/admin/lessons/{lesson.id}/history' if lesson else f'/admin/lesson-series/{event.series_id}/history'})
    for event, lesson, actor in db.execute(select(AttendanceEvent, LessonAttendance, User)
            .join(LessonAttendance, LessonAttendance.lesson_id == AttendanceEvent.lesson_id)
            .join(User, User.id == AttendanceEvent.actor_id).order_by(AttendanceEvent.created_at.desc(), AttendanceEvent.id.desc()).limit(50)):
        events.append({'at':event.created_at, 'actor':actor.display_name, 'student':students.get(lesson.student_id,'Ученик'),
            'what':'Результат посещаемости', 'before':{'исход':event.before_outcome,'минуты':event.before_minutes},
            'after':{'исход':event.after_outcome,'минуты':event.after_minutes}, 'url':f'/admin/lessons/{event.lesson_id}/attendance'})
    for event, submission, target, assignment, actor in db.execute(select(ReviewEvent, Submission, AssignmentTarget, Assignment, User)
            .join(Submission, Submission.id == ReviewEvent.submission_id).join(AssignmentTarget, AssignmentTarget.id == Submission.target_id)
            .join(Assignment, Assignment.id == AssignmentTarget.assignment_id).outerjoin(User, User.id == ReviewEvent.actor_id)
            .order_by(ReviewEvent.created_at.desc(), ReviewEvent.id.desc()).limit(50)):
        events.append({'at':event.created_at,'actor':actor.display_name if actor else '—','student':students.get(target.student_id,'Ученик'),
            'what':'Проверка задания · '+assignment.title,'before':{},'after':{'статус':event.state},
            'url':f'/admin/assignments/{assignment.id}'})
    for event, assignment, actor in db.execute(select(DeadlineEvent, Assignment, User).join(Assignment, Assignment.id == DeadlineEvent.assignment_id)
            .join(User, User.id == DeadlineEvent.actor_id).order_by(DeadlineEvent.created_at.desc(), DeadlineEvent.id.desc()).limit(50)):
        events.append({'at':event.created_at,'actor':actor.display_name,'student':'Все назначенные ученики',
            'what':'Срок задания · '+assignment.title,'before':{'срок':event.previous_due_at.isoformat()},
            'after':{'срок':event.due_at.isoformat()},'url':f'/admin/assignments/{assignment.id}'})
    for event, actor in db.execute(select(StudentStatusEvent, User).outerjoin(User, User.id == StudentStatusEvent.changed_by)
            .order_by(StudentStatusEvent.changed_at.desc(), StudentStatusEvent.id.desc()).limit(50)):
        events.append({'at':event.changed_at,'actor':actor.display_name if actor else '—','student':students.get(event.student_id,'Ученик'),
            'what':'Статус ученика','before':{},'after':{'статус':event.status,'доступ': 'включён' if event.access_enabled else 'выключен'},
            'url':f'/admin/students/{event.student_id}/edit'})
    for event, record, note, actor in db.execute(select(TeachingRevision, LessonRecord, StudentNote, User)
            .outerjoin(LessonRecord, LessonRecord.id == TeachingRevision.lesson_record_id)
            .outerjoin(StudentNote, StudentNote.id == TeachingRevision.note_id)
            .join(User, User.id == TeachingRevision.actor_id)
            .order_by(TeachingRevision.created_at.desc(), TeachingRevision.id.desc()).limit(50)):
        student_id = record.student_id if record else note.student_id if note else None
        if student_id:
            kind = 'педагогической записи' if record else 'личной заметки'
            events.append({'at':event.created_at,'actor':actor.display_name,'student':students.get(student_id,'Ученик'),
                'what':f'Изменение {kind} · {event.action}','before':{},'after':{},
                'url':f'/admin/students/{student_id}?section=history'})
    for event, topic, actor in db.execute(select(TopicEvent, Topic, User).join(Topic, Topic.id == TopicEvent.topic_id)
            .join(User, User.id == TopicEvent.actor_id).order_by(TopicEvent.created_at.desc(), TopicEvent.id.desc()).limit(50)):
        try:
            before = {key:value for key,value in json.loads(event.before_json or '{}').items() if key == 'status'}
            after = {key:value for key,value in json.loads(event.after_json or '{}').items() if key == 'status'}
        except (ValueError, TypeError): before, after = {}, {}
        events.append({'at':event.created_at,'actor':actor.display_name,'student':'Учебный прогресс',
            'what':'Тема · '+topic.title,'before':before,'after':after,'url':'/admin/topics'})
    for event, answer, version, actor in db.execute(select(QuizGradeEvent, QuizAnswer, QuizVersion, User)
            .join(QuizAnswer, QuizAnswer.id == QuizGradeEvent.answer_id).join(QuizVersion, QuizVersion.id == QuizAnswer.version_id)
            .join(User, User.id == QuizGradeEvent.actor_id).order_by(QuizGradeEvent.created_at.desc(), QuizGradeEvent.id.desc()).limit(50)):
        attempt = db.get(QuizAttempt, answer.attempt_id)
        events.append({'at':event.created_at,'actor':actor.display_name,'student':students.get(attempt.student_id,'Ученик') if attempt else 'Ученик',
            'what':'Оценка теста · '+version.title,'before':{'баллы':event.old_points},'after':{'баллы':event.new_points},
            'url':f'/admin/quizzes/attempts/{answer.attempt_id}'})
    events.sort(key=lambda item:item['at'] or utc_now(), reverse=True)
    for event in events: event['at'] = event['at'].isoformat() if event['at'] else ''
    return render(request,'admin/history.html',user=user,events=events[:100])


@router.get('/productivity/notifications')
def notifications(request: Request, db: Session = Depends(db_session), user: User = Depends(require_admin)):
    return render(request, 'admin/notifications.html', user=user, notifications=active_notifications(db, user.id))


@router.post('/productivity/notifications/read')
async def mark_read(request: Request, db: Session = Depends(db_session), user: User = Depends(require_admin)):
    form = await validated_form(request)
    key = str(form.get('key', ''))[:120]
    if not key:
        raise HTTPException(400, 'Выберите уведомление.')
    now = utc_now()
    existing = db.get(UserNotificationState, (user.id, key))
    if not existing:
        db.add(UserNotificationState(user_id=user.id, notification_key=key, read_at=now))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    return JSONResponse({'read': True})
