"""Transactional topic operations. Callers own commit/rollback."""
import json
import re
import unicodedata
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import select, update, delete, func
from sqlalchemy.orm import joinedload
from .models import Topic, StudentTopic, TopicEvent, LessonTopic, LessonRecord, utc_now
from .web import clean_text, parse_id

STATUSES = {'unmarked':'Без отметки','studying':'Изучаем','practicing':'Практикуем','repeat':'Повторить','confident':'Уверенно'}
EVENTS = {'created':'Тема создана в каталоге','renamed':'Название или категория изменены',
          'assigned':'Тема добавлена ученику','updated':'Статус или примечание изменены',
          'linked':'Тема рассмотрена на занятии','unlinked':'Связь с занятием снята',
          'lesson_updated':'Запись занятия исправлена','lesson_deleted':'Запись занятия удалена'}


def normalized(text):
    return ' '.join(unicodedata.normalize('NFC',text).split()).casefold()


def creation_key(raw):
    if not re.fullmatch('[a-f0-9]{32}',str(raw or '')):
        raise HTTPException(400,'Неверный ключ формы. Откройте новую форму; введённый текст сохранён ниже.')
    return raw


def pedagogical_status(raw):
    if raw not in STATUSES:
        raise HTTPException(400,'Выберите допустимую педагогическую отметку')
    return raw


def event(db,topic,actor,kind,progress=None,lesson=None,before=None,after=None):
    db.add(TopicEvent(topic_id=topic.id,student_topic_id=progress.id if progress else None,
        lesson_record_id=lesson.id if lesson else None,actor_id=actor,kind=kind,title_snapshot=topic.title,
        before_json=json.dumps(before or {},ensure_ascii=False),after_json=json.dumps(after or {},ensure_ascii=False)))


def get_or_create_topic(db,title,category,actor,key=None):
    title=' '.join(clean_text(title,180,'Название темы').split())
    category=' '.join(clean_text(category,80,'Категория',False).split())
    existing=db.scalar(select(Topic).where(Topic.normalized_title==normalized(title),Topic.normalized_category==normalized(category)))
    if existing:
        return existing
    topic=Topic(title=title,category=category,normalized_title=normalized(title),normalized_category=normalized(category),creation_key=key or uuid4().hex)
    db.add(topic);db.flush()
    event(db,topic,actor,'created',after={'title':title,'category':category})
    return topic


def assign(db,student_id,topic,actor,status='unmarked',note='',key=None):
    row=db.scalar(select(StudentTopic).where(StudentTopic.student_id==student_id,StudentTopic.topic_id==topic.id))
    if row:
        return row
    row=StudentTopic(student_id=student_id,topic_id=topic.id,status=pedagogical_status(status),
                     note=clean_text(note,4000,'Примечание',False),creation_key=key or uuid4().hex)
    db.add(row);db.flush()
    event(db,topic,actor,'assigned',progress=row,after={'status':row.status,'note':row.note})
    return row


def owned_progress(db,student_id,progress_id):
    row=db.scalar(select(StudentTopic).options(joinedload(StudentTopic.topic)).where(StudentTopic.id==progress_id,StudentTopic.student_id==student_id))
    if not row:
        raise HTTPException(404,'Тема ученика не найдена')
    return row


def change_progress(db,row,version,status,note,actor):
    status=pedagogical_status(status)
    note=clean_text(note,4000,'Примечание',False)
    before={'status':row.status,'note':row.note}
    if version!=row.version:
        raise HTTPException(409,'Отметка изменена в другой вкладке. Ваш текст сохранён ниже; откройте актуальную версию.')
    data={'status':status,'note':note,'version':StudentTopic.version+1,'updated_at':utc_now()}
    if status!=row.status:
        data['status_changed_at']=utc_now()
    result=db.execute(update(StudentTopic).where(StudentTopic.id==row.id,StudentTopic.student_id==row.student_id,StudentTopic.version==version).values(**data),execution_options={'synchronize_session':False})
    if result.rowcount!=1:
        raise HTTPException(409,'Отметка изменена в другой вкладке. Сравните сохранённую версию с вашим текстом.')
    if before!={'status':status,'note':note}:
        event(db,row.topic,actor,'updated',progress=row,before=before,after={'status':status,'note':note})


def lesson_choices(db,student_id):
    return list(db.execute(select(StudentTopic.id,Topic.title,Topic.category).join(Topic).where(StudentTopic.student_id==student_id).order_by(Topic.normalized_title,Topic.id)))


def lesson_ids(db,lesson_id):
    return [str(x) for x in db.scalars(select(LessonTopic.student_topic_id).where(LessonTopic.lesson_record_id==lesson_id))]


def sync_lesson(db,lesson,raw_ids,actor,new_title='',new_category='',old_values=None):
    ids=set()
    if len(raw_ids)>50:
        raise HTTPException(400,'Для одного урока выберите не более 50 тем')
    for value in raw_ids:
        ident=parse_id(value,'темы')
        if not ident:
            raise HTTPException(400,'Некорректная тема')
        ids.add(ident)
    rows=list(db.scalars(select(StudentTopic).options(joinedload(StudentTopic.topic)).where(StudentTopic.id.in_(ids),StudentTopic.student_id==lesson.student_id))) if ids else []
    if len(rows)!=len(ids):
        raise HTTPException(400,'Можно выбрать только темы этого ученика')
    if new_title.strip():
        topic=get_or_create_topic(db,new_title,new_category,actor)
        row=assign(db,lesson.student_id,topic,actor)
        if row.id not in ids:
            rows.append(row);ids.add(row.id)
    elif new_category.strip():
        raise HTTPException(400,'Для новой темы укажите название')
    previous=set(int(x) for x in lesson_ids(db,lesson.id))
    all_rows={r.id:r for r in rows}
    for ident in previous-ids:
        row=owned_progress(db,lesson.student_id,ident)
        event(db,row.topic,actor,'unlinked',row,lesson,before=old_values or {'lesson_date':str(lesson.lesson_date),'summary':lesson.topic})
        db.execute(delete(LessonTopic).where(LessonTopic.lesson_record_id==lesson.id,LessonTopic.student_topic_id==ident))
    for ident in ids-previous:
        row=all_rows[ident]
        db.add(LessonTopic(lesson_record_id=lesson.id,student_topic_id=ident,student_id=lesson.student_id))
        event(db,row.topic,actor,'linked',row,lesson,after={'lesson_date':str(lesson.lesson_date),'summary':lesson.topic})
    now_values={'lesson_date':str(lesson.lesson_date),'summary':lesson.topic}
    if old_values and old_values!=now_values:
        for ident in ids & previous:
            row=all_rows[ident]
            event(db,row.topic,actor,'lesson_updated',row,lesson,before=old_values,after=now_values)


def unlink_deleted_lesson(db,lesson,actor):
    for ident in lesson_ids(db,lesson.id):
        row=owned_progress(db,lesson.student_id,int(ident))
        event(db,row.topic,actor,'lesson_deleted',row,lesson,before={'lesson_date':str(lesson.lesson_date),'summary':lesson.topic})
    db.execute(delete(LessonTopic).where(LessonTopic.lesson_record_id==lesson.id))


def summary(db,student_id):
    base=select(StudentTopic).options(joinedload(StudentTopic.topic)).where(StudentTopic.student_id==student_id)
    return {'repeat_topics':list(db.scalars(base.where(StudentTopic.status=='repeat').order_by(StudentTopic.updated_at.desc(),StudentTopic.id.desc()).limit(5))),
            'studying_topics':list(db.scalars(base.where(StudentTopic.status.in_(['studying','practicing'])).order_by(StudentTopic.updated_at.desc(),StudentTopic.id.desc()).limit(5))),
            'repeat_count':db.scalar(select(func.count()).select_from(StudentTopic).where(StudentTopic.student_id==student_id,StudentTopic.status=='repeat')),
            'topic_statuses':STATUSES}
