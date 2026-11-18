"""Explicit bot binding and private-chat verification; no token or incoming text logging."""
from dataclasses import dataclass, field
from datetime import timedelta
from hashlib import sha256
import os, re, secrets, json
from urllib.request import Request, urlopen
from sqlalchemy import select, func
from fastapi import HTTPException
from .models import User, utc_now
from .personal_models import PersonalRecipient, TelegramLink, TelegramCursor
from .scheduling import lock

@dataclass(frozen=True)
class PersonalConfig:
    token: str = field(default='',repr=False)
    bot_id: str = ''
    environment: str = 'production'
    enabled: bool = False
    allowed_chat_ids: tuple[str,...] = field(default=(),repr=False)
    @property
    def configured(self):
        return bool(re.fullmatch(r'[1-9][0-9]*:[A-Za-z0-9_-]+',self.token) and self.bot_id==self.token.split(':')[0] and self.environment in ('test','production'))
    def allows(self,chat_id):
        return bool(re.fullmatch(r'[1-9][0-9]*',str(chat_id)) and (self.environment=='production' or str(chat_id) in self.allowed_chat_ids))
    @classmethod
    def from_env(cls):
        return cls(os.getenv('PERSONAL_TELEGRAM_BOT_TOKEN',''),os.getenv('PERSONAL_TELEGRAM_BOT_ID',''),
                   'production',os.getenv('PERSONAL_TELEGRAM_ENABLED','false').lower()=='true')


def api(config,method,payload):
    if method not in ('getMe','getUpdates') or not config.configured: raise RuntimeError('Telegram configuration unavailable')
    request=Request('https://api.telegram.org/bot'+config.token+'/'+method,data=json.dumps(payload).encode(),
                    headers={'Content-Type':'application/json'},method='POST')
    try:
        with urlopen(request,timeout=8) as response: data=json.loads(response.read(1048576))
        if data.get('ok') is True: return data['result']
    except Exception: pass
    # Never propagate urllib errors: their URL contains the token.
    raise RuntimeError('Telegram API unavailable')


def new_link(db,user,config,now=None):
    now=now or utc_now(); lock(db)
    if user.role not in ('admin','sender') or not config.configured: raise HTTPException(400,'Тестовый или рабочий бот ещё не настроен')
    count=db.scalar(select(func.count()).select_from(TelegramLink).where(TelegramLink.user_id==user.id,TelegramLink.created_at>now-timedelta(minutes=15)))
    if count>=3: raise HTTPException(429,'Подождите 15 минут перед новым кодом')
    for old in db.scalars(select(TelegramLink).where(TelegramLink.user_id==user.id,TelegramLink.consumed==False)): old.consumed=True
    token=secrets.token_urlsafe(32)
    db.add(TelegramLink(digest=sha256(token.encode()).hexdigest(),user_id=user.id,environment=config.environment,bot_id=config.bot_id,
                        created_at=now,expires_at=now+timedelta(minutes=15)))
    return token


def process_updates(db,config,updates,now=None):
    from .personal import disable
    now=now or utc_now(); lock(db)
    scope=config.environment+':'+config.bot_id
    cursor=db.get(TelegramCursor,scope)
    if not cursor: cursor=TelegramCursor(scope=scope,update_id=-1); db.add(cursor)
    for update in sorted(updates,key=lambda u:u.get('update_id',-1)):
        uid=update.get('update_id')
        if not isinstance(uid,int) or uid<=cursor.update_id: continue
        cursor.update_id=uid
        msg=update.get('message',{}); chat=msg.get('chat',{}); source=msg.get('from',{})
        cid=str(chat.get('id',''))
        if chat.get('type')!='private' or str(source.get('id'))!=cid or source.get('is_bot') or not config.allows(cid): continue
        text=msg.get('text','').strip()
        if text=='/stop_personal':
            prefs=db.scalar(select(PersonalRecipient).where(PersonalRecipient.chat_id==cid,PersonalRecipient.bot_id==config.bot_id,PersonalRecipient.environment==config.environment))
            if prefs: disable(db,prefs,now)
            continue
        match=re.fullmatch(r'/start ([A-Za-z0-9_-]{43})',text)
        if not match: continue
        link=db.get(TelegramLink,sha256(match[1].encode()).hexdigest())
        if not link or link.consumed or link.expires_at<=now or link.environment!=config.environment or link.bot_id!=config.bot_id: continue
        user=db.get(User,link.user_id)
        if not user or not user.active or user.must_change_password or user.role not in ('admin','sender'): continue
        duplicate=db.scalar(select(PersonalRecipient).where(PersonalRecipient.chat_id==cid,PersonalRecipient.bot_id==config.bot_id,PersonalRecipient.environment==config.environment,PersonalRecipient.user_id!=user.id))
        if duplicate: continue
        prefs=db.get(PersonalRecipient,user.id)
        if prefs: disable(db,prefs,now)
        else: prefs=PersonalRecipient(user_id=user.id,version=1); db.add(prefs)
        prefs.environment=config.environment; prefs.bot_id=config.bot_id; prefs.chat_id=cid
        prefs.verified_at=now; prefs.enabled=False; prefs.consented_at=None; link.consumed=True
    db.flush()


def poll(factory,config,caller=api):
    me=caller(config,'getMe',{})
    if str(me.get('id'))!=config.bot_id or not me.get('is_bot'): raise RuntimeError('Unexpected bot identity')
    with factory() as db:
        cursor=db.get(TelegramCursor,config.environment+':'+config.bot_id)
        offset=cursor.update_id+1 if cursor else 0
    updates=caller(config,'getUpdates',{'offset':offset,'limit':100,'timeout':0,'allowed_updates':['message']})
    with factory() as db:
        process_updates(db,config,updates); db.commit()
