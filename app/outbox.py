"""Shared durable delivery protocol. Never automatically retry an uncertain send."""
from datetime import timedelta
from sqlalchemy import select
from .models import utc_now
from .scheduling import lock

def dispatch(factory, model, filters, valid, deliver, *, recent=None, changed=None, now=None):
    now = now or utc_now()
    def state(db, item, value):
        item.state = value
        if changed: changed(db, item, value, now)
    with factory() as db:
        lock(db)
        item = db.scalar(select(model).where(*filters, model.state=='pending', model.due_at<=now).order_by(model.due_at, model.id).limit(1))
        if not item:
            db.commit(); return False
        if not valid(db,item,now):
            state(db,item,'obsolete'); db.commit(); return True
        if item.due_at < now-timedelta(minutes=5):
            state(db,item,'missed'); db.commit(); return True
        if recent and recent(db,item,now):
            state(db,item,'obsolete'); db.commit(); return True
        ident=item.id
        state(db,item,'unknown'); item.attempts+=1; db.commit()
    with factory() as db:
        lock(db); item=db.get(model,ident)
        if not valid(db,item,now):
            state(db,item,'obsolete'); db.commit(); return True
        # The same database lock serializes revocation/cancellation and network IO.
        try: result,value=deliver(db,item)
        except Exception: result,value='unknown',None
        if result=='sent':
            item.message_id=value; item.sent_at=now; state(db,item,'sent')
        elif result=='retry' and item.attempts<3:
            try: delay=min(3600,max(1,int(value)))
            except (TypeError,ValueError): state(db,item,'failed')
            else:
                item.due_at=now+timedelta(seconds=delay); state(db,item,'pending')
        else: state(db,item,result if result in ('failed','unknown') else 'failed')
        db.commit()
    return True
