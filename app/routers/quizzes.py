"""Quiz UI routes. Student payloads are built explicitly without hidden solutions."""
from uuid import uuid4
from fastapi import APIRouter,Depends,Request,HTTPException
from fastapi.responses import RedirectResponse,JSONResponse
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from ..web import db_session,require_admin,require_student,validated_form,render
from ..models import User,Topic,utc_now
from ..quiz_models import *
from .. import quizzes as q
router=APIRouter()

def paginated(request,db,query,key,size=50):
    number=q.integer(request.query_params.get(key,'1'),'page',1,1000000)
    rows=list(db.scalars(query.offset((number-1)*size).limit(size+1)))
    links=dict(number=number,previous=str(request.url.include_query_params(**{key:number-1})) if number>1 else None,next=str(request.url.include_query_params(**{key:number+1})) if len(rows)>size else None)
    return rows[:size],links

def context():return dict(types=q.TYPES,states=q.STATES,points=q.display,creation_key=uuid4().hex)
def page(request,template,user,**kw):return render(request,template,user=user,**context(),**kw)
def finalize(db,user):q.finalize_scope(db,user);db.commit()
def assignment_content(db,row):
    v=db.get(QuizVersion,row.version_id)
    latest=db.scalar(select(QuizAttempt).where(QuizAttempt.assignment_id==row.id).order_by(QuizAttempt.number.desc()).limit(1))
    status=q.STATES[latest.state] if latest else ('Срок истёк' if row.deadline and utc_now()>=row.deadline else 'Не начат')
    return dict(status=status,row=row,version=v,topics=list(db.scalars(select(QuizVersionTopic).where(QuizVersionTopic.version_id==v.id))),used=q.count_attempts(db,row.id))

@router.get('/admin/quizzes')
def admin_index(request:Request,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    finalize(db,user)
    versions,version_pages=paginated(request,db,select(QuizVersion).order_by(QuizVersion.id.desc()),'versions_page')
    quizzes={x.id:x for x in db.scalars(select(Quiz))}
    query=select(QuizAssignment).order_by(QuizAssignment.id.desc())
    sid=request.query_params.get('student_id')
    if sid:query=query.where(QuizAssignment.student_id==q.integer(sid,'ученик'))
    assignments,assignment_pages=paginated(request,db,query,'assignments_page')
    pending,pending_pages=paginated(request,db,select(QuizAttempt).where(QuizAttempt.state=='submitted').order_by(QuizAttempt.id),'pending_page')
    return page(request,'admin/quizzes.html',user,mode='index',version_pages=version_pages,assignment_pages=assignment_pages,pending_pages=pending_pages,versions=versions,quizzes=quizzes,assignments=assignments,pending=pending,users={x.id:x for x in db.scalars(select(User))},subjects=list(db.scalars(select(Subject).order_by(Subject.title))))

@router.get('/admin/quizzes/versions/{vid}')
def editor(request:Request,vid:int,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    v=q.get(db,QuizVersion,vid);rows=q.questions(db,vid)
    option_map = {question.id: [] for question in rows}
    options_query = select(QuizOption).join(QuizQuestion).where(QuizQuestion.version_id == vid)
    for option in db.scalars(options_query.order_by(QuizOption.question_id, QuizOption.position)):
        option_map[option.question_id].append(option)
    return page(request,'admin/quizzes.html',user,mode='editor',v=v,quiz=db.get(Quiz,v.quiz_id),questions=rows,option_map=option_map,subjects=list(db.scalars(select(Subject))),topics=list(db.scalars(select(Topic).order_by(Topic.title))),chosen=set(db.scalars(select(QuizVersionTopic.topic_id).where(QuizVersionTopic.version_id==vid))),preview=request.query_params.get('preview')=='yes')

@router.get('/admin/quizzes/versions/{vid}/question')
def question_form(request:Request,vid:int,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    v=q.get(db,QuizVersion,vid)
    if v.state!='draft':raise HTTPException(409,'Создайте новый черновик опубликованного теста')
    question=q.get(db,QuizQuestion,request.query_params['question_id']) if request.query_params.get('question_id') else None
    if question and question.version_id!=vid:raise HTTPException(404,'Вопрос не найден')
    return page(request,'admin/quizzes.html',user,mode='question',v=v,question=question,options=q.options(db,question.id) if question else [])

@router.get('/admin/quizzes/versions/{vid}/assign')
def assign_form(request:Request,vid:int,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    v=q.get(db,QuizVersion,vid)
    if v.state!='published':raise HTTPException(409,'Сначала опубликуйте версию')
    return page(request,'admin/quizzes.html',user,mode='assign',v=v,assignment=None,students=list(db.scalars(select(User).where(User.role=='student',User.active.is_(True),User.learning_status=='active').order_by(User.display_name))),events=[],attempts=[])

@router.get('/admin/quizzes/assignments/{aid}')
def admin_assignment(request:Request,aid:int,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    finalize(db,user);row=q.get(db,QuizAssignment,aid)
    return page(request,'admin/quizzes.html',user,mode='assign',v=db.get(QuizVersion,row.version_id),assignment=row,students=[db.get(User,row.student_id)],events=list(db.scalars(select(QuizAssignmentEvent).where(QuizAssignmentEvent.assignment_id==aid).order_by(QuizAssignmentEvent.revision.desc()))),attempts=list(db.scalars(select(QuizAttempt).where(QuizAttempt.assignment_id==aid).order_by(QuizAttempt.number))))

@router.get('/admin/quizzes/attempts/{aid}')
def admin_attempt(request:Request,aid:int,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    finalize(db,user);a=q.get(db,QuizAttempt,aid);ans=q.answers(db,aid)
    return page(request,'admin/quizzes.html',user,mode='review',a=a,v=db.get(QuizVersion,a.version_id),student=db.get(User,a.student_id),answers=ans,questions={x.question_id:db.get(QuizQuestion,x.question_id) for x in ans},options={x.question_id:q.options(db,x.question_id) for x in ans},selected={x.id:q.selected(db,x.id) for x in ans},grades=list(db.scalars(select(QuizGradeEvent).where(QuizGradeEvent.answer_id.in_([x.id for x in ans])).order_by(QuizGradeEvent.id.desc()))),extensions=list(db.scalars(select(QuizExtensionEvent).where(QuizExtensionEvent.attempt_id==aid).order_by(QuizExtensionEvent.id.desc()))),actors={u.id:u.display_name for u in db.scalars(select(User))})

@router.get('/student/quizzes')
def student_index(request:Request,db:Session=Depends(db_session),user:User=Depends(require_student)):
    finalize(db,user)
    rows,assignment_pages=paginated(request,db,select(QuizAssignment).where(QuizAssignment.student_id==user.id).order_by(QuizAssignment.id.desc()),'assignments_page')
    return page(request,'student/quizzes.html',user,mode='index',assignment_pages=assignment_pages,assignments=[assignment_content(db,r) for r in rows],now=utc_now())

@router.get('/student/quizzes/assignments/{aid}')
def student_assignment(request:Request,aid:int,db:Session=Depends(db_session),user:User=Depends(require_student)):
    row=q.owned_assignment(db,aid,user);finalize(db,user);now=utc_now();history=[]
    for a in db.scalars(select(QuizAttempt).where(QuizAttempt.assignment_id==aid).order_by(QuizAttempt.number)):
        scores,_=q.disclosure(db,a,now);history.append(dict(id=a.id,number=a.number,state=a.state,started_at=a.started_at,total_points=a.total_points if scores else None,max_points=a.max_points))
    return page(request,'student/quizzes.html',user,mode='assignment',item=assignment_content(db,row),history=history,now=now)

@router.get('/student/quizzes/attempts/{aid}')
def student_attempt(request:Request,aid:int,db:Session=Depends(db_session),user:User=Depends(require_student)):
    a=q.owned_attempt(db,aid,user);finalize(db,user);now=utc_now();content,scores,solutions=q.learner_content(db,a,now)
    safe=dict(id=a.id,assignment_id=a.assignment_id,number=a.number,revision=a.revision,state=a.state,started_at=a.started_at,submitted_at=a.submitted_at,expires_at=a.expires_at,timed_out=a.timed_out,max_points=a.max_points)
    if scores:safe.update(total_points=a.total_points,percentage=q.percentage(a))
    pos=q.integer(request.query_params.get('question','1'),'номер вопроса',1,max(1,len(content)))
    return page(request,'student/quizzes.html',user,mode='attempt',a=safe,v=db.get(QuizVersion,a.version_id),content=content,current=content[pos-1],position=pos,scores=scores,solutions=solutions,server_now=now.isoformat()+'Z',expires_iso=a.expires_at.isoformat()+'Z' if a.expires_at else '',saved=request.query_params.get('notice')=='saved')

async def post(request,db,user):
    form=await validated_form(request);action=form.get('action');is_student=user.role=='student'
    try:
        ident=q.execute(db,user,form,request.app.state.settings.timezone);db.commit()
    except (HTTPException,SQLAlchemyError) as exc:
        db.rollback()
        code=exc.status_code if isinstance(exc,HTTPException) else 503
        if code in (403,404):raise
        error=exc.detail if isinstance(exc,HTTPException) else 'Сохранение не подтверждено. Ввод сохранён ниже; проверьте актуальные данные перед повтором.'
        if request.headers.get('x-quiz-save')=='yes':return JSONResponse({'error':error},status_code=code)
        response=page(request,'quiz_error.html',user,form_error=error,fields=list(form.multi_items()),student_mode=is_student)
        response.status_code=code;return response
    if is_student:
        a=db.get(QuizAttempt,ident)
        if request.headers.get('x-quiz-save')=='yes':
            answer=db.get(QuizAnswer,int(form.get('answer_id',0)))
            return JSONResponse(dict(state=a.state,revision=answer.revision if answer and answer.attempt_id==a.id else None,attempt_revision=a.revision))
        suffix='?notice=saved&question='+str(q.integer(form.get('next_question','1'),'номер вопроса',1,100)) if action=='save' and a.state=='in_progress' else ''
        return RedirectResponse(f'/student/quizzes/attempts/{ident}'+suffix,303)
    if action in {'grade','review_complete','extend'}:path=f'/admin/quizzes/attempts/{ident}'
    elif action in {'assign','assignment_edit'}:path=f'/admin/quizzes/assignments/{ident}'
    elif action in {'subject','archive'}:path='/admin/quizzes'
    else:path=f'/admin/quizzes/versions/{ident}'
    notice='attempt_closed' if action=='extend' and db.get(QuizAttempt,ident).state!='in_progress' else 'saved'
    return RedirectResponse(path+'?notice='+notice,303)

@router.post('/admin/quizzes/action')
async def admin_post(request:Request,db:Session=Depends(db_session),user:User=Depends(require_admin)):return await post(request,db,user)
@router.post('/student/quizzes/action')
async def student_post(request:Request,db:Session=Depends(db_session),user:User=Depends(require_student)):return await post(request,db,user)
