"""Explicit outcomes; serialized with the calendar, no financial operations."""
from datetime import date, datetime, time, timedelta, timezone
from uuid import uuid4
import hashlib, json
from fastapi import HTTPException
from sqlalchemy import select, func, case, and_, or_
from .models import Lesson, LessonCompletion, User, utc_now
from .attendance_models import LessonAttendance, AttendanceEvent, OUTCOMES
from . import scheduling
from .web import clean_text
from .progress import creation_key

LABELS = {'unknown':'Результат неизвестен','conducted':'Проведено','student_absent':'Ученик не явился',
          'teacher_absent':'Преподаватель не явился','cancelled_student':'Отменено учеником',
          'cancelled_teacher':'Отменено преподавателем','other':'Не состоялось по другой причине'}
CATEGORY_LABELS = {**LABELS,'not_finished':'Ещё не завершено','cancelled_unknown':'Отменено · инициатор неизвестен','suppressed':'Исключено правилом серии'}
CANCELLATIONS = {'cancelled_student','cancelled_teacher'}

def has_result(db, lesson_id):
    row = db.get(LessonAttendance, lesson_id)
    return bool(row and row.outcome != 'unknown')

def values(row):
    return (row.outcome, row.actual_minutes, row.comment) if row else ('unknown', None, '')

def record(db, row, before, actor, reason, key, digest, now):
    db.add(AttendanceEvent(lesson_id=row.lesson_id,actor_id=actor,created_at=now,
        request_key=key,request_hash=digest,before_outcome=before[0],before_minutes=before[1],before_comment=before[2],
        after_outcome=row.outcome,after_minutes=row.actual_minutes,after_comment=row.comment,reason=reason,result_version=row.version))

def clear_on_reschedule(db, lesson, actor):
    """An explicit restore/move retracts a cancellation in the same transaction."""
    row=db.get(LessonAttendance,lesson.id)
    if row and row.outcome in CANCELLATIONS:
        before=values(row);row.outcome='unknown';row.actual_minutes=None;row.comment=''
        row.version+=1;row.updated_at=utc_now();row.updated_by=actor
        record(db,row,before,actor,'Отмена снята явным восстановлением / переносом встречи',uuid4().hex,'calendar',row.updated_at)

def save(db, lesson_id, form, actor, now=None):
    now=now or utc_now()
    scheduling.lock(db)
    db.expire_all()  # Re-read versions after acquiring the cross-process SQLite write lock.
    lesson=db.get(Lesson,lesson_id)
    if not lesson:raise HTTPException(404,'Занятие не найдено')
    key=creation_key(form.get('creation_key'))
    # Bind replay protection to object, versions and submitted content, not just the token.
    fields=('version','lesson_version','outcome','actual_minutes','comment','reason','restore_schedule','confirm_early')
    digest=hashlib.sha256(json.dumps([lesson_id,actor,[(k,str(form.get(k,''))) for k in fields]],ensure_ascii=False).encode()).hexdigest()
    previous=db.scalar(select(AttendanceEvent).where(AttendanceEvent.request_key==key))
    if previous:
        if previous.request_hash!=digest:raise HTTPException(409,'Ключ формы уже использован для других данных. Обновите форму.')
        return db.get(LessonAttendance,lesson_id)
    row=db.get(LessonAttendance,lesson_id)
    try:expected=int(form.get('version',''));expected_lesson=int(form.get('lesson_version',''))
    except (TypeError,ValueError):raise HTTPException(409,'Обновите форму: отсутствует версия записи.')
    if expected!=(row.version if row else 0) or expected_lesson!=lesson.version:
        raise HTTPException(409,'Посещаемость или расписание изменились в другой вкладке. Ваши поля сохранены; сравните их с актуальными данными.')
    outcome=str(form.get('outcome',''))
    if outcome not in OUTCOMES:raise HTTPException(400,'Выберите результат занятия')
    comment=clean_text(form.get('comment'),2000,'Комментарий',False)
    reason=clean_text(form.get('reason'),500,'Причина исправления',bool(row))
    raw=str(form.get('actual_minutes','')).strip();minutes=None
    if raw:
        try:minutes=int(raw)
        except ValueError:raise HTTPException(400,'Фактическая длительность — целое число минут')
        if outcome!='conducted' or not 1<=minutes<=1440:raise HTTPException(400,'Длительность от 1 до 1440 минут допустима только для проведённого занятия')
    early=outcome=='conducted' and now<lesson.starts_at+timedelta(minutes=lesson.duration_minutes)
    if early:
        if now<lesson.starts_at:raise HTTPException(400,'Занятие ещё не началось. Отметить его проведённым нельзя даже с подтверждением.')
        if lesson.state!='scheduled':raise HTTPException(409,'Для досрочного завершения встреча должна быть активной в расписании.')
        if form.get('confirm_early')!='yes':raise HTTPException(400,'Подтвердите, что уже начавшееся занятие действительно завершено досрочно.')
        reason=clean_text('Досрочное завершение подтверждено до планового окончания. '+reason,500,'Причина и подтверждение досрочного завершения')
    elif outcome not in CANCELLATIONS|{'unknown'} and now<lesson.starts_at+timedelta(minutes=lesson.duration_minutes):
        raise HTTPException(400,'Отметить неявку или другую причину можно после планового окончания встречи')
    if lesson.state=='suppressed' and outcome!='unknown':
        raise HTTPException(409,'Встреча исключена правилом серии. Сначала восстановите её в расписании.')
    if db.get(LessonCompletion,lesson_id) and outcome not in {'unknown','conducted'}:
        raise HTTPException(409,'У встречи есть педагогический итог. Сначала исправьте ошибочный итог в карточке ученика; история сохранится.')
    before=values(row)
    if lesson.state=='cancelled' and outcome not in CANCELLATIONS|{'unknown'}:
        if form.get('restore_schedule')!='yes':raise HTTPException(409,'Подтвердите снятие отмены в расписании при исправлении результата.')
        if outcome=='conducted':scheduling.check_conflict(db,lesson.starts_at,lesson.duration_minutes,lesson.id,(lesson.series_id,lesson.occurrence_date),now)
    old_schedule=scheduling.snapshot(lesson)
    if outcome in CANCELLATIONS:lesson.state='cancelled';lesson.is_exception=True
    elif outcome!='unknown' and lesson.state=='cancelled':lesson.state='scheduled'
    # Only a calendar change invalidates its outbox version. Editing private comments must not resend reminders.
    if scheduling.snapshot(lesson)!=old_schedule:
        lesson.version+=1
        scheduling.event(db,lesson,'attendance',actor,old_schedule)
    if row is None:
        row=LessonAttendance(lesson_id=lesson.id,student_id=lesson.student_id,version=1)
        db.add(row)
    else:row.version+=1
    row.outcome=outcome;row.actual_minutes=minutes;row.comment=comment;row.updated_at=now;row.updated_by=actor
    db.flush()
    record(db,row,before,actor,reason,key,digest,now)
    return row

def period(params,tz,now=None):
    today=scheduling.local(now or utc_now(),tz).date()
    preset=params.get('period','month')
    try:
        if preset=='week':first=today-timedelta(days=today.weekday());last=first+timedelta(days=6)
        elif preset=='month':first=today.replace(day=1);last=(first.replace(day=28)+timedelta(days=4)).replace(day=1)-timedelta(days=1)
        elif preset=='custom':first=date.fromisoformat(params.get('from',''));last=date.fromisoformat(params.get('to',''))
        else:raise ValueError()
        if last<first or (last-first).days>365:raise ValueError()
        # Reporting boundaries are dates, not appointments; zoneinfo resolves midnight offsets.
        zone=scheduling.zone(tz)
        start=datetime.combine(first,time.min,zone).astimezone(timezone.utc).replace(tzinfo=None)
        end=datetime.combine(last+timedelta(days=1),time.min,zone).astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError,TypeError,OverflowError):raise HTTPException(400,'Выберите период от 1 до 366 дней с корректными датами')
    return first,last,start,end,preset

def report(db,params,tz,now=None):
    now=now or utc_now();first,last,start,end,preset=period(params,tz,now)
    try:
        sid=int(params.get('student_id') or 0);page=int(params.get('page','1'))
        if not 0<=sid<=2147483647 or not 1<=page<=1000000:raise ValueError()
    except (ValueError,TypeError):raise HTTPException(400,'Проверьте ученика и номер страницы')
    if sid:
        student=db.get(User,sid)
        if not student or student.role!='student':raise HTTPException(404,'Ученик не найден')
    selected=params.get('outcome','');moved=params.get('moved')=='yes'
    if selected and selected not in CATEGORY_LABELS:raise HTTPException(400,'Неизвестный фильтр результата')
    finish=func.datetime(Lesson.starts_at,func.printf('+%d minutes',Lesson.duration_minutes))
    category=case((and_(LessonAttendance.outcome.is_not(None),LessonAttendance.outcome!='unknown'),LessonAttendance.outcome),
        (Lesson.state=='suppressed','suppressed'),(Lesson.state=='cancelled','cancelled_unknown'),
        (finish>now.strftime('%Y-%m-%d %H:%M:%S'),'not_finished'),else_='unknown')
    base=select(Lesson,LessonAttendance,category.label('category')).outerjoin(LessonAttendance,LessonAttendance.lesson_id==Lesson.id).where(Lesson.starts_at>=start,Lesson.starts_at<end)
    if sid:base=base.where(Lesson.student_id==sid)
    if moved:base=base.where(Lesson.is_exception.is_(True),Lesson.original_starts_at.is_not(None),Lesson.starts_at!=Lesson.original_starts_at)
    # Distribution describes the whole period/student/move selection, independently of result filter and pagination.
    sub=base.subquery();counts={name:0 for name in CATEGORY_LABELS}
    counts.update(dict(db.execute(select(sub.c.category,func.count()).group_by(sub.c.category)).all()))
    if selected:base=base.where(category==selected)
    total=db.scalar(select(func.count()).select_from(base.subquery()))
    from sqlalchemy.orm import selectinload
    rows=db.execute(base.options(selectinload(Lesson.student)).order_by(Lesson.starts_at.desc(),Lesson.id.desc()).offset((page-1)*30).limit(30)).all()
    return dict(rows=rows,counts=counts,total=total,page=page,pages=max(1,(total+29)//30),first=first,last=last,preset=preset,student_id=sid,selected=selected,moved=moved,labels=CATEGORY_LABELS)
