"""Quiz transactions. Published content is immutable; conditions are frozen at start."""
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
import hashlib,json,re
from sqlalchemy import select,func,delete
from fastapi import HTTPException
from .models import User,Topic,utc_now
from .quiz_models import *
from .scheduling import lock,local_to_utc
from .web import clean_text
from .progress import creation_key

TYPES={'single':'Один ответ','multiple':'Несколько ответов','boolean':'Верно / неверно','text':'Короткий текст'}
STATES={'draft':'Черновик','published':'Опубликован','in_progress':'В процессе','submitted':'Ожидает проверки','checked':'Проверен'}

def fail(s,code=400):raise HTTPException(code,s)
def integer(v,label,low=1,high=2147483647):
    try:n=int(str(v))
    except (ValueError,TypeError):fail('Проверьте: '+label)
    if not low<=n<=high:fail('Недопустимое значение: '+label)
    return n

def points(v,zero=False):
    raw=str(v).strip().replace(',','.')
    if not re.fullmatch(r'\d{1,5}(?:\.\d{1,2})?',raw):fail('Баллы: число с точностью до сотой')
    n=int(Decimal(raw)*100)
    if not (0 if zero else 1)<=n<=1000000:fail('Баллы должны быть в допустимом диапазоне до 10 000')
    return n

def display(n):return '' if n is None else format(Decimal(n)/100,'f').rstrip('0').rstrip('.') if n%100 else str(n//100)
def percentage(a):return str((Decimal(a.total_points)*100/Decimal(a.max_points)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)) if a.state=='checked' else None

def get(db,model,ident):
    row=db.get(model,integer(ident,'идентификатор'))
    if not row:fail('Запись не найдена',404)
    return row

def questions(db,vid):return list(db.scalars(select(QuizQuestion).where(QuizQuestion.version_id==vid).order_by(QuizQuestion.position)))
def options(db,qid):return list(db.scalars(select(QuizOption).where(QuizOption.question_id==qid).order_by(QuizOption.position)))
def answers(db,aid):return list(db.scalars(select(QuizAnswer).where(QuizAnswer.attempt_id==aid).order_by(QuizAnswer.id)))
def selected(db,aid):return set(db.scalars(select(QuizAnswerOption.option_id).where(QuizAnswerOption.answer_id==aid)))
def count_attempts(db,ident):return db.scalar(select(func.count()).select_from(QuizAttempt).where(QuizAttempt.assignment_id==ident))
def claim(row,form):
    if integer(form.get('revision'),'версия',0)!=row.revision:fail('Данные изменились в другой вкладке. Ваш ввод сохранён; сравните его с актуальными данными.',409)

def draft(db,ident,form):
    row=get(db,QuizVersion,ident)
    if row.state!='draft':fail('Опубликованная версия неизменяема. Создайте новый черновик.',409)
    if db.get(Quiz,row.quiz_id).archived:fail('Тест архивирован',409)
    claim(row,form);row.revision+=1;return row

def owned_assignment(db,ident,user):
    row=get(db,QuizAssignment,ident)
    if user.role!='admin' and row.student_id!=user.id:fail('Назначение не найдено',404)
    return row

def owned_attempt(db,ident,user):
    row=get(db,QuizAttempt,ident)
    if user.role!='admin' and row.student_id!=user.id:fail('Попытка не найдена',404)
    return row

def finish(db,a,now,timeout=False):
    if a.state!='in_progress':return
    for answer in answers(db,a.id):
        q=db.get(QuizQuestion,answer.question_id)
        if q.kind!='text':
            correct={o.id for o in options(db,q.id) if o.correct}
            answer.points=q.max_points if selected(db,answer.id)==correct else 0
        answer.revision+=1
    a.state='submitted';a.submitted_at=a.expires_at if timeout else now;a.finalized_at=now;a.timed_out=timeout;a.revision+=1
    db.flush()
    if all(x.points is not None for x in answers(db,a.id)):
        a.total_points=sum(x.points for x in answers(db,a.id));a.state='checked';a.checked_at=now

def expire(db,a,now):
    if a.state=='in_progress' and a.expires_at and now>=a.expires_at:finish(db,a,now,True)

def finalize_scope(db,user,now=None):
    lock(db);db.expire_all();now=now or utc_now()
    query=select(QuizAttempt).where(QuizAttempt.state=='in_progress',QuizAttempt.expires_at.is_not(None),QuizAttempt.expires_at<=now)
    if user.role!='admin':query=query.where(QuizAttempt.student_id==user.id)
    for a in db.scalars(query):finish(db,a,now,True)
    db.flush()

def disclosure(db,a,now):
    assignment=db.get(QuizAssignment,a.assignment_id)
    active=db.scalar(select(QuizAttempt.id).where(QuizAttempt.assignment_id==assignment.id,QuizAttempt.state=='in_progress').limit(1))
    closed=count_attempts(db,assignment.id)>=assignment.max_attempts or bool(assignment.deadline and now>=assignment.deadline)
    complete=a.state=='checked'
    return complete and assignment.show_scores, complete and assignment.show_correct and closed and not active

def learner_content(db,a,now):
    """Explicit DTO: correct flags and hidden grades never enter the student template."""
    scores,solutions=disclosure(db,a,now);out=[]
    for answer in answers(db,a.id):
        q=db.get(QuizQuestion,answer.question_id)
        row=dict(id=q.id,answer_id=answer.id,revision=answer.revision,position=q.position,kind=q.kind,body=q.body,max_points=q.max_points,text_answer=answer.text_answer,selected=selected(db,answer.id),options=[dict(id=o.id,body=o.body) for o in options(db,q.id)])
        if scores:row.update(points=answer.points,grade_comment=answer.grade_comment)
        if solutions:row['correct_options']=[o.id for o in options(db,q.id) if o.correct]
        out.append(row)
    return sorted(out,key=lambda x:x['position']),scores,solutions

def snapshot_assignment(db,row,actor,reason):
    db.add(QuizAssignmentEvent(assignment_id=row.id,revision=row.revision,actor_id=actor,reason=reason,
        **{k:getattr(row,k) for k in ('available_from','deadline','max_attempts','time_limit_minutes','show_scores','show_correct','comment')}))

def clone(db,old,quiz,actor):
    number=(db.scalar(select(func.max(QuizVersion.number)).where(QuizVersion.quiz_id==quiz.id)) or 0)+1
    v=QuizVersion(quiz_id=quiz.id,number=number,title=old.title,description=old.description,subject_id=old.subject_id,subject_title=old.subject_title);db.add(v);db.flush()
    for t in db.scalars(select(QuizVersionTopic).where(QuizVersionTopic.version_id==old.id)):db.add(QuizVersionTopic(version_id=v.id,topic_id=t.topic_id,title_snapshot=t.title_snapshot))
    for q in questions(db,old.id):
        n=QuizQuestion(version_id=v.id,position=q.position,kind=q.kind,body=q.body,max_points=q.max_points);db.add(n);db.flush()
        for o in options(db,q.id):db.add(QuizOption(question_id=n.id,position=o.position,body=o.body,correct=o.correct))
    return v

def execute(db,user,form,tz='Europe/Moscow',now=None):
    lock(db);db.expire_all();now=now or utc_now()
    actor=db.get(User,user.id)
    if not actor or not actor.active or actor.role not in {'admin','student'}:fail('Нет доступа',403)
    action=form.get('action','')
    student_actions={'start','save','submit'}
    if (actor.role=='student')!=(action in student_actions):fail('Нет доступа к действию',403)
    key=creation_key(form.get('creation_key'))
    data={k:form.getlist(k) if hasattr(form,'getlist') else form[k] for k in form if k!='csrf'}
    digest=hashlib.sha256(json.dumps([actor.id,data],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    prior=db.scalar(select(QuizRequest).where(QuizRequest.request_key==key))
    if prior:
        if prior.request_hash!=digest:fail('Ключ запроса уже использован для других данных',409)
        return prior.result_id
    ident=form.get('id');result=None
    def values(name):
        raw=form.getlist(name) if hasattr(form,'getlist') else form.get(name,[])
        return raw if isinstance(raw,list) else [raw]
    if action=='subject':
        title=' '.join(clean_text(form.get('title'),160,'Предмет').split());normalized=title.casefold()
        row=db.scalar(select(Subject).where(Subject.normalized_title==normalized))
        if not row:row=Subject(title=title,normalized_title=normalized);db.add(row);db.flush()
        result=row.id
    elif action=='create':
        title=clean_text(form.get('title'),180,'Название');quiz=Quiz(title=title,created_by=actor.id);db.add(quiz);db.flush()
        v=QuizVersion(quiz_id=quiz.id,number=1,title=title,description='');db.add(v);db.flush();result=v.id
    elif action in {'copy','new_version'}:
        old=get(db,QuizVersion,ident);quiz=db.get(Quiz,old.quiz_id)
        if action=='copy':quiz=Quiz(title=old.title+' — копия',created_by=actor.id);db.add(quiz);db.flush()
        elif quiz.archived:fail('Тест архивирован')
        result=clone(db,old,quiz,actor.id).id
    elif action=='archive':
        quiz=get(db,Quiz,ident);claim(quiz,form)
        if form.get('confirm')!='yes':fail('Подтвердите архивирование')
        quiz.archived=True;quiz.revision+=1;result=quiz.id
    elif action=='metadata':
        v=draft(db,ident,form);v.title=clean_text(form.get('title'),180,'Название');v.description=clean_text(form.get('description'),10000,'Описание',False)
        subject=get(db,Subject,form['subject_id']) if form.get('subject_id') else None
        v.subject_id=subject.id if subject else None;v.subject_title=subject.title if subject else ''
        db.execute(delete(QuizVersionTopic).where(QuizVersionTopic.version_id==v.id))
        for tid in set(values('topics')):
            topic=get(db,Topic,tid);db.add(QuizVersionTopic(version_id=v.id,topic_id=topic.id,title_snapshot=topic.title))
        db.get(Quiz,v.quiz_id).title=v.title;result=v.id
    elif action=='question':
        v=draft(db,ident,form);kind=form.get('kind')
        if kind not in TYPES:fail('Выберите тип вопроса')
        q=get(db,QuizQuestion,form['question_id']) if form.get('question_id') else None
        if q and q.version_id!=v.id:fail('Вопрос не найден в этой версии',404)
        body=clean_text(form.get('body'),10000,'Вопрос');weight=points(form.get('points'))
        opts=[]
        if kind in {'single','multiple'}:
            for i in range(1,11):
                text_value=clean_text(form.get('option_'+str(i)),3000,'Вариант',False)
                if text_value:opts.append((text_value,form.get('correct_'+str(i))=='yes'))
            if len(opts)<2:fail('Добавьте хотя бы два варианта')
            correct=sum(o[1] for o in opts)
            if correct<1 or (kind=='single' and correct!=1):fail('Укажите правильные варианты; для одного ответа — ровно один')
        elif kind=='boolean':
            if form.get('truth') not in {'true','false'}:fail('Выберите верно или неверно')
            opts=[('Верно',form.get('truth')=='true'),('Неверно',form.get('truth')=='false')]
        if not q:
            if len(questions(db,v.id))>=100:fail('В одной версии допускается до 100 вопросов')
            q=QuizQuestion(version_id=v.id,position=len(questions(db,v.id))+1,kind=kind,body=body,max_points=weight);db.add(q);db.flush()
        else:
            q.kind=kind;q.body=body;q.max_points=weight;db.execute(delete(QuizOption).where(QuizOption.question_id==q.id))
        for i,(label,correct) in enumerate(opts,1):db.add(QuizOption(question_id=q.id,position=i,body=label,correct=correct))
        result=v.id
    elif action in {'remove_question','move_question'}:
        v=draft(db,ident,form);q=get(db,QuizQuestion,form.get('question_id'))
        if q.version_id!=v.id:fail('Вопрос не найден',404)
        rows=questions(db,v.id)
        if action=='remove_question':
            db.execute(delete(QuizOption).where(QuizOption.question_id==q.id));db.delete(q);rows=[r for r in rows if r.id!=q.id];db.flush()
        else:
            index=rows.index(q);target=index+(-1 if form.get('direction')=='up' else 1)
            if not 0<=target<len(rows):fail('Вопрос уже находится у края списка')
            rows[index],rows[target]=rows[target],rows[index]
        for i,row in enumerate(rows,1001):row.position=i
        db.flush()
        for i,row in enumerate(rows,1):row.position=i
        result=v.id
    elif action=='publish':
        v=draft(db,ident,form)
        if form.get('confirm')!='yes':fail('Подтвердите публикацию')
        if not questions(db,v.id):fail('Добавьте хотя бы один вопрос')
        for q in questions(db,v.id):
            opts=options(db,q.id);correct=sum(o.correct for o in opts)
            if q.kind!='text' and (len(opts)<2 or not correct or (q.kind in {'single','boolean'} and correct!=1)):fail('Проверьте правильные ответы')
        v.state='published';v.published_at=now;result=v.id
    elif action in {'assign','assignment_edit'}:
        if action=='assign':
            v=get(db,QuizVersion,ident)
            if v.state!='published' or db.get(Quiz,v.quiz_id).archived:fail('Выберите опубликованный активный тест')
            students=set(values('students'))
            if not students or len(students)>50:fail('Выберите от 1 до 50 учеников')
            rows=[]
            for sid in students:
                student=get(db,User,sid)
                if student.role!='student' or not student.active or student.learning_status!='active':fail('Выберите активного ученика')
                if db.scalar(select(QuizAssignment.id).where(QuizAssignment.version_id==v.id,QuizAssignment.student_id==student.id)):fail('Эта версия уже назначена одному из выбранных учеников. Измените существующее назначение.',409)
                row=QuizAssignment(version_id=v.id,student_id=student.id,created_by=actor.id);db.add(row);rows.append(row)
        else:
            row=get(db,QuizAssignment,ident);claim(row,form);row.revision+=1;rows=[row]
        available=local_to_utc(form['available_from'],tz) if form.get('available_from') else None
        deadline=local_to_utc(form['deadline'],tz) if form.get('deadline') else None
        if available and deadline and deadline<=available:fail('Дедлайн должен быть позже начала доступности')
        limit=integer(form['time_limit'],'минуты',1,1440) if form.get('time_limit') else None
        maximum=integer(form.get('max_attempts'),'попытки',1,100)
        reason=clean_text(form.get('reason'),1000,'Причина изменения',action=='assignment_edit')
        for row in rows:
            if row.id and maximum<count_attempts(db,row.id):fail('Лимит не может быть меньше уже начатого количества попыток')
            row.available_from=available;row.deadline=deadline;row.time_limit_minutes=limit;row.max_attempts=maximum;row.show_scores=form.get('show_scores')=='yes';row.show_correct=form.get('show_correct')=='yes';row.comment=clean_text(form.get('comment'),3000,'Комментарий',False)
            db.flush();snapshot_assignment(db,row,actor.id,reason)
        result=rows[0].id
    elif action=='start':
        row=owned_assignment(db,ident,actor)
        active=db.scalar(select(QuizAttempt).where(QuizAttempt.assignment_id==row.id,QuizAttempt.state=='in_progress'))
        if active:expire(db,active,now);result=active.id
        else:
            if row.available_from and now<row.available_from:fail('Тест пока недоступен')
            if row.deadline and now>=row.deadline:fail('Срок начала теста истёк')
            used=count_attempts(db,row.id)
            if used>=row.max_attempts:fail('Доступные попытки использованы')
            limits=[x for x in (row.deadline,now+timedelta(minutes=row.time_limit_minutes) if row.time_limit_minutes else None) if x]
            a=QuizAttempt(assignment_id=row.id,student_id=actor.id,version_id=row.version_id,number=used+1,assignment_revision=row.revision,started_at=now,deadline_snapshot=row.deadline,time_limit_snapshot=row.time_limit_minutes,expires_at=min(limits) if limits else None,max_points=sum(q.max_points for q in questions(db,row.version_id)))
            db.add(a);db.flush()
            for q in questions(db,row.version_id):db.add(QuizAnswer(attempt_id=a.id,version_id=a.version_id,question_id=q.id))
            result=a.id
    elif action in {'save','submit','grade','review_complete','extend'}:
        a=owned_attempt(db,ident,actor);expire(db,a,now);result=a.id
        if action in {'save','submit','extend'} and a.state!='in_progress':
            # Lazy finalization must commit even when the current request arrived too late.
            pass
        elif action=='save':
            answer=get(db,QuizAnswer,form.get('answer_id'))
            if answer.attempt_id!=a.id:fail('Ответ не найден',404)
            claim(answer,form);q=db.get(QuizQuestion,answer.question_id)
            chosen={integer(x,'вариант') for x in values('options')}
            allowed={o.id for o in options(db,q.id)}
            if not chosen<=allowed or (q.kind in {'single','boolean'} and len(chosen)>1) or (q.kind=='text' and chosen):fail('Проверьте выбранные варианты')
            answer.text_answer=clean_text(form.get('text_answer'),20000,'Ответ',False) if q.kind=='text' else ''
            db.execute(delete(QuizAnswerOption).where(QuizAnswerOption.answer_id==answer.id))
            for option in chosen:db.add(QuizAnswerOption(answer_id=answer.id,question_id=q.id,option_id=option))
            answer.revision+=1;answer.saved_at=now;a.revision+=1
        elif action=='submit':
            claim(a,form)
            if form.get('confirm')!='yes':fail('Подтвердите окончательную отправку')
            finish(db,a,now)
        elif action=='extend':
            claim(a,form)
            if form.get('confirm')!='yes':fail('Подтвердите индивидуальное продление')
            reason=clean_text(form.get('reason'),1000,'Причина продления')
            if not a.expires_at:fail('У попытки уже нет ограничения времени')
            if bool(form.get('minutes'))==bool(form.get('new_end')):fail('Укажите дополнительные минуты или новый срок')
            end=a.expires_at+timedelta(minutes=integer(form['minutes'],'дополнительные минуты',1,10080)) if form.get('minutes') else local_to_utc(form['new_end'],tz)
            if end<=a.expires_at or end<=now:fail('Новый срок должен быть позже текущего')
            db.add(QuizExtensionEvent(attempt_id=a.id,actor_id=actor.id,old_expires_at=a.expires_at,new_expires_at=end,reason=reason));a.expires_at=end;a.revision+=1
        elif action=='grade':
            if a.state=='in_progress':fail('Попытка ещё не отправлена')
            answer=get(db,QuizAnswer,form.get('answer_id'))
            if answer.attempt_id!=a.id:fail('Ответ не найден',404)
            claim(answer,form);q=db.get(QuizQuestion,answer.question_id)
            if q.kind!='text':fail('Объективные вопросы проверяются по опубликованным правилам')
            value=points(form.get('points'),True)
            if value>q.max_points:fail('Баллы выше максимума вопроса')
            reason=clean_text(form.get('reason'),1000,'Причина исправления',answer.points is not None);comment=clean_text(form.get('comment'),3000,'Комментарий',False)
            db.add(QuizGradeEvent(answer_id=answer.id,actor_id=actor.id,old_points=answer.points,new_points=value,old_comment=answer.grade_comment,new_comment=comment,reason=reason))
            answer.points=value;answer.grade_comment=comment;answer.revision+=1;a.revision+=1
            db.flush()
            if a.state=='checked':a.total_points=sum(x.points for x in answers(db,a.id))
        elif action=='review_complete':
            if a.state!='submitted':fail('Попытка не ожидает проверки')
            claim(a,form)
            if any(x.points is None for x in answers(db,a.id)):fail('Остались ответы без ручной оценки')
            a.total_points=sum(x.points for x in answers(db,a.id));a.state='checked';a.checked_at=now;a.revision+=1
    else:fail('Неизвестное действие')
    db.flush();db.add(QuizRequest(request_key=key,request_hash=digest,actor_id=actor.id,action=action,result_id=result));db.flush();return result
