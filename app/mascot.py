"""Best-effort private decoration. Never participates in homework transactions."""
import json
import logging
import random
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload
from .config import ROOT
from .models import AssignmentTarget, Lesson, MascotPreference, User, utc_now
from .logic import status

log = logging.getLogger(__name__)
CATALOG = ROOT / 'app/private_assets/dialogues.json'
DISPLAY_SECONDS = 25
RECENT_SECONDS = 900
INTERVALS = {'rare': 1800, 'normal': 300, 'frequent': 120}
TONES = {'soft': {'soft'}, 'medium': {'soft', 'medium'}, 'spicy': {'soft', 'medium', 'spicy'}}
EVENTS = {'session.admin_login', 'student.created', 'student.paused', 'student.archived',
          'student.restored', 'assignment.created', 'assignment.updated', 'assignment.deadline_changed',
          'submission.reviewed', 'submission.revision_requested', 'lesson.created', 'settings.saved'}
ASSETS = {'neutral','coffee','typing','side_eye','smug','sleepy','soft_attached','empty_reviews',
          'pending_reviews','archive','returned','success','error','lesson','on_pillow','rain_window',
          'queen_mode','how_you_wish','overworked','waiting','annoyed'}
ASSET_URLS = {state: '/admin/cat/placeholder.svg' for state in ASSETS}
# Editorial decisions and unsupported facts remain disabled even if a raw catalog is copied over.
HELD = {'cat_007','cat_025','cat_052','cat_082','cat_099','cat_112','cat_123'}


def load_catalog():
    catalog = json.loads(CATALOG.read_text(encoding='utf-8-sig'))
    if catalog.get('audience') != 'admin_only' or catalog.get('voice') != 'female':
        raise ValueError('Invalid catalog audience')
    entries, ids = catalog['entries'], set()
    for e in entries:
        if (e['id'] in ids or e['mode'] not in {'normal','rare_care','easter_egg'}
                or e['tone'] not in {'soft','medium','spicy','care'} or e['asset'] not in ASSETS
                or type(e['weight']) not in (int,float) or not 0 < e['weight'] <= 100
                or type(e['enabled']) is not bool or not isinstance(e['text'],str)
                or len(e['text']) > 1000 or not isinstance(e['placements'],list)
                or (e['mode']=='rare_care') != (e['tone']=='care')):
            raise ValueError('Invalid catalog entry')
        ids.add(e['id'])
    return entries


def preference(db, user_id):
    row = db.get(MascotPreference, user_id)
    if row is None:
        row = MascotPreference(user_id=user_id)
        db.add(row)
        db.flush()
    return row


def save_state(db, row, state, **settings):
    """Compare-and-swap: at most one concurrent tab may claim a message."""
    version = row.version
    result = db.execute(update(MascotPreference).where(
        MascotPreference.user_id == row.user_id, MascotPreference.version == version
    ).values(state_json=json.dumps(state), version=version+1, **settings))
    if result.rowcount != 1:
        db.rollback()
        return False
    db.commit()
    return True


def record_event(request, user, trigger, **facts):
    """Call AFTER business commit. Failure is deliberately isolated and contains no private log text."""
    if user.role != 'admin' or user.must_change_password or trigger not in EVENTS:
        return
    try:
        with request.app.state.sessionmaker() as db:
            row = preference(db, user.id)
            state = json.loads(row.state_json)
            if not row.enabled:
                state.pop('event', None)
            else:
                # Store only bounded booleans, never names/comments/assignment content.
                state['event'] = {'trigger': trigger, 'at': utc_now().isoformat(),
                                  'facts': {k: bool(v) for k,v in facts.items()
                                            if k in {'has_files','has_comment','returned_after_absence'}}}
            save_state(db, row, state)
    except Exception:
        log.warning('Mascot event unavailable')


def night_key(local):
    minute = local.hour*60+local.minute
    if minute >= 23*60+30:
        return local.date().isoformat()
    if minute < 5*60:
        return (local.date()-timedelta(days=1)).isoformat()
    return None


def elapsed(state, key, now, seconds):
    return not state.get(key) or (now-datetime.fromisoformat(state[key])).total_seconds() >= seconds


def eligible(e, triggers, facts, row, state, now, local):
    if not e['enabled'] or e['id'] in HELD or e['id'] in state.get('recent', []):
        return False
    if e['trigger'] not in triggers or not set(e['placements']) & {'admin_cat_widget','admin_success_secondary'}:
        return False
    if e['id']=='cat_054' and not facts.get('has_files'):
        return False
    if e['id']=='cat_019' and not facts.get('no_submissions'):
        return False
    if e['id'] in {'cat_069','cat_075'} and not facts.get('has_comment'):
        return False
    if e['mode']=='normal':
        return e['tone'] in TONES[row.tone]
    if e['mode']=='easter_egg':
        return (row.easter_eggs and row.tone=='spicy' and state.get('count',0)>=20
                and elapsed(state,'egg_at',now,7*86400))
    if not row.rare_care or not elapsed(state,'care_at',now,86400):
        return False
    if e['trigger']=='session.late_night':
        return bool(night_key(local) and state.get('night') != night_key(local))
    if e['trigger']=='rare.care_unexpected':
        previous = state.get('compliment_date')
        return (state.get('count',0)>=10 and (not previous or
                (local.date()-datetime.fromisoformat(previous).date()).days>=7))
    return True


def select_message(db, row, path, tz_name, now, entries):
    from .cat_assets import choose_widget_asset, asset_path, APPROVED_WIDGET_ASSETS
    state = json.loads(row.state_json)
    recent = state.get('recent', [])
    recent_at = state.get('recent_at', {})
    # A small approved catalogue must not become permanently exhausted.
    # Keep the last quote excluded, even after the 15-minute repetition window.
    state['recent'] = [ident for ident in recent if ident == (recent[-1] if recent else None)
        or not elapsed({'at':recent_at.get(ident,state.get('shown_at'))}, 'at', now, RECENT_SECONDS)]
    event = state.get('event')  # Keep fresh confirmed events while briefly throttled.
    view = {'enabled': row.enabled, 'animations': row.animations, 'quote': None,
            'asset_url': ASSET_URLS['neutral']}
    if not row.enabled:
        state.pop('event', None)
        state.pop('visible', None)
        save_state(db,row,state)
        return view
    triggers, facts = [], {}
    local = now.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(tz_name))
    event_prefixes = {'student':'/admin/students','assignment':'/admin/assignments',
                      'submission':'/admin/assignments','lesson':'/admin/lessons','session':'/admin'}
    event_matches = event and (path.startswith(event_prefixes.get(event['trigger'].split('.')[0],'/admin/cat'))
                              or (path == '/admin/reviews' and event['trigger'].startswith('submission.')))
    fresh_event = event_matches and 0 <= (now-datetime.fromisoformat(event['at'])).total_seconds() < 90
    if event and (now-datetime.fromisoformat(event['at'])).total_seconds() >= 90:
        state.pop('event', None)
    if fresh_event:
        triggers.append(event['trigger'])
        facts = event.get('facts',{})
        if event['trigger']=='session.admin_login' and facts.get('returned_after_absence'):
            triggers.insert(0,'session.return_after_absence')
    new_pending = False
    if path in {'/admin', '/admin/reviews'}:
        targets = list(db.scalars(select(AssignmentTarget).options(
            selectinload(AssignmentTarget.assignment), selectinload(AssignmentTarget.submissions))))
        pending = sum(status(t,now)=='pending' for t in targets)
        latest_pending = max((s.id for t in targets if status(t,now)=='pending' for s in t.submissions), default=0)
        new_pending = latest_pending > state.get('pending_seen',0)
        # State triggers are facts, not notices or arbitrary client input.
        facts['no_submissions'] = not any(t.submissions for t in targets)
        state_trigger = 'reviews.many' if pending>=8 else ('reviews.some' if pending else 'reviews.none')
        if path == '/admin' and night_key(local):
            triggers.append('session.late_night')
        if path == '/admin' and pending>=8:
            triggers.append('dashboard.heavy_load')
        if path == '/admin':
            triggers += ['rare.care_unexpected','rare.inside_joke',state_trigger,'dashboard.opened']
        elif pending == 0:
            triggers.append('reviews.none')
    elif path == '/admin/lessons':
        today = local.date()
        upcoming = db.scalar(select(Lesson.id).where(Lesson.state=='scheduled',Lesson.starts_at>=now,Lesson.starts_at<=now+timedelta(minutes=30)).limit(1))
        today_start = datetime.combine(today,time.min,tzinfo=local.tzinfo).astimezone(timezone.utc).replace(tzinfo=None)
        tomorrow_start = datetime.combine(today+timedelta(days=1),time.min,tzinfo=local.tzinfo).astimezone(timezone.utc).replace(tzinfo=None)
        has_today = db.scalar(select(Lesson.id).where(Lesson.state=='scheduled',Lesson.starts_at>=today_start,Lesson.starts_at<tomorrow_start).limit(1))
        if upcoming:
            triggers.append('lesson.upcoming')
        elif not has_today:
            triggers.append('lesson.none_today')    # A bounded display lease survives navigation, without advancing repetition counters.
    visible = state.get('visible')
    if visible:
        age = (now-datetime.fromisoformat(visible['at'])).total_seconds()
        cached = next((e for e in entries if e['id']==visible['id'] and e['enabled'] and e['id'] not in HELD), None)
        # State claims must remain true; committed events remain facts for this short lease.
        if (cached and 0 <= age < DISPLAY_SECONDS and (cached['trigger'] in EVENTS or cached['trigger'] in triggers)
                and (cached['id']!='cat_019' or facts.get('no_submissions'))):
            key=visible.get('asset_id')
            if key not in APPROVED_WIDGET_ASSETS or not asset_path(key):
                key=choose_widget_asset(cached['asset'],cached['mode'],state.get('last_asset'))
                state['visible']['asset_id']=key
                state['last_asset']=key
                if not save_state(db,row,state):return view
            view.update(quote=cached, asset_id=key, visible_ms=int((DISPLAY_SECONDS-age)*1000))
            return view
    priority = bool(fresh_event or new_pending)
    first_greeting = fresh_event and event['trigger']=='session.admin_login' and not state.get('greeted_at')
    interval = 30 if first_greeting else (60 if priority and row.frequency!='rare' else INTERVALS[row.frequency])
    if not elapsed(state,'shown_at',now,interval):
        return view
    if new_pending and not fresh_event:
        triggers.insert(0,state_trigger)
    selected = None
    for trigger in dict.fromkeys(triggers):
        pool = [e for e in entries if eligible(e,{trigger},facts,row,state,now,local)]
        if pool:
            selected = random.choices(pool,weights=[e['weight'] for e in pool],k=1)[0]
            break
    if selected:
        state.pop('event', None)
        key=choose_widget_asset(selected['asset'],selected['mode'],state.get('last_asset'))
        state['visible'] = {'id':selected['id'],'at':now.isoformat(),'asset_id':key}
        state['last_asset']=key
        view['asset_id']=key
        view['visible_ms'] = DISPLAY_SECONDS*1000
        if selected['trigger']=='session.admin_login':
            state['greeted_at'] = now.isoformat()
        if selected['trigger'] in {'reviews.some','reviews.many'}:
            state['pending_seen'] = latest_pending
        state['shown_at'] = now.isoformat()
        state['recent'] = (state.get('recent',[])+[selected['id']])[-8:]
        state['recent_at'] = {ident: (now.isoformat() if ident==selected['id'] else recent_at.get(ident,state.get('shown_at'))) for ident in state['recent']}
        state['count'] = state.get('count',0)+1
        if selected['mode']=='rare_care':
            state['care_at'] = now.isoformat()
            if selected['trigger']=='session.late_night':
                state['night'] = night_key(local)
            if selected['trigger']=='rare.care_unexpected':
                state['compliment_date'] = local.date().isoformat()
        elif selected['mode']=='easter_egg':
            state['egg_at'] = now.isoformat()
    if selected or event:
        if save_state(db,row,state) and selected:
            view['quote'] = selected
            view['asset_url'] = ASSET_URLS.get(selected['asset'], ASSET_URLS['neutral'])
    else:
        db.commit()  # persist initial preferences, if any
    return view


def view_for(request, user):
    from .cat_assets import APPROVED_WIDGET_ASSETS, widget_asset_key
    if not APPROVED_WIDGET_ASSETS:
        return None
    if not user or user.role != 'admin' or user.must_change_password:
        return None
    # No decorative content on settings, password/security or error pages.
    if not request.url.path.startswith('/admin') or request.url.path.startswith('/admin/cat'):
        return None
    if request.query_params.get('notice') in {'password_reset','password_changed','deleted'}:
        return None
    try:
        entries = load_catalog()
        with request.app.state.sessionmaker() as db:
            fresh = db.get(User,user.id)
            if not fresh or not fresh.active or fresh.role!='admin' or fresh.must_change_password:
                return None
            allowed = [e for e in entries if widget_asset_key(e['asset'],e['mode'])]
            view = select_message(db,preference(db,user.id),request.url.path,
                                  request.app.state.settings.timezone,utc_now(),allowed)
            if not view['quote'] or not view.get('asset_id'):
                return None
            view['asset_url'] = '/admin/cat/widget/' + view['asset_id']
            return view
    except FileNotFoundError:
        log.warning('Mascot display unavailable: required_private_file_missing')
        return None
    except (ValueError, KeyError, TypeError):
        log.warning('Mascot display unavailable: invalid_catalog_or_state')
        return None
    except Exception:
        log.warning('Mascot display unavailable: storage_or_runtime_error')
        return None
