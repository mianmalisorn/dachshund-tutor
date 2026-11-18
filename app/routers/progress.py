"""Teacher-only topic catalog and personal progress pages."""
import json
from datetime import time
from uuid import uuid4
from fastapi import APIRouter,Depends,Request,HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select,update,or_,func
from sqlalchemy.orm import Session,joinedload
from sqlalchemy.exc import SQLAlchemyError,IntegrityError
from ..models import Topic,StudentTopic,TopicEvent,LessonRecord,LessonTopic,User,utc_now
from ..web import db_session,require_admin,validated_form,render,clean_text,parse_id
from .. import progress as logic
from .teaching import pupil

router=APIRouter(prefix='/admin')
LIMIT=20


def page_number(request,key='page'):
    page=parse_id(request.query_params.get(key,'1'),'страницы')
    if not page or page>100000:raise HTTPException(400,'Некорректная страница')
    return page


def go(student_id=None):
    return RedirectResponse(f'/admin/students/{student_id}/topics?notice=saved' if student_id else '/admin/topics?notice=saved',status_code=303)


def failed(exc):
    if isinstance(exc,HTTPException):return exc.status_code,exc.detail
    if isinstance(exc,IntegrityError):return 409,'Такая тема уже существует или данные изменились. Ваш текст сохранён ниже; проверьте каталог перед повтором.'
    return 503,'Не удалось сохранить. Ваш текст остался в форме. Проверьте результат перед повторной отправкой.'


def catalog_form(request,user,topic=None,values=None,error=None,code=200):
    values=values if values is not None else ({'title':topic.title,'category':topic.category,'version':topic.version,'creation_key':topic.creation_key} if topic else {'creation_key':uuid4().hex})
    response=render(request,'admin/topic_catalog_form.html',user=user,topic=topic,values=values,form_error=error)
    response.status_code=code
    return response


@router.get('/topics')
def catalog(request:Request,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    page=page_number(request);q=request.query_params.get('q','').strip()[:180]
    query=select(Topic).order_by(Topic.normalized_title,Topic.id)
    if q:query=query.where(Topic.normalized_title.contains(logic.normalized(q),autoescape=True))
    rows=list(db.scalars(query.limit(LIMIT+1).offset((page-1)*LIMIT)))
    return render(request,'admin/topic_catalog.html',user=user,topics=rows[:LIMIT],more=len(rows)>LIMIT,page=page,q=q)


@router.get('/topics/new')
def new_catalog(request:Request,user:User=Depends(require_admin)):
    return catalog_form(request,user)


@router.post('/topics')
async def create_catalog(request:Request,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    form=await validated_form(request)
    try:
        key=logic.creation_key(form.get('creation_key'))
        if not db.scalar(select(Topic).where(Topic.creation_key==key)):
            logic.get_or_create_topic(db,form.get('title'),form.get('category'),user.id,key)
        db.commit()
    except (HTTPException,SQLAlchemyError,OSError) as exc:
        db.rollback();code,error=failed(exc)
        return catalog_form(request,user,values=dict(form),error=error,code=code)
    return go()


@router.get('/topics/{topic_id}/edit')
def edit_catalog(request:Request,topic_id:int,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    topic=db.get(Topic,topic_id)
    if not topic:raise HTTPException(404)
    return catalog_form(request,user,topic)


@router.post('/topics/{topic_id}/edit')
async def save_catalog(request:Request,topic_id:int,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    topic=db.get(Topic,topic_id)
    if not topic:raise HTTPException(404)
    form=await validated_form(request)
    try:
        title=' '.join(clean_text(form.get('title'),180,'Название темы').split())
        category=' '.join(clean_text(form.get('category'),80,'Категория',False).split())
        version=parse_id(form.get('version'),'версии')
        if form.get('confirm')!='yes':raise HTTPException(400,'Подтвердите изменение общего каталога для всех учеников')
        if version!=topic.version:raise HTTPException(409,'Тема уже изменена. Ваш текст сохранён; откройте актуальную версию.')
        before={'title':topic.title,'category':topic.category}
        result=db.execute(update(Topic).where(Topic.id==topic_id,Topic.version==version).values(title=title,category=category,
            normalized_title=logic.normalized(title),normalized_category=logic.normalized(category),version=Topic.version+1,updated_at=utc_now()),execution_options={'synchronize_session':False})
        if result.rowcount!=1:raise HTTPException(409,'Тема уже изменена в другой вкладке')
        logic.event(db,topic,user.id,'renamed',before=before,after={'title':title,'category':category})
        db.commit()
    except (HTTPException,SQLAlchemyError,OSError) as exc:
        db.rollback();code,error=failed(exc)
        return catalog_form(request,user,topic,dict(form),error,code)
    return go()


def progress_form(request,user,student,db,row=None,values=None,error=None,code=200):
    values=values if values is not None else ({'status':row.status,'note':row.note,'version':row.version} if row else {'creation_key':uuid4().hex,'status':'studying'})
    response=render(request,'admin/student_topic_form.html',user=user,student=student,progress=row,values=values,
        topics=list(db.scalars(select(Topic).order_by(Topic.normalized_title,Topic.id))) if not row else [],statuses=logic.STATUSES,form_error=error)
    response.status_code=code
    return response


@router.get('/students/{student_id}/topics')
def topics(request:Request,student_id:int,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    student=pupil(db,student_id);page=page_number(request)
    q=request.query_params.get('q','').strip()[:180];status=request.query_params.get('status','')
    if status and status not in logic.STATUSES:raise HTTPException(400,'Неизвестный статус')
    query=select(StudentTopic).join(Topic).options(joinedload(StudentTopic.topic)).where(StudentTopic.student_id==student_id).order_by(Topic.normalized_title,StudentTopic.id)
    if q:query=query.where(Topic.normalized_title.contains(logic.normalized(q),autoescape=True))
    if status:query=query.where(StudentTopic.status==status)
    rows=list(db.scalars(query.limit(LIMIT+1).offset((page-1)*LIMIT)))
    latest={}
    for row in rows[:LIMIT]:
        latest[row.id]=db.scalar(select(LessonRecord).join(LessonTopic,LessonTopic.lesson_record_id==LessonRecord.id).where(
            LessonTopic.student_topic_id==row.id,LessonRecord.deleted_at.is_(None)).order_by(LessonRecord.lesson_date.desc(),func.coalesce(LessonRecord.lesson_time,time(0,0)).desc(),LessonRecord.id.desc()).limit(1))
    return render(request,'admin/student_topics.html',user=user,student=student,topics=rows[:LIMIT],latest=latest,more=len(rows)>LIMIT,page=page,q=q,selected_status=status,statuses=logic.STATUSES)


@router.get('/students/{student_id}/topics/new')
def new_progress(request:Request,student_id:int,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    return progress_form(request,user,pupil(db,student_id),db)


@router.post('/students/{student_id}/topics')
async def create_progress(request:Request,student_id:int,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    student=pupil(db,student_id);form=await validated_form(request)
    try:
        key=logic.creation_key(form.get('creation_key'))
        old=db.scalar(select(StudentTopic).where(StudentTopic.creation_key==key))
        if old:
            if old.student_id!=student_id:raise HTTPException(409,'Эта форма уже использована для другого ученика')
            return go(student_id)
        status=logic.pedagogical_status(form.get('status'))
        note=clean_text(form.get('note'),4000,'Примечание',False)
        topic_id=parse_id(form.get('topic_id'),'темы')
        if topic_id:
            if str(form.get('title','')).strip():raise HTTPException(400,'Выберите существующую тему или введите новую — не оба варианта')
            topic=db.get(Topic,topic_id)
            if not topic:raise HTTPException(400,'Тема не найдена')
        else:topic=logic.get_or_create_topic(db,form.get('title'),form.get('category'),user.id)
        existing=db.scalar(select(StudentTopic).where(StudentTopic.student_id==student_id,StudentTopic.topic_id==topic.id))
        if existing and (existing.status!=status or existing.note!=note):
            raise HTTPException(409,'Эта тема уже есть у ученика. Введённые сведения сохранены ниже. Измените существующую отметку через список тем.')
        logic.assign(db,student_id,topic,user.id,status,note,key)
        db.commit()
    except (HTTPException,SQLAlchemyError,OSError) as exc:
        db.rollback();code,error=failed(exc)
        return progress_form(request,user,student,db,values=dict(form),error=error,code=code)
    return go(student_id)


@router.get('/students/{student_id}/topics/{progress_id}')
def detail(request:Request,student_id:int,progress_id:int,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    student=pupil(db,student_id);row=logic.owned_progress(db,student_id,progress_id)
    page=page_number(request);lesson_page=page_number(request,'lesson_page')
    events=list(db.scalars(select(TopicEvent).where(or_(TopicEvent.student_topic_id==row.id,
        (TopicEvent.topic_id==row.topic_id)&TopicEvent.student_topic_id.is_(None))).order_by(TopicEvent.id.desc()).limit(LIMIT+1).offset((page-1)*LIMIT)))
    lessons=list(db.scalars(select(LessonRecord).join(LessonTopic,LessonTopic.lesson_record_id==LessonRecord.id).where(
        LessonTopic.student_topic_id==row.id,LessonRecord.deleted_at.is_(None)).order_by(LessonRecord.lesson_date.desc(),func.coalesce(LessonRecord.lesson_time,time(0,0)).desc(),LessonRecord.id.desc()).limit(LIMIT+1).offset((lesson_page-1)*LIMIT)))
    return render(request,'admin/student_topic_detail.html',user=user,student=student,progress=row,statuses=logic.STATUSES,
        events=[(e,json.loads(e.before_json),json.loads(e.after_json)) for e in events[:LIMIT]],event_labels=logic.EVENTS,
        more=len(events)>LIMIT,page=page,lessons=lessons[:LIMIT],lesson_page=lesson_page,more_lessons=len(lessons)>LIMIT)


@router.get('/students/{student_id}/topics/{progress_id}/edit')
def edit_progress(request:Request,student_id:int,progress_id:int,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    return progress_form(request,user,pupil(db,student_id),db,logic.owned_progress(db,student_id,progress_id))


@router.post('/students/{student_id}/topics/{progress_id}/edit')
async def update_progress(request:Request,student_id:int,progress_id:int,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    student=pupil(db,student_id);row=logic.owned_progress(db,student_id,progress_id);form=await validated_form(request)
    values=dict(form);values.setdefault('note',row.note)
    try:
        logic.change_progress(db,row,parse_id(form.get('version'),'версии'),form.get('status'),values['note'],user.id)
        db.commit()
    except (HTTPException,SQLAlchemyError,OSError) as exc:
        db.rollback();code,error=failed(exc)
        return progress_form(request,user,student,db,row,values,error,code)
    if form.get('return_to')=='progress':
        page=parse_id(form.get('return_page','1'),'страницы') or 1
        if page>100000:page=1
        return RedirectResponse(f'/admin/students/{student_id}?notice=saved&section=progress&page={page}#progress-top',status_code=303)
    if form.get('return_to')=='card':return RedirectResponse(f'/admin/students/{student_id}?notice=saved&section=overview',status_code=303)
    return go(student_id)
