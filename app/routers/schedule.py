"""Private calendar; every write uses scheduling's shared database lock."""
from calendar import monthrange
from datetime import date,datetime,timedelta
from ..attendance_models import LessonAttendance
from ..attendance import LABELS as ATTENDANCE_LABELS
from uuid import uuid4
import json
from fastapi import APIRouter,Depends,Request,HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session,selectinload
from sqlalchemy.exc import SQLAlchemyError
from .. import scheduling as logic
from ..models import Lesson,LessonSeries,LessonCompletion,ScheduleEvent,User,utc_now
from ..web import db_session,require_admin,validated_form,render
from ..mascot import record_event
router=APIRouter(prefix='/admin')
LABELS={'scheduled':'Запланировано','cancelled':'Отменено','suppressed':'Приостановлено','active':'Действует','paused':'Пауза','ended':'Завершено'}
DAYS=['Пн','Вт','Ср','Чт','Пт','Сб','Вс']

def back(path='/admin/lessons'):return RedirectResponse(path+'?notice=saved',status_code=303)

def form_page(request,db,user,kind,row=None,values=None,error=None,code=200,prefill_student_id=None):
    if values is None:
        if row:
            values={col.name:str(getattr(row,col.name) or '') for col in row.__table__.columns}
            if kind=='lesson':values['starts_at']=logic.local(row.starts_at,row.timezone_name or request.app.state.settings.timezone).strftime('%Y-%m-%dT%H:%M')
            else:values['weekdays']=row.weekdays.split(',')
        else:values={'creation_key':uuid4().hex,'timezone_name':request.app.state.settings.timezone,'duration_minutes':'60','interval_weeks':'1','start_date':logic.local(utc_now(),request.app.state.settings.timezone).date().isoformat(),'weekdays':[]}
    if prefill_student_id is not None: values['student_id']=str(prefill_student_id)
    response=render(request,'admin/schedule_form.html',user=user,kind=kind,row=row,values=values,form_error=error,days=DAYS,
        cancel_key=uuid4().hex,attendance_row=db.get(LessonAttendance,row.id) if row and kind=='lesson' else None,
        students=list(db.scalars(select(User).where(User.role=='student',User.active.is_(True),User.learning_status=='active').order_by(User.display_name))))
    response.status_code=code;return response

def failure(exc):return (exc.status_code,exc.detail) if isinstance(exc,HTTPException) else (503,'Не удалось сохранить или расписание сейчас изменяется. Поля сохранены ниже. Проверьте результат перед повтором.')

@router.get('/lessons')
def calendar(request:Request,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    errors=[];tz=request.app.state.settings.timezone;today=logic.local(utc_now(),tz).date()
    try:day=date.fromisoformat(request.query_params.get('day',today.isoformat()))
    except ValueError:raise HTTPException(400,'Проверьте дату')
    view=request.query_params.get('view','day')
    if view not in {'day','week','month'}:raise HTTPException(400,'Неизвестный вид календаря')
    if view=='week':first=day-timedelta(days=day.weekday());span=7
    elif view=='month':
        first=day.replace(day=1);first-=timedelta(days=first.weekday());span=42
    else:first=day;span=1
    try:
        if view=='month':
            previous_month=date(day.year-1,12,1) if day.month==1 else date(day.year,day.month-1,1)
            next_month=date(day.year+1,1,1) if day.month==12 else date(day.year,day.month+1,1)
            previous_day=previous_month.replace(day=min(day.day,monthrange(previous_month.year,previous_month.month)[1]))
            next_day=next_month.replace(day=min(day.day,monthrange(next_month.year,next_month.month)[1]))
        else:
            step=7 if view=='week' else 1
            previous_day=day-timedelta(days=step);next_day=day+timedelta(days=step)
    except OverflowError:raise HTTPException(400,'Дата вне поддерживаемого диапазона')
    try:last=first+timedelta(days=span)
    except OverflowError:raise HTTPException(400,'Дата вне поддерживаемого диапазона')
    start=logic.local_to_utc(str(first)+'T00:00',tz);end=logic.local_to_utc(str(last)+'T00:00',tz)
    rows=list(db.scalars(select(Lesson).options(selectinload(Lesson.student)).where(Lesson.starts_at>=start,Lesson.starts_at<end).order_by(Lesson.starts_at,Lesson.id)))
    completions={r.lesson_id:r.record_id for r in db.scalars(select(LessonCompletion).where(LessonCompletion.lesson_id.in_([r.id for r in rows])))} if rows else {}
    marks={a.lesson_id:a for a in db.scalars(select(LessonAttendance).where(LessonAttendance.lesson_id.in_([r.id for r in rows])))} if rows else {}
    local_first=logic.local(start,tz).date();local_last=logic.local(end-timedelta(seconds=1),tz).date()
    days=[];cursor=local_first
    while cursor<=local_last:
        days.append({'date':cursor,'lessons':[]});cursor+=timedelta(days=1)
    by_day={item['date']:item for item in days}
    for row in rows:
        local_start=logic.local(row.starts_at,tz)
        bucket=by_day.get(logic.local(row.starts_at,tz).date())
        if bucket:bucket['lessons'].append((row,local_start,marks.get(row.id),completions.get(row.id)))
    weekdays = ('Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье')
    months = ('января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
              'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря')
    day_caption = f'{weekdays[day.weekday()]}, {day.day} {months[day.month - 1]}'
    return render(request,'admin/calendar.html',user=user,lessons=rows,days=days,labels=LABELS,errors=errors,day=day,today=today,day_caption=day_caption,
        first=local_first,last=local_last,view=view,previous_day=previous_day,next_day=next_day,attendance_labels=ATTENDANCE_LABELS,attendance_keys={r.id:uuid4().hex for r in rows},
        horizon=today+timedelta(days=logic.WINDOW_DAYS),timezone_name=tz,now=utc_now(),timedelta=timedelta,creation_key=uuid4().hex)
@router.get('/lessons/new')
def new_lesson(request:Request,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    values=None;prefill=request.query_params.get('starts_at');student_id=None
    raw_student_id=request.query_params.get('student_id')
    if raw_student_id:
        try: student_id=int(raw_student_id)
        except ValueError: raise HTTPException(400,'Проверьте ученика')
        student=db.get(User,student_id)
        if not student or student.role!='student' or not student.active or student.learning_status!='active':
            raise HTTPException(404,'Активный ученик не найден')
    if prefill:
        try:
            parsed=datetime.fromisoformat(prefill)
            if parsed.tzinfo or parsed.second or parsed.microsecond:raise ValueError
            values={'creation_key':uuid4().hex,'timezone_name':request.app.state.settings.timezone,'duration_minutes':'60','interval_weeks':'1','start_date':logic.local(utc_now(),request.app.state.settings.timezone).date().isoformat(),'weekdays':[],'starts_at':parsed.strftime('%Y-%m-%dT%H:%M')}
        except ValueError:raise HTTPException(400,'Проверьте время нового занятия')
    return form_page(request,db,user,'lesson',values=values,prefill_student_id=student_id)
@router.post('/lessons')
async def create(request:Request,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    form=await validated_form(request)
    try:
        row=logic.create_lesson(db,form,request.app.state.settings.timezone,user.id)
        created=any(isinstance(item,ScheduleEvent) and item.kind=='created' and item.lesson_id==row.id for item in db.new)
        db.commit()
    except (HTTPException,SQLAlchemyError) as exc:
        db.rollback();code,error=failure(exc);return form_page(request,db,user,'lesson',values=dict(form),error=error,code=code)
    if created:record_event(request,user,'lesson.created')
    day=logic.local(row.starts_at,request.app.state.settings.timezone).date()
    return RedirectResponse(f'/admin/lessons?day={day}&notice=saved',status_code=303)

@router.get('/lessons/{lesson_id}/edit')
def edit_lesson(request:Request,lesson_id:int,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    row=db.get(Lesson,lesson_id)
    if not row:raise HTTPException(404)
    return form_page(request,db,user,'lesson',row)

@router.post('/lessons/{lesson_id}/edit')
@router.post('/lessons/{lesson_id}/delete')
async def edit(request:Request,lesson_id:int,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    form=await validated_form(request);values=dict(form)
    if request.url.path.endswith('/delete'):values['action']='cancel'
    try:
        row=logic.mutate_lesson(db,lesson_id,values,user.id,request.app.state.settings.timezone);db.commit()
    except (HTTPException,SQLAlchemyError) as exc:
        db.rollback();code,error=failure(exc);return form_page(request,db,user,'lesson',db.get(Lesson,lesson_id),values,error,code)
    day=logic.local(row.starts_at,request.app.state.settings.timezone).date()
    return RedirectResponse(f'/admin/lessons?day={day}&notice=saved',status_code=303)

@router.get('/lesson-series')
def series_list(request:Request,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    return render(request,'admin/series.html',user=user,series=list(db.scalars(select(LessonSeries).options(selectinload(LessonSeries.student)).order_by(LessonSeries.id.desc()))),labels=LABELS,days=DAYS)

@router.get('/lesson-series/new')
def new_series(request:Request,db:Session=Depends(db_session),user:User=Depends(require_admin)):return form_page(request,db,user,'series')

@router.get('/lesson-series/{series_id}/edit')
def edit_series(request:Request,series_id:int,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    row=db.get(LessonSeries,series_id)
    if not row:raise HTTPException(404)
    if row.state=='ended':return RedirectResponse(f'/admin/lesson-series/{row.id}/history',status_code=303)
    return form_page(request,db,user,'series',row)

async def write_series(request,db,user,series_id=None):
    form=await validated_form(request);values=dict(form);values['weekdays']=form.getlist('weekdays')
    try:logic.save_series(db,form,user.id,series_id);db.commit()
    except (HTTPException,SQLAlchemyError) as exc:
        db.rollback();code,error=failure(exc);return form_page(request,db,user,'series',db.get(LessonSeries,series_id) if series_id else None,values,error,code)
    return back('/admin/lesson-series')

@router.post('/lesson-series')
async def create_series(request:Request,db:Session=Depends(db_session),user:User=Depends(require_admin)):return await write_series(request,db,user)

@router.post('/lesson-series/{series_id}/edit')
async def update_series(request:Request,series_id:int,db:Session=Depends(db_session),user:User=Depends(require_admin)):return await write_series(request,db,user,series_id)

@router.get('/lessons/{lesson_id}/history')
def history(request:Request,lesson_id:int,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    row=db.get(Lesson,lesson_id)
    if not row:raise HTTPException(404)
    events=list(db.scalars(select(ScheduleEvent).where(ScheduleEvent.lesson_id==lesson_id).order_by(ScheduleEvent.id.desc()).limit(100)))
    return render(request,'admin/schedule_history.html',user=user,lesson=row,events=[(e,json.loads(e.before_json),json.loads(e.after_json)) for e in events])


@router.get('/lesson-series/{series_id}/history')
def series_history(request:Request,series_id:int,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    row=db.get(LessonSeries,series_id)
    if not row:raise HTTPException(404)
    events=list(db.scalars(select(ScheduleEvent).where(ScheduleEvent.series_id==series_id,ScheduleEvent.lesson_id.is_(None)).order_by(ScheduleEvent.id.desc()).limit(100)))
    return render(request,'admin/schedule_history.html',user=user,lesson=row,is_series=True,events=[(e,json.loads(e.before_json),json.loads(e.after_json)) for e in events])
