"""Private one-off reminders. Every mutation participates in the shared DB write lock."""
import json
from datetime import timedelta
from sqlalchemy import select, func
from fastapi import HTTPException
from .models import User, utc_now
from .personal_models import PersonalRecipient, PersonalGrant, PersonalReminder, PersonalDelivery, PersonalEvent
from .scheduling import lock as database_lock, local_to_utc
from .web import clean_text, parse_id
from .progress import creation_key
from .reminders import TelegramConfig, telegram_send
from .outbox import dispatch

LABELS={'pending':'Ожидает отправки','sent':'Принято Telegram API (прочтение неизвестно)','unknown':'Результат неизвестен — автоматического повтора нет',
        'cancelled':'Отменено','obsolete':'Снято с отправки','failed':'Ошибка доставки','missed':'Пропущено: время прошло'}

def lock(db):
    database_lock(db)
    # Routes may have loaded an object before acquiring the write lock.
    db.expire_all()

def eligible(user): return bool(user and user.active and not user.must_change_password and user.role in ('admin','sender'))

def event(db,row,kind,actor=None):
    db.add(PersonalEvent(reminder_id=row.id,actor_id=actor,kind=kind,snapshot_json=json.dumps(
        {'version':row.version,'text':row.body,'due_at':row.due_at.isoformat(),'timezone':row.timezone_name,'state':row.state},ensure_ascii=False)))

def invalidate(db,recipient_id,sender_id=None,now=None):
    query=select(PersonalReminder).where(PersonalReminder.recipient_id==recipient_id,PersonalReminder.state=='pending')
    if sender_id is not None: query=query.where(PersonalReminder.sender_id==sender_id)
    for row in db.scalars(query):
        row.state='cancelled'; row.version+=1; row.updated_at=now or utc_now()
        for d in db.scalars(select(PersonalDelivery).where(PersonalDelivery.reminder_id==row.id,PersonalDelivery.state=='pending')): d.state='obsolete'
        event(db,row,'permission_revoked',recipient_id)

def disable(db,prefs,now=None):
    prefs.enabled=False; prefs.version+=1; prefs.consented_at=None
    invalidate(db,prefs.user_id,now=now)

def consent(db,user,form,config,now=None):
    now=now or utc_now(); lock(db); prefs=db.get(PersonalRecipient,user.id)
    if not prefs or prefs.environment!=config.environment or prefs.bot_id!=config.bot_id: raise HTTPException(400,'Сначала подтвердите подключение через /start')
    if str(form.get('version'))!=str(prefs.version): raise HTTPException(409,'Настройки изменены. Обновите страницу.')
    ids={parse_id(x,'отправителя') for x in form.getlist('sender_ids')}
    allowed=set(db.scalars(select(User.id).where(User.role.in_(['admin','sender']),User.active==True,User.id!=user.id)))
    if not ids<=allowed: raise HTTPException(400,'Недоступный отправитель')
    # Any consent change invalidates the old queue, even if access is later restored.
    disable(db,prefs,now)
    prefs.enabled=form.get('enabled')=='yes'; prefs.consented_at=now if prefs.enabled else None
    for sid in allowed | set(db.scalars(select(PersonalGrant.sender_id).where(PersonalGrant.recipient_id==user.id))):
        grant=db.get(PersonalGrant,(user.id,sid))
        if not grant: grant=PersonalGrant(recipient_id=user.id,sender_id=sid,version=0); db.add(grant)
        grant.active=sid in ids; grant.version+=1

def permitted(db,sender_id,recipient_id,config):
    prefs=db.get(PersonalRecipient,recipient_id); grant=db.get(PersonalGrant,(recipient_id,sender_id))
    if not eligible(db.get(User,sender_id)) or not eligible(db.get(User,recipient_id)): return None
    if not prefs or not grant or not grant.active or not prefs.enabled: return None
    if prefs.environment!=config.environment or prefs.bot_id!=config.bot_id or not config.allows(prefs.chat_id): return None
    return prefs,grant

def owned(db,ident,user):
    row=db.get(PersonalReminder,ident)
    if not row or user.id not in (row.sender_id,row.recipient_id): raise HTTPException(404,'Напоминание не найдено')
    return row

def save(db,user,form,config,ident=None,now=None):
    now=now or utc_now(); lock(db)
    if not eligible(user): raise HTTPException(403,'Доступ закрыт')
    row=owned(db,ident,user) if ident else None
    if row:
        if row.sender_id!=user.id: raise HTTPException(403,'Изменять текст может только отправитель')
        if row.state!='pending' or str(form.get('version'))!=str(row.version): raise HTTPException(409,'Сообщение уже обрабатывается или изменено. Обновите страницу.')
        rid=row.recipient_id
    else:
        key=creation_key(form.get('creation_key'))
        old=db.scalar(select(PersonalReminder).where(PersonalReminder.creation_key==key))
        if old:
            if old.sender_id!=user.id: raise HTTPException(409,'Повторите создание из новой формы')
            return old
        rid=parse_id(form.get('recipient_id'),'получателя')
    access=permitted(db,user.id,rid,config)
    if not access: raise HTTPException(400,'Получатель не подключён или не разрешил Вам личные сообщения')
    prefs,grant=access
    body=clean_text(form.get('body'),1000,'Сообщение')
    tz=clean_text(form.get('timezone_name'),60,'Часовой пояс'); due=local_to_utc(form.get('due_at'),tz)
    if not now<due<=now+timedelta(days=366): raise HTTPException(400,'Выберите будущее время в пределах года')
    day=now.replace(hour=0,minute=0,second=0,microsecond=0)
    if not row and db.scalar(select(func.count()).select_from(PersonalReminder).where(PersonalReminder.recipient_id==rid,PersonalReminder.created_at>=day))>=5:
        raise HTTPException(429,'Не больше пяти новых напоминаний получателю за сутки UTC, включая отменённые')
    scheduled_day=due.replace(hour=0,minute=0,second=0,microsecond=0)
    daily=select(func.count()).select_from(PersonalReminder).where(PersonalReminder.recipient_id==rid,
        PersonalReminder.state.in_(['pending','sent','unknown']),PersonalReminder.due_at>=scheduled_day,PersonalReminder.due_at<scheduled_day+timedelta(days=1))
    if row: daily=daily.where(PersonalReminder.id!=row.id)
    if db.scalar(daily)>=5: raise HTTPException(429,'Не больше пяти личных напоминаний получателю на одни сутки UTC')
    neighbors=select(PersonalReminder.id).where(PersonalReminder.recipient_id==rid,PersonalReminder.state.in_(['pending','sent','unknown']),
        PersonalReminder.due_at>due-timedelta(minutes=15),PersonalReminder.due_at<due+timedelta(minutes=15))
    if row: neighbors=neighbors.where(PersonalReminder.id!=row.id)
    if db.scalar(neighbors): raise HTTPException(409,'Между личными напоминаниями должно быть не менее 15 минут')
    kind='edited' if row else 'created'
    if row:
        for d in db.scalars(select(PersonalDelivery).where(PersonalDelivery.reminder_id==row.id,PersonalDelivery.state=='pending')): d.state='obsolete'
        row.version+=1
    else:
        row=PersonalReminder(sender_id=user.id,recipient_id=rid,creation_key=key,version=1,created_at=now); db.add(row)
    row.body=body; row.due_at=due; row.timezone_name=tz; row.updated_at=now; row.state='pending'
    row.chat_id=prefs.chat_id; row.bot_id=prefs.bot_id; row.environment=prefs.environment
    row.recipient_version=prefs.version; row.grant_version=grant.version
    db.flush(); db.add(PersonalDelivery(reminder_id=row.id,reminder_version=row.version,due_at=due)); event(db,row,kind,user.id)
    return row

def cancel(db,user,ident,version):
    lock(db); row=owned(db,ident,user)
    if row.state!='pending' or str(version)!=str(row.version): raise HTTPException(409,'Отмена недоступна: сообщение изменено или уже обрабатывается')
    row.state='cancelled'; row.version+=1; row.updated_at=utc_now()
    for d in db.scalars(select(PersonalDelivery).where(PersonalDelivery.reminder_id==row.id,PersonalDelivery.state=='pending')): d.state='obsolete'
    event(db,row,'cancelled',user.id)

def dispatch_one(factory,config,sender=telegram_send,now=None):
    if not config.enabled or not config.configured: return False
    def valid(db,d,at):
        row=db.get(PersonalReminder,d.reminder_id)
        if not row or row.state not in ('pending','unknown') or row.version!=d.reminder_version: return False
        access=permitted(db,row.sender_id,row.recipient_id,config)
        return bool(access and row.recipient_version==access[0].version and row.grant_version==access[1].version
                    and row.chat_id==access[0].chat_id and row.bot_id==config.bot_id and row.environment==config.environment)
    def recent(db,d,at):
        row=db.get(PersonalReminder,d.reminder_id)
        return db.scalar(select(PersonalDelivery.id).join(PersonalReminder,PersonalReminder.id==PersonalDelivery.reminder_id).where(
            PersonalReminder.recipient_id==row.recipient_id,PersonalDelivery.id!=d.id,PersonalDelivery.state.in_(['sent','unknown']),
            PersonalDelivery.claimed_at>at-timedelta(minutes=15)))
    def changed(db,d,state,at):
        row=db.get(PersonalReminder,d.reminder_id)
        if state=='unknown' and d.claimed_at is None: d.claimed_at=at
        if row.version==d.reminder_version:
            row.state=state; row.updated_at=at; event(db,row,'delivery_'+state)
    def deliver(db,d):
        row=db.get(PersonalReminder,d.reminder_id)
        return sender(TelegramConfig(token=config.token,chat_id=row.chat_id,admin_id=row.recipient_id,enabled=True),row.body)
    scope=select(PersonalReminder.id).where(PersonalReminder.environment==config.environment,PersonalReminder.bot_id==config.bot_id)
    return dispatch(factory,PersonalDelivery,[PersonalDelivery.reminder_id.in_(scope)],valid,deliver,recent=recent,changed=changed,now=now)
