from fastapi import APIRouter,Depends,Request,HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from ..models import ReminderPreference,ReminderDelivery,User
from ..web import require_admin,db_session,render,validated_form,parse_id
from ..scheduling import lock
from ..reminders import TelegramConfig
router=APIRouter(prefix='/admin/reminders')

def page(request,db,user,values=None,error=None,code=200):
    prefs=db.get(ReminderPreference,user.id)
    if values is None:values={'enabled':'yes' if prefs and prefs.enabled else '', 'minutes_before':str(prefs.minutes_before if prefs else 30),'message_format':prefs.message_format if prefs else 'compact','version':prefs.version if prefs else 0}
    cfg=getattr(request.app.state,'calendar_telegram_config',None) or TelegramConfig.from_env()
    response=render(request,'admin/reminders.html',user=user,values=values,form_error=error,connected=cfg.configured and cfg.admin_id==user.id,worker_enabled=cfg.enabled,
        deliveries=list(db.scalars(select(ReminderDelivery).where(ReminderDelivery.user_id==user.id).order_by(ReminderDelivery.id.desc()).limit(30))))
    response.status_code=code;return response

@router.get('')
def settings(request:Request,db:Session=Depends(db_session),user:User=Depends(require_admin)):return page(request,db,user)

@router.post('')
async def save(request:Request,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    form=await validated_form(request);values=dict(form)
    try:
        lock(db);prefs=db.get(ReminderPreference,user.id)
        if str(form.get('version',''))!=str(prefs.version if prefs else 0):raise HTTPException(409,'Настройки изменились в другой вкладке')
        minutes=parse_id(form.get('minutes_before'),'интервала');fmt=form.get('message_format')
        if minutes not in (10,30,60) or fmt not in ('compact','links'):raise HTTPException(400,'Выберите интервал и формат из списка')
        if not prefs:prefs=ReminderPreference(user_id=user.id,version=0);db.add(prefs)
        prefs.enabled=form.get('enabled')=='yes';prefs.minutes_before=minutes;prefs.message_format=fmt;prefs.version+=1;db.commit()
    except (HTTPException,SQLAlchemyError) as exc:
        db.rollback();return page(request,db,user,values,exc.detail if isinstance(exc,HTTPException) else 'Не удалось сохранить. Поля остались ниже.',exc.status_code if isinstance(exc,HTTPException) else 503)
    return RedirectResponse('/admin/reminders?notice=saved',status_code=303)
