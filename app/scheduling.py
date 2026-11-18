"""One calendar, bounded weekly expansion and serialized schedule mutations."""
import json
from datetime import datetime,date,time,timedelta,timezone
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import select,update
from .models import Lesson,LessonSeries,LessonCompletion,ScheduleLock,ScheduleEvent,User,utc_now
from .logic import meeting_url
from .web import clean_text,parse_id

def attendance_fixed(db, lesson_id):
    from .attendance import has_result
    return has_result(db,lesson_id)

WINDOW_DAYS=84


def zone(name):
    try:return ZoneInfo(str(name))
    except (ZoneInfoNotFoundError,ValueError,TypeError):raise HTTPException(400,'Укажите существующий IANA-пояс, например Europe/Moscow')


def local_to_utc(value,tz_name):
    try:local=datetime.fromisoformat(str(value))
    except ValueError:raise HTTPException(400,'Проверьте дату и время')
    if local.tzinfo or local.second or local.microsecond:raise HTTPException(400,'Нужно местное время с точностью до минуты, без смещения UTC')
    tz=zone(tz_name);choices=set()
    for fold in (0,1):
        aware=local.replace(tzinfo=tz,fold=fold);absolute=aware.astimezone(timezone.utc)
        if absolute.astimezone(tz).replace(tzinfo=None)==local:choices.add(absolute.replace(tzinfo=None))
    if not choices:raise HTTPException(400,'Это местное время не существует из-за перевода часов. Выберите другое время.')
    if len(choices)>1:raise HTTPException(400,'Это местное время встречается дважды. Для разового занятия задайте точный момент в поясе UTC; для серии выберите другое время.')
    return choices.pop()


def local(dt,tz_name):return dt.replace(tzinfo=timezone.utc).astimezone(zone(tz_name))


def lock(db):
    # Database row lock also serializes separate processes; SQLite obtains its write lock.
    if db.execute(update(ScheduleLock).where(ScheduleLock.id==1).values(revision=ScheduleLock.revision+1)).rowcount!=1:
        raise RuntimeError('Missing schedule lock migration')


def key(raw):
    from .progress import creation_key
    return creation_key(raw) if raw else uuid4().hex


def student(db,ident):
    user=db.get(User,parse_id(ident,'ученика'))
    if not user or user.role!='student' or not user.active or user.learning_status!='active':raise HTTPException(400,'Выберите активного ученика')
    return user


def common(form):
    try:duration=int(form.get('duration_minutes',60))
    except (ValueError,TypeError):raise HTTPException(400,'Проверьте длительность')
    if not 15<=duration<=240:raise HTTPException(400,'Длительность от 15 до 240 минут')
    url=clean_text(form.get('meeting_url'),500,'Ссылка',False)
    if url:
        try:url=meeting_url(url)
        except ValueError as exc:raise HTTPException(400,str(exc))
    return dict(duration_minutes=duration,meeting_url=url,note=clean_text(form.get('note'),500,'Организационная заметка',False))


def snapshot(row):
    names=['starts_at','duration_minutes','meeting_url','note','state','timezone_name','is_exception'] if isinstance(row,Lesson) else ['weekdays','local_time','timezone_name','interval_weeks','start_date','end_date','duration_minutes','meeting_url','note','state']
    return {name:str(getattr(row,name)) for name in names}


def event(db,row,kind,actor=None,before=None):
    db.add(ScheduleEvent(lesson_id=row.id if isinstance(row,Lesson) else None,series_id=row.id if isinstance(row,LessonSeries) else row.series_id,
        actor_id=actor,kind=kind,before_json=json.dumps(before or {},ensure_ascii=False),after_json=json.dumps(snapshot(row),ensure_ascii=False)))


def occurrences(series,first,last):
    day=max(first,series.start_date);last=min(last,series.end_date or last)
    anchor=series.start_date-timedelta(days=series.start_date.weekday())
    weekdays={int(x) for x in series.weekdays.split(',')}
    while day<=last:
        if day.weekday() in weekdays and ((day-anchor).days//7)%series.interval_weeks==0:
            yield day,local_to_utc(datetime.combine(day,series.local_time).isoformat(),series.timezone_name)
        day+=timedelta(days=1)


def check_conflict(db,start,duration,ignore_id=None,slot=None,now=None):
    now=now or utc_now()
    end=start+timedelta(minutes=duration)
    rows=db.scalars(select(Lesson).where(Lesson.state=='scheduled',Lesson.starts_at<end,Lesson.starts_at>start-timedelta(hours=4)))
    for row in rows:
        if row.id!=ignore_id and start<row.starts_at+timedelta(minutes=row.duration_minutes):
            raise HTTPException(409,f'Пересечение с занятием №{row.id}. Выберите другое время; соседние занятия допустимы.')
    # Check virtual occurrences too: a one-off beyond the materialized window is not a free slot.
    for series in db.scalars(select(LessonSeries).where(LessonSeries.state=='active')):
        pupil=db.get(User,series.student_id)
        if not pupil.active or pupil.learning_status!='active':continue
        first=local(start-timedelta(hours=4),series.timezone_name).date()
        last=local(end,series.timezone_name).date()
        for day,at in occurrences(series,first,last):
            if slot==(series.id,day):continue
            existing=db.scalar(select(Lesson).where(Lesson.series_id==series.id,Lesson.occurrence_date==day))
            if existing and (existing.starts_at<now or existing.is_exception or db.get(LessonCompletion,existing.id) or attendance_fixed(db,existing.id)):continue
            if at<end and start<at+timedelta(minutes=series.duration_minutes):
                raise HTTPException(409,f'Пересечение с регулярным правилом №{series.id}. Выберите другое время.')


def refresh_series(db,series,now=None):
    now=now or utc_now();pupil=db.get(User,series.student_id)
    enabled=series.state=='active' and pupil.active and pupil.learning_status=='active'
    rows=list(db.scalars(select(Lesson).where(Lesson.series_id==series.id)))
    by_day={row.occurrence_date:row for row in rows}
    old_states={row.id:row.state for row in rows}
    # Suppression is reversible and never destroys individual exceptions or conducted history.
    for row in rows:
        if row.starts_at>=now and not row.is_exception and not db.get(LessonCompletion,row.id) and not attendance_fixed(db,row.id):
            row.state='suppressed'
    db.flush()
    if not enabled:
        for row in rows:
            if row.state!=old_states[row.id]:row.version+=1
        return
    first=local(now,series.timezone_name).date();last=first+timedelta(days=WINDOW_DAYS)
    for day,at in occurrences(series,first,last):
        if at<now:continue
        row=by_day.get(day)
        if row and (row.starts_at<now or row.is_exception or db.get(LessonCompletion,row.id) or attendance_fixed(db,row.id)):continue
        check_conflict(db,at,series.duration_minutes,row.id if row else None,(series.id,day),now)
        values=dict(starts_at=at,duration_minutes=series.duration_minutes,meeting_url=series.meeting_url,note=series.note,timezone_name=series.timezone_name,state='scheduled')
        if row:
            # Suppression above is temporary; compare meaningful scheduling properties.
            if any(getattr(row,k)!=v for k,v in values.items() if k!='state'):
                row.version+=1
            for k,v in values.items():setattr(row,k,v)
        else:
            row=Lesson(student_id=series.student_id,series_id=series.id,occurrence_date=day,original_starts_at=at,**values)
            db.add(row)
        db.flush()

    for row in rows:
        if row.state!=old_states[row.id]:row.version+=1


def refresh_all(db,now=None):
    lock(db);errors=[]
    for series in list(db.scalars(select(LessonSeries))):
        try:
            with db.begin_nested():refresh_series(db,series,now)
        except HTTPException as exc:errors.append(f'Правило №{series.id}: {exc.detail}')
    db.commit();return errors


def create_lesson(db,form,default_zone,actor,now=None):
    lock(db);creation=key(form.get('creation_key'))
    old=db.scalar(select(Lesson).where(Lesson.creation_key==creation))
    if old:return old
    pupil=student(db,form.get('student_id'));tz=str(form.get('timezone_name') or default_zone);start=local_to_utc(form.get('starts_at'),tz)
    if start<(now or utc_now()):raise HTTPException(400,'Для прошедшего урока используйте запись итогов в карточке ученика')
    values=common(form);check_conflict(db,start,values['duration_minutes'],now=now)
    row=Lesson(student_id=pupil.id,starts_at=start,original_starts_at=start,timezone_name=tz,creation_key=creation,**values)
    db.add(row);db.flush();event(db,row,'created',actor);return row


def mutate_lesson(db,ident,form,actor,default_zone,now=None):
    lock(db);row=db.get(Lesson,ident)
    if not row:raise HTTPException(404)
    if parse_id(form.get('version'),'версии')!=row.version:raise HTTPException(409,'Занятие изменено в другой вкладке. Ваши поля сохранены ниже.')
    if db.get(LessonCompletion,row.id):raise HTTPException(409,'У занятия уже записан итог. Изменение расписания не должно менять проведённый урок.')
    if row.starts_at<(now or utc_now()):raise HTTPException(400,'Прошедшее расписание не изменяется. Исправьте фактическую запись урока в карточке.')
    before=snapshot(row);action=form.get('action','move')
    from .attendance_models import LessonAttendance
    from .attendance import CANCELLATIONS, clear_on_reschedule
    attendance=db.get(LessonAttendance,row.id)
    if attendance and attendance.outcome not in CANCELLATIONS|{'unknown'}:
        raise HTTPException(409,'Сначала исправьте фактический результат встречи в посещаемости.')
    if action=='cancel':row.state='cancelled'
    elif action in ('move','restore'):
        if action=='move':
            tz=str(form.get('timezone_name') or row.timezone_name or default_zone)
            start=local_to_utc(form.get('starts_at'),tz);values=common(form)
            if start<(now or utc_now()):raise HTTPException(400,'Нельзя перенести встречу в прошлое')
        else:tz=row.timezone_name or default_zone;start=row.starts_at;values=common({'duration_minutes':row.duration_minutes,'meeting_url':row.meeting_url,'note':row.note})
        check_conflict(db,start,values['duration_minutes'],row.id,(row.series_id,row.occurrence_date),now)
        if action=='move' and row.original_starts_at is None and start!=row.starts_at:
            row.original_starts_at=row.starts_at  # Known time immediately before this explicit move, never inferred history.
        row.starts_at=start;row.timezone_name=tz;row.state='scheduled'
        for k,v in values.items():setattr(row,k,v)
    else:raise HTTPException(400,'Неизвестное действие')
    if action in ('move','restore'):clear_on_reschedule(db,row,actor)
    row.is_exception=True;row.version+=1;event(db,row,action,actor,before);return row


def save_series(db,form,actor,ident=None,now=None):
    lock(db);now=now or utc_now();row=db.get(LessonSeries,ident) if ident else None
    if ident and not row:raise HTTPException(404)
    creation=key(form.get('creation_key'))
    if not row:
        old=db.scalar(select(LessonSeries).where(LessonSeries.creation_key==creation))
        if old:return old
    elif parse_id(form.get('version'),'версии')!=row.version:raise HTTPException(409,'Правило изменилось в другой вкладке. Ваши поля сохранены.')
    if row and row.state=='ended':raise HTTPException(400,'Завершённое правило не изменяется. Создайте новое.')
    before=snapshot(row) if row else None;action=form.get('action','save')
    if row and action in ('pause','resume','end'):
        row.state={'pause':'paused','resume':'active','end':'ended'}[action]
    else:
        pupil=student(db,form.get('student_id') if not row else row.student_id)
        tz=str(form.get('timezone_name',''));zone(tz)
        try:
            weekdays=sorted({int(x) for x in form.getlist('weekdays')})
            at=time.fromisoformat(str(form.get('local_time','')));start=date.fromisoformat(str(form.get('start_date','')))
            end=date.fromisoformat(str(form['end_date'])) if form.get('end_date') else None
            every=int(form.get('interval_weeks',1))
            if not weekdays or not set(weekdays)<=set(range(7)) or every not in (1,2) or at.second or at.microsecond or at.tzinfo or (end and end<start):raise ValueError()
            if not row and start<local(now,tz).date():raise ValueError()
            if start>local(now,tz).date()+timedelta(days=WINDOW_DAYS):raise ValueError()
        except (ValueError,TypeError):raise HTTPException(400,'Проверьте дни, время и даты. Начало нового правила — не в прошлом и в пределах 84 дней.')
        if not row:row=LessonSeries(student_id=pupil.id,creation_key=creation);db.add(row)
        for k,v in dict(weekdays=','.join(map(str,weekdays)),local_time=at,timezone_name=tz,start_date=start,end_date=end,interval_weeks=every,**common(form)).items():setattr(row,k,v)
    if ident:row.version+=1
    db.flush();refresh_series(db,row,now)
    # Future one-offs outside the window are also checked against the edited rule.
    for lesson in db.scalars(select(Lesson).where(Lesson.state=='scheduled',Lesson.starts_at>=now,Lesson.series_id.is_(None))):
        check_conflict(db,lesson.starts_at,lesson.duration_minutes,lesson.id,now=now)
    event(db,row,action,actor,before);return row


def connect_record(db,record,lesson_id,actor):
    lesson=db.get(Lesson,lesson_id)
    if not lesson or lesson.student_id!=record.student_id:raise HTTPException(400,'Запланированное занятие должно принадлежать этому ученику')
    if lesson.state!='scheduled' or lesson.starts_at>utc_now():raise HTTPException(400,'Итог можно связать с уже начавшимся активным занятием')
    from .attendance_models import LessonAttendance
    attendance=db.get(LessonAttendance,lesson_id)
    if attendance and attendance.outcome not in {'unknown','conducted'}:
        raise HTTPException(409,'Результат посещаемости не допускает педагогический итог. Сначала исправьте результат встречи.')
    bound=db.scalar(select(LessonCompletion).where(LessonCompletion.record_id==record.id))
    if bound and bound.lesson_id!=lesson_id:raise HTTPException(409,'Этот итог уже связан с другой встречей')
    old=db.get(LessonCompletion,lesson_id)
    if old and old.record_id!=record.id:raise HTTPException(409,'Итог этого занятия уже сохранён. Откройте его из расписания.')
    if not old:
        db.add(LessonCompletion(lesson_id=lesson_id,record_id=record.id,student_id=record.student_id));event(db,lesson,'recorded',actor)
