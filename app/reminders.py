"""Teacher-only outbox. Ambiguous network outcomes are never blindly retried."""
from dataclasses import dataclass,field
from datetime import timedelta
import os,json,re
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from sqlalchemy import select
from .models import User,Lesson,ReminderPreference,ReminderDelivery,utc_now
from .scheduling import lock,local

@dataclass(frozen=True)
class TelegramConfig:
    token:str=field(default='',repr=False)
    chat_id:str=field(default='',repr=False)
    admin_id:int=0
    enabled:bool=False
    site_url:str=''

    @classmethod
    def from_env(cls):
        try:owner=int(os.getenv('REMINDER_ADMIN_ID','0'))
        except ValueError:owner=0
        site=os.getenv('TUTOR_PUBLIC_URL','').rstrip('/')
        from urllib.parse import urlsplit
        parsed=urlsplit(site)
        if site and (parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment):site=''
        return cls(os.getenv('TELEGRAM_BOT_TOKEN',''),os.getenv('TELEGRAM_CHAT_ID',''),owner,os.getenv('TELEGRAM_ENABLED','false').lower()=='true',site)

    @property
    def configured(self):
        return bool(re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+',self.token) and re.fullmatch(r'[1-9][0-9]*',self.chat_id) and self.admin_id>0)


def valid(db,delivery,now):
    user=db.get(User,delivery.user_id);lesson=db.get(Lesson,delivery.lesson_id);prefs=db.get(ReminderPreference,delivery.user_id)
    if not user or user.role!='admin' or not user.active or user.must_change_password or not prefs or not prefs.enabled:return False
    if not lesson or lesson.state!='scheduled' or lesson.version!=delivery.lesson_version or prefs.version!=delivery.preference_version or lesson.starts_at<=now:return False
    from .attendance import has_result
    if has_result(db,lesson.id):return False
    pupil=db.get(User,lesson.student_id)
    return bool(pupil and pupil.active and pupil.learning_status=='active')


def enqueue(db,admin_id,now=None):
    now=now or utc_now();lock(db)
    prefs=db.get(ReminderPreference,admin_id);owner=db.get(User,admin_id)
    for item in db.scalars(select(ReminderDelivery).where(ReminderDelivery.state=='pending')):
        if not valid(db,item,now):item.state='obsolete'
    if not owner or owner.role!='admin' or not owner.active or not prefs or not prefs.enabled:return
    for lesson in db.scalars(select(Lesson).where(Lesson.state=='scheduled',Lesson.starts_at>now,Lesson.starts_at<=now+timedelta(days=84))):
        from .attendance import has_result
        if has_result(db,lesson.id):continue
        old=db.scalar(select(ReminderDelivery).where(ReminderDelivery.lesson_id==lesson.id,ReminderDelivery.user_id==admin_id,
            ReminderDelivery.lesson_version==lesson.version,ReminderDelivery.preference_version==prefs.version))
        sent=db.scalar(select(ReminderDelivery.id).where(ReminderDelivery.lesson_id==lesson.id,ReminderDelivery.user_id==admin_id,
            ReminderDelivery.lesson_version==lesson.version,ReminderDelivery.state.in_(['sent','unknown'])))
        if not old and not sent:
            db.add(ReminderDelivery(lesson_id=lesson.id,user_id=admin_id,lesson_version=lesson.version,preference_version=prefs.version,due_at=lesson.starts_at-timedelta(minutes=prefs.minutes_before)))


def message(db,delivery,config,default_zone):
    lesson=db.get(Lesson,delivery.lesson_id);pupil=db.get(User,lesson.student_id);prefs=db.get(ReminderPreference,delivery.user_id)
    tz=lesson.timezone_name or default_zone
    value=f'Tutor: {local(lesson.starts_at,tz).strftime("%d.%m %H:%M")} ({tz}) — {pupil.display_name}'
    if prefs.message_format=='links':
        if lesson.meeting_url:value+='\nСозвон: '+lesson.meeting_url
        if config.site_url:value+=f'\nПодготовка: {config.site_url}/admin/students/{pupil.id}'
    return value[:4000]


def telegram_send(config,text):
    """Return only non-sensitive result codes. No exception URL/token is logged."""
    payload=json.dumps({'chat_id':config.chat_id,'text':text,'link_preview_options':{'is_disabled':True}}).encode()
    request=Request('https://api.telegram.org/bot'+config.token+'/sendMessage',data=payload,headers={'Content-Type':'application/json'},method='POST')
    try:
        with urlopen(request,timeout=8) as response:data=json.loads(response.read(65536))
    except HTTPError as exc:
        if exc.code==429:
            try:data=json.loads(exc.read(65536))
            except Exception:return 'failed',None
        elif 400<=exc.code<500:return 'failed',None
        else:return 'unknown',None
    except Exception:return 'unknown',None
    if data.get('ok') is True:return 'sent',str(data.get('result',{}).get('message_id',''))
    if data.get('error_code')==429:
        try:return 'retry',min(3600,max(1,int(data.get('parameters',{}).get('retry_after',60))))
        except (TypeError,ValueError):return 'failed',None
    return ('failed' if data.get('error_code',500)<500 else 'unknown'),None


def dispatch_one(factory,config,default_zone,sender=telegram_send,now=None):
    if not config.enabled or not config.configured:return False
    from .outbox import dispatch
    def recent(db,item,at):
        return db.scalar(select(ReminderDelivery.id).where(ReminderDelivery.lesson_id==item.lesson_id,
            ReminderDelivery.user_id==item.user_id,ReminderDelivery.state=='sent',
            ReminderDelivery.sent_at>at-timedelta(minutes=10)))
    return dispatch(factory,ReminderDelivery,[ReminderDelivery.user_id==config.admin_id],valid,
        lambda db,item: sender(config,message(db,item,config,default_zone)),recent=recent,now=now)
