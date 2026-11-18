from uuid import uuid4
import re
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select, or_, func
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from ..web import db_session, require_user, require_admin, render, validated_form, clean_text
from ..models import User
from ..personal_models import PersonalRecipient, PersonalGrant, PersonalReminder, PersonalDelivery, PersonalEvent
from ..personal_telegram import PersonalConfig, new_link
from .. import personal as service
from ..scheduling import local, lock
from ..security import password_hash

router=APIRouter()

def member(user:User=Depends(require_user)):
    if user.role not in ('admin','sender'): raise HTTPException(403,'Личные напоминания недоступны ученикам')
    if user.must_change_password: raise HTTPException(423,'Сначала смените временный пароль')
    return user

def config(request): return getattr(request.app.state,'personal_config',None) or PersonalConfig.from_env()

def back(path='/personal'): return RedirectResponse(path+'?notice=saved',status_code=303)

def recipients(db,user,cfg):
    return [u for u in db.scalars(select(User).where(User.role.in_(['admin','sender']),User.id!=user.id).order_by(User.display_name)) if service.permitted(db,user.id,u.id,cfg)]

@router.get('/personal')
def home(request:Request,db:Session=Depends(db_session),user:User=Depends(member)):
    try: page=max(1,int(request.query_params.get('page','1')))
    except ValueError: page=1
    q=select(PersonalReminder).where(or_(PersonalReminder.sender_id==user.id,PersonalReminder.recipient_id==user.id)).order_by(PersonalReminder.id.desc())
    rows=list(db.scalars(q.offset((page-1)*30).limit(31)))
    names={u.id:u.display_name for u in db.scalars(select(User).where(User.id.in_({i for r in rows for i in (r.sender_id,r.recipient_id)})))}
    return render(request,'personal/list.html',user=user,rows=rows[:30],names=names,page=page,has_next=len(rows)>30,labels=service.LABELS,personal_time=lambda row:local(row.due_at,row.timezone_name).strftime('%d.%m.%Y %H:%M')+' ('+row.timezone_name+')')

def editor(request,db,user,row=None,values=None,error=None,code=200):
    if values is None:
        values={'creation_key':uuid4().hex,'timezone_name':request.app.state.settings.timezone}
        if row: values.update(body=row.body,recipient_id=str(row.recipient_id),version=row.version,timezone_name=row.timezone_name,due_at=local(row.due_at,row.timezone_name).strftime('%Y-%m-%dT%H:%M'))
    response=render(request,'personal/form.html',user=user,row=row,values=values,form_error=error,recipients=recipients(db,user,config(request)))
    response.status_code=code; return response

@router.get('/personal/new')
def new(request:Request,db:Session=Depends(db_session),user:User=Depends(member)): return editor(request,db,user)

@router.get('/personal/{ident:int}')
def detail(ident:int,request:Request,db:Session=Depends(db_session),user:User=Depends(member)):
    row=service.owned(db,ident,user)
    return render(request,'personal/detail.html',user=user,row=row,labels=service.LABELS,personal_time=lambda row:local(row.due_at,row.timezone_name).strftime('%d.%m.%Y %H:%M')+' ('+row.timezone_name+')',
        deliveries=list(db.scalars(select(PersonalDelivery).where(PersonalDelivery.reminder_id==ident).order_by(PersonalDelivery.id.desc()).limit(30))),
        events=list(db.scalars(select(PersonalEvent).where(PersonalEvent.reminder_id==ident).order_by(PersonalEvent.id.desc()).limit(50))))

@router.get('/personal/{ident:int}/edit')
def edit(ident:int,request:Request,db:Session=Depends(db_session),user:User=Depends(member)):
    row=service.owned(db,ident,user)
    if row.sender_id!=user.id or row.state!='pending': raise HTTPException(409,'Редактирование недоступно')
    return editor(request,db,user,row)

async def persist(request,db,user,ident=None):
    form=await validated_form(request); row=service.owned(db,ident,user) if ident else None
    try:
        saved=service.save(db,user,form,config(request),ident); db.commit(); return back('/personal/'+str(saved.id))
    except (HTTPException,SQLAlchemyError) as exc:
        db.rollback(); return editor(request,db,user,row,dict(form),exc.detail if isinstance(exc,HTTPException) else 'Не удалось сохранить. Введённые поля сохранены в форме.',exc.status_code if isinstance(exc,HTTPException) else 503)

@router.post('/personal/new')
async def create(request:Request,db:Session=Depends(db_session),user:User=Depends(member)): return await persist(request,db,user)

@router.post('/personal/{ident:int}/edit')
async def update(ident:int,request:Request,db:Session=Depends(db_session),user:User=Depends(member)): return await persist(request,db,user,ident)

@router.post('/personal/{ident:int}/cancel')
async def cancel(ident:int,request:Request,db:Session=Depends(db_session),user:User=Depends(member)):
    form=await validated_form(request); service.cancel(db,user,ident,form.get('version')); db.commit(); return back('/personal/'+str(ident))

def connection_page(request,db,user,code=None,error=None,status=200):
    prefs=db.get(PersonalRecipient,user.id)
    senders=list(db.scalars(select(User).where(User.role.in_(['admin','sender']),User.active==True,User.id!=user.id).order_by(User.display_name)))
    grants=set(db.scalars(select(PersonalGrant.sender_id).where(PersonalGrant.recipient_id==user.id,PersonalGrant.active==True)))
    cfg=config(request)
    connected=bool(prefs and prefs.bot_id==cfg.bot_id and prefs.environment==cfg.environment)
    response=render(request,'personal/connection.html',user=user,prefs=prefs,senders=senders,grants=grants,link_code=code,form_error=error,connected=connected,configured=cfg.configured,worker_enabled=cfg.enabled)
    response.status_code=status; return response

@router.get('/personal/connection')
def connection(request:Request,db:Session=Depends(db_session),user:User=Depends(member)): return connection_page(request,db,user)

@router.post('/personal/connection/link')
async def link(request:Request,db:Session=Depends(db_session),user:User=Depends(member)):
    await validated_form(request)
    code=new_link(db,user,config(request)); db.commit(); return connection_page(request,db,user,code=code)

@router.post('/personal/connection/consent')
async def consent(request:Request,db:Session=Depends(db_session),user:User=Depends(member)):
    form=await validated_form(request)
    try: service.consent(db,user,form,config(request)); db.commit()
    except (HTTPException,SQLAlchemyError) as exc:
        db.rollback(); return connection_page(request,db,user,error=exc.detail if isinstance(exc,HTTPException) else 'Сохранение не удалось; разрешения не изменены.',status=exc.status_code if isinstance(exc,HTTPException) else 503)
    return back('/personal/connection')

@router.get('/admin/personal-senders')
def senders(request:Request,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    return render(request,'personal/senders.html',user=user,senders=list(db.scalars(select(User).where(User.role=='sender').order_by(User.id))))

@router.post('/admin/personal-senders')
async def create_sender(request:Request,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    form=await validated_form(request); lock(db)
    name=clean_text(form.get('display_name'),120,'Имя'); username=clean_text(form.get('username'),60,'Логин').lower()
    if not re.fullmatch(r'[a-z0-9_.-]{3,60}',username): raise HTTPException(400,'Логин: 3–60 латинских букв, цифр, точек, дефисов')
    if db.scalar(select(User.id).where(User.username==username)): raise HTTPException(409,'Логин уже занят')
    if db.scalar(select(func.count()).select_from(User).where(User.role=='sender'))>=10: raise HTTPException(400,'Достигнут лимит десяти отправителей')
    try: hashed=password_hash(str(form.get('password','')))
    except ValueError as exc: raise HTTPException(400,str(exc))
    db.add(User(username=username,display_name=name,role='sender',password_hash=hashed,must_change_password=True)); db.commit()
    return back('/admin/personal-senders')

@router.post('/admin/personal-senders/{ident}/access')
async def sender_access(ident:int,request:Request,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    form=await validated_form(request); lock(db); row=db.get(User,ident)
    if not row or row.role!='sender': raise HTTPException(404,'Отправитель не найден')
    if str(form.get('version'))!=str(row.auth_version): raise HTTPException(409,'Аккаунт изменён. Обновите страницу.')
    action=form.get('action')
    if action=='reset':
        try: row.password_hash=password_hash(str(form.get('password','')))
        except ValueError as exc: raise HTTPException(400,str(exc))
        row.must_change_password=True
    elif action in ('disable','enable'): row.active=action=='enable'
    else: raise HTTPException(400,'Неизвестное действие')
    row.auth_version+=1
    # Revocation is permanent for the existing queue; enabling cannot resurrect old sends.
    service.invalidate(db,row.id)
    for recipient_id in db.scalars(select(PersonalGrant.recipient_id).where(PersonalGrant.sender_id==row.id)): service.invalidate(db,recipient_id,row.id)
    db.commit(); return back('/admin/personal-senders')
