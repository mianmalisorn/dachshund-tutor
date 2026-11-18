"""Admin-only attendance views; student routes and navigation are unchanged."""
from datetime import timedelta
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from ..models import Lesson, User, utc_now
from ..attendance_models import LessonAttendance, AttendanceEvent
from .. import attendance as logic
from ..web import db_session, require_admin, validated_form, render

router=APIRouter(prefix='/admin')

def get_lesson(db,lesson_id):
    row=db.get(Lesson,lesson_id)
    if not row:raise HTTPException(404,'Занятие не найдено')
    return row

def form_page(request,db,user,lesson,values=None,error=None,code=200):
    current=db.get(LessonAttendance,lesson.id)
    if values is None:
        values={'version':current.version if current else 0,'lesson_version':lesson.version,'creation_key':uuid4().hex,
                'outcome':current.outcome if current else ('conducted' if lesson.state=='scheduled' and utc_now()>=lesson.starts_at else 'unknown'),'actual_minutes':current.actual_minutes if current and current.actual_minutes else '',
                'comment':current.comment if current else '', 'reason':''}
    try:page=max(1,int(request.query_params.get('page','1')))
    except ValueError:raise HTTPException(400,'Проверьте номер страницы')
    events=list(db.scalars(select(AttendanceEvent).where(AttendanceEvent.lesson_id==lesson.id).order_by(AttendanceEvent.id.desc()).offset((page-1)*30).limit(31)))
    response=render(request,'admin/attendance_form.html',user=user,lesson=lesson,current=current,values=values,labels=logic.LABELS,
        actors={actor.id:actor.display_name for actor in db.scalars(select(User).where(User.id.in_([e.actor_id for e in events])))},can_finish_early=lesson.state=='scheduled' and lesson.starts_at<=utc_now()<lesson.starts_at+timedelta(minutes=lesson.duration_minutes),
        events=events[:30],has_more=len(events)>30,page=page,form_error=error,can_complete=utc_now()>=lesson.starts_at+timedelta(minutes=lesson.duration_minutes))
    response.status_code=code;return response

@router.get('/attendance')
def overview(request:Request,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    data=logic.report(db,request.query_params,request.app.state.settings.timezone)
    data['students']=list(db.scalars(select(User).where(User.role=='student').order_by(User.display_name,User.id)))
    return render(request,'admin/attendance.html',user=user,**data)

@router.get('/lessons/{lesson_id}/attendance')
def edit(request:Request,lesson_id:int,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    return form_page(request,db,user,get_lesson(db,lesson_id))

@router.post('/lessons/{lesson_id}/attendance')
async def save(request:Request,lesson_id:int,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    form=await validated_form(request)
    try:
        logic.save(db,lesson_id,form,user.id);db.commit()
    except (HTTPException,SQLAlchemyError) as exc:
        db.rollback()
        if isinstance(exc,HTTPException) and exc.status_code==404:raise
        code=exc.status_code if isinstance(exc,HTTPException) else 503
        error=exc.detail if isinstance(exc,HTTPException) else 'Не удалось сохранить. Поля сохранены ниже. Проверьте результат перед повтором.'
        return form_page(request,db,user,get_lesson(db,lesson_id),dict(form),error,code)
    if form.get('return_to_schedule')=='yes':
        from ..scheduling import local
        day=local(get_lesson(db,lesson_id).starts_at,request.app.state.settings.timezone).date()
        return RedirectResponse(f'/admin/lessons?day={day}&notice=saved',status_code=303)
    return RedirectResponse(f'/admin/lessons/{lesson_id}/attendance?notice=saved',status_code=303)
