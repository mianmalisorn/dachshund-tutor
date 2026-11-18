"""Private student memory. Every route checks the teacher role on the server."""
import json
import re
from datetime import date, time, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select, update, func, literal, cast, union_all, String
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from sqlalchemy.orm import Session, selectinload
from ..models import (User, Assignment, AssignmentTarget, Submission, ReviewEvent, DeadlineEvent, StudentStatusEvent,
    LessonRecord, StudentNote, TeachingRevision, TopicEvent, StudentTopic, LessonTopic, Lesson, LessonSeries, ScheduleEvent, utc_now)
from ..students import STATUS_LABELS
from .. import progress as topic_logic
from .. import scheduling
from ..models import Lesson, LessonSeries, LessonCompletion
from ..models import VideoMeeting
from ..web import db_session, require_admin, validated_form, render, clean_text, parse_id
from ..attendance_models import LessonAttendance, AttendanceEvent
from ..quiz_models import QuizAssignment, QuizVersion, QuizAttempt, QuizAnswer, QuizGradeEvent

router = APIRouter(prefix='/admin/students')
PAGE_SIZE = 10
FIELDS = {'records': {'topic': 'Что проходили / краткий итог', 'completed': 'Что успели сделать',
                     'stopped_at': 'Где остановились', 'difficulties': 'Трудности', 'next_step': 'Что дальше'},
          'notes': {'body': 'Личная заметка'}}
PROFILE = {'learning_goal': ('Цель занятий', 2000), 'learning_materials': ('Учебник и материалы', 2000),
           'learning_position': ('Текущая точка обучения', 2000), 'internal_notes': ('Общая заметка', 4000)}


def pupil(db, student_id):
    row = db.get(User, student_id)
    if not row or row.role != 'student':
        raise HTTPException(404, 'Ученик не найден')
    return row


def model_for(kind):
    if kind not in FIELDS:
        raise HTTPException(404)
    return LessonRecord if kind == 'records' else StudentNote


def record_for(db, model, student_id, record_id):
    row = db.scalar(select(model).where(model.id == record_id, model.student_id == student_id, model.deleted_at.is_(None)))
    if not row:
        raise HTTPException(404, 'Запись не найдена')
    return row


def today(request):
    return utc_now().replace(tzinfo=timezone.utc).astimezone(ZoneInfo(request.app.state.settings.timezone)).date()


def destination(student_id, kind='records', section=None):
    section = section if section in CARD_SECTIONS else ('history' if kind in {'records','notes'} else 'overview')
    return RedirectResponse(f'/admin/students/{student_id}?notice=saved&section={section}#{kind}',status_code=303)


def values_of(row, kind):
    names = list(FIELDS[kind]) + (['lesson_date','lesson_time','timezone_name'] if kind=='records' else [])
    return {key: str(getattr(row,key) or '') for key in names}


def form_response(request, user, student, kind, row=None, values=None, error=None, status_code=200):
    values = values if values is not None else (values_of(row,kind) if row else {'lesson_date': today(request).isoformat()})
    values.setdefault('creation_key', row.creation_key if row else uuid4().hex)
    values.setdefault('version', row.version if row else 1)
    section=values.get('return_section') or request.query_params.get('section','history')
    if section not in CARD_SECTIONS: section='history'
    values['return_section']=section
    with request.app.state.sessionmaker() as lookup:
        choices=topic_logic.lesson_choices(lookup,student.id) if kind=='records' else []
        selected=values.get('student_topic_ids',topic_logic.lesson_ids(lookup,row.id) if row and kind=='records' else [])
    response = render(request,'admin/teaching_form.html',user=user,student=student,kind=kind,record=row,return_section=section,
                      values=values,fields=FIELDS[kind],form_error=error,topic_choices=choices,selected_topics=selected)
    response.status_code = status_code
    return response


def changed(db, row, model, student_id, expected, data, actor_id, action):
    if expected != row.version:
        raise HTTPException(409,'Запись изменена в другой вкладке. Ваш текст сохранён ниже. Откройте актуальную запись перед повторным сохранением.')
    snapshot = {c.name: str(getattr(row,c.name) or '') for c in model.__table__.columns}
    result = db.execute(update(model).where(model.id==row.id, model.student_id==student_id,
        model.version==expected, model.deleted_at.is_(None)).values(**data,version=model.version+1,updated_at=utc_now()),
        execution_options={'synchronize_session':False})
    if result.rowcount != 1:
        raise HTTPException(409,'Запись изменена в другой вкладке. Ваш текст сохранён ниже. Откройте актуальную запись перед повторным сохранением.')
    db.add(TeachingRevision(lesson_record_id=row.id if model is LessonRecord else None,
                           note_id=row.id if model is StudentNote else None,actor_id=actor_id,
                           action=action,snapshot=json.dumps(snapshot,ensure_ascii=False)))


CARD_SECTIONS = ('overview','schedule','attendance','homework','tests','progress','finance','history')


def history_streams(student_id):
    target_url=literal(f'/admin/students/{student_id}?section=history')
    streams=[]
    streams.append(select(Lesson.starts_at.label('event_at'),literal('lesson').label('event_type'),User.display_name.label('title'),Lesson.state.label('detail'),target_url.label('url'),Lesson.id.label('event_id')).join(User,User.id==Lesson.student_id).where(Lesson.student_id==student_id))
    streams.append(select(ScheduleEvent.created_at.label('event_at'),literal('schedule_change').label('event_type'),literal('Schedule changed').label('title'),ScheduleEvent.kind.label('detail'),(literal('/admin/lessons/')+cast(Lesson.id,String)+literal('/edit')).label('url'),ScheduleEvent.id.label('event_id')).join(Lesson,Lesson.id==ScheduleEvent.lesson_id).where(Lesson.student_id==student_id))
    streams.append(select(ScheduleEvent.created_at.label('event_at'),literal('series_change').label('event_type'),literal('Recurring schedule changed').label('title'),ScheduleEvent.kind.label('detail'),(literal('/admin/lesson-series/')+cast(LessonSeries.id,String)+literal('/edit')).label('url'),ScheduleEvent.id.label('event_id')).join(LessonSeries,LessonSeries.id==ScheduleEvent.series_id).where(LessonSeries.student_id==student_id,ScheduleEvent.lesson_id.is_(None)))
    streams.append(select(AttendanceEvent.created_at.label('event_at'),literal('attendance').label('event_type'),literal('Attendance updated').label('title'),AttendanceEvent.after_outcome.label('detail'),(literal('/admin/lessons/')+cast(AttendanceEvent.lesson_id,String)+literal('/attendance')).label('url'),AttendanceEvent.id.label('event_id')).join(LessonAttendance,LessonAttendance.lesson_id==AttendanceEvent.lesson_id).where(LessonAttendance.student_id==student_id))
    streams.append(select(Submission.created_at.label('event_at'),literal('submission').label('event_type'),Assignment.title.label('title'),literal('Work submitted').label('detail'),(literal('/admin/assignments/')+cast(Assignment.id,String)).label('url'),Submission.id.label('event_id')).join(AssignmentTarget,AssignmentTarget.id==Submission.target_id).join(Assignment,Assignment.id==AssignmentTarget.assignment_id).where(AssignmentTarget.student_id==student_id))
    streams.append(select(ReviewEvent.created_at.label('event_at'),literal('review').label('event_type'),Assignment.title.label('title'),ReviewEvent.state.label('detail'),(literal('/admin/assignments/')+cast(Assignment.id,String)).label('url'),ReviewEvent.id.label('event_id')).join(Submission,Submission.id==ReviewEvent.submission_id).join(AssignmentTarget,AssignmentTarget.id==Submission.target_id).join(Assignment,Assignment.id==AssignmentTarget.assignment_id).where(AssignmentTarget.student_id==student_id))
    streams.append(select(QuizAttempt.submitted_at.label('event_at'),literal('test').label('event_type'),QuizVersion.title.label('title'),QuizAttempt.state.label('detail'),(literal('/admin/quizzes/attempts/')+cast(QuizAttempt.id,String)).label('url'),QuizAttempt.id.label('event_id')).join(QuizVersion,QuizVersion.id==QuizAttempt.version_id).where(QuizAttempt.student_id==student_id,QuizAttempt.submitted_at.is_not(None)))
    streams.append(select(QuizGradeEvent.created_at.label('event_at'),literal('test_grade').label('event_type'),QuizVersion.title.label('title'),literal('Manual score updated').label('detail'),(literal('/admin/quizzes/attempts/')+cast(QuizAttempt.id,String)).label('url'),QuizGradeEvent.id.label('event_id')).join(QuizAnswer,QuizAnswer.id==QuizGradeEvent.answer_id).join(QuizAttempt,QuizAttempt.id==QuizAnswer.attempt_id).join(QuizVersion,QuizVersion.id==QuizAttempt.version_id).where(QuizAttempt.student_id==student_id))
    record_url=literal(f'/admin/students/{student_id}/records/')
    streams.append(select(LessonRecord.updated_at.label('event_at'),literal('record').label('event_type'),LessonRecord.topic.label('title'),literal('Teaching record').label('detail'),(record_url+cast(LessonRecord.id,String)+literal('/edit')).label('url'),LessonRecord.id.label('event_id')).where(LessonRecord.student_id==student_id,LessonRecord.deleted_at.is_(None)))
    streams.append(select(TeachingRevision.created_at.label('event_at'),literal('record_revision').label('event_type'),literal('Teaching record revised').label('title'),TeachingRevision.action.label('detail'),target_url.label('url'),TeachingRevision.id.label('event_id')).join(LessonRecord,LessonRecord.id==TeachingRevision.lesson_record_id).where(LessonRecord.student_id==student_id,LessonRecord.deleted_at.is_(None)))
    streams.append(select(StudentNote.created_at.label('event_at'),literal('note').label('event_type'),literal('Private note added').label('title'),literal('').label('detail'),target_url.label('url'),StudentNote.id.label('event_id')).where(StudentNote.student_id==student_id,StudentNote.deleted_at.is_(None)))
    streams.append(select(TeachingRevision.created_at.label('event_at'),literal('note_revision').label('event_type'),literal('Private note updated').label('title'),literal('').label('detail'),target_url.label('url'),TeachingRevision.id.label('event_id')).join(StudentNote,StudentNote.id==TeachingRevision.note_id).where(StudentNote.student_id==student_id,StudentNote.deleted_at.is_(None)))
    streams.append(select(TopicEvent.created_at.label('event_at'),literal('progress').label('event_type'),TopicEvent.title_snapshot.label('title'),TopicEvent.kind.label('detail'),(literal(f'/admin/students/{student_id}/topics/')+cast(TopicEvent.student_topic_id,String)).label('url'),TopicEvent.id.label('event_id')).join(StudentTopic,StudentTopic.id==TopicEvent.student_topic_id).where(StudentTopic.student_id==student_id))
    streams.append(select(StudentStatusEvent.changed_at.label('event_at'),literal('status').label('event_type'),literal('Student status changed').label('title'),StudentStatusEvent.status.label('detail'),literal(f'/admin/students/{student_id}/edit').label('url'),StudentStatusEvent.id.label('event_id')).where(StudentStatusEvent.student_id==student_id))
    streams.append(select(DeadlineEvent.created_at.label('event_at'),literal('deadline').label('event_type'),Assignment.title.label('title'),literal('Deadline changed').label('detail'),(literal('/admin/assignments/')+cast(Assignment.id,String)).label('url'),DeadlineEvent.id.label('event_id')).join(Assignment,Assignment.id==DeadlineEvent.assignment_id).join(AssignmentTarget,AssignmentTarget.assignment_id==Assignment.id).where(AssignmentTarget.student_id==student_id))
    return streams


@router.get('/{student_id}')
def card(request: Request, student_id: int, db: Session=Depends(db_session), user: User=Depends(require_admin)):
    student=pupil(db,student_id)
    section=request.query_params.get('section','overview')
    if section not in CARD_SECTIONS:raise HTTPException(400,'Unknown student section')
    now=utc_now();context={'section':section,'sections':CARD_SECTIONS,'now':now,'next_lesson':None,'student_series':[],
        'pending_homework':[],'pending_homework_count':0,'quiz_assignments':[],'quiz_assignment_count':0,'pending_quiz_count':0,
        'attendance_counts':{},'last_lesson':None,'schedule_errors':[],'lesson_needing_attendance_count':0}
    context['current_video_meeting']=db.scalar(select(VideoMeeting).where(
        VideoMeeting.lease_slot==1,VideoMeeting.state=='active',VideoMeeting.teacher_id==user.id))
    if section=='overview':
        context['next_lesson']=db.scalar(select(Lesson).where(Lesson.student_id==student_id,Lesson.state=='scheduled',Lesson.starts_at>=now).order_by(Lesson.starts_at,Lesson.id).limit(1))
        context['lesson_needing_attendance']=db.scalar(select(Lesson).outerjoin(LessonAttendance,LessonAttendance.lesson_id==Lesson.id).where(
            Lesson.student_id==student_id,Lesson.state=='scheduled',
            func.datetime(Lesson.starts_at,func.printf('+%d minutes',Lesson.duration_minutes))<=now,
            (LessonAttendance.lesson_id.is_(None) | (LessonAttendance.outcome=='unknown'))
        ).order_by(Lesson.starts_at.desc(),Lesson.id.desc()).limit(1))
        context['lesson_needing_attendance_count']=db.scalar(select(func.count()).select_from(Lesson).outerjoin(
            LessonAttendance,LessonAttendance.lesson_id==Lesson.id).where(
            Lesson.student_id==student_id,Lesson.state=='scheduled',
            func.datetime(Lesson.starts_at,func.printf('+%d minutes',Lesson.duration_minutes))<=now,
            (LessonAttendance.lesson_id.is_(None) | (LessonAttendance.outcome=='unknown'))
        )) or 0
        context['student_series']=list(db.scalars(select(LessonSeries).where(LessonSeries.student_id==student_id,LessonSeries.state!='ended').order_by(LessonSeries.id.desc()).limit(3)))
        context['last_lesson']=db.scalar(select(LessonRecord).where(LessonRecord.student_id==student_id,LessonRecord.deleted_at.is_(None)).order_by(LessonRecord.lesson_date.desc(),func.coalesce(LessonRecord.lesson_time,time(0,0)).desc(),LessonRecord.id.desc()).limit(1))
        latest_submission=select(func.max(Submission.id)).where(Submission.target_id==AssignmentTarget.id).correlate(AssignmentTarget).scalar_subquery()
        pending_filter=(AssignmentTarget.student_id==student_id,Submission.id==latest_submission,Submission.review_state=='pending')
        context['pending_homework_count']=db.scalar(select(func.count()).select_from(AssignmentTarget).join(Submission,Submission.target_id==AssignmentTarget.id).where(*pending_filter)) or 0
        context['pending_homework']=list(db.execute(select(Submission,Assignment.id,Assignment.title).join(AssignmentTarget,AssignmentTarget.id==Submission.target_id).join(Assignment,Assignment.id==AssignmentTarget.assignment_id).where(*pending_filter).order_by(Submission.created_at,Submission.id).limit(5)).all())
        context['quiz_assignment_count']=db.scalar(select(func.count()).select_from(QuizAssignment).where(QuizAssignment.student_id==student_id)) or 0
        context['pending_quiz_count']=db.scalar(select(func.count()).select_from(QuizAttempt).where(QuizAttempt.student_id==student_id,QuizAttempt.state=='submitted')) or 0
        context['pending_quiz_attempts']=list(db.execute(select(QuizAttempt,QuizVersion.title).join(QuizVersion,QuizVersion.id==QuizAttempt.version_id).where(QuizAttempt.student_id==student_id,QuizAttempt.state=='submitted').order_by(QuizAttempt.submitted_at,QuizAttempt.id).limit(5)).all())
        context['quiz_assignments']=list(db.execute(select(QuizAssignment,QuizVersion.title).join(QuizVersion,QuizVersion.id==QuizAssignment.version_id).where(QuizAssignment.student_id==student_id).order_by(QuizAssignment.deadline.is_(None),QuizAssignment.deadline,QuizAssignment.id.desc()).limit(5)).all())
        context['attendance_counts']=dict(db.execute(select(LessonAttendance.outcome,func.count()).where(LessonAttendance.student_id==student_id).group_by(LessonAttendance.outcome)).all())
    elif section=='schedule':
        scope=request.query_params.get('scope','upcoming')
        if scope not in {'upcoming','past'}:raise HTTPException(400,'Invalid schedule filter')
        page=parse_id(request.query_params.get('page','1'),'page')
        if not page or page>100000:raise HTTPException(400,'Invalid page')
        query=select(Lesson).where(Lesson.student_id==student_id)
        query=query.where(Lesson.starts_at>=now) if scope=='upcoming' else query.where(Lesson.starts_at<now)
        ordering=Lesson.starts_at.asc() if scope=='upcoming' else Lesson.starts_at.desc()
        lessons=list(db.scalars(query.order_by(ordering,Lesson.id).offset((page-1)*PAGE_SIZE).limit(PAGE_SIZE+1)))
        context.update(schedule_lessons=lessons[:PAGE_SIZE],schedule_more=len(lessons)>PAGE_SIZE,schedule_page=page,schedule_scope=scope)
        ids=[x.id for x in lessons[:PAGE_SIZE]]
        context['attendance_by_lesson']={x.lesson_id:x for x in db.scalars(select(LessonAttendance).where(LessonAttendance.lesson_id.in_(ids)))} if ids else {}
        context['student_series']=list(db.scalars(select(LessonSeries).where(LessonSeries.student_id==student_id,LessonSeries.state!='ended').order_by(LessonSeries.id.desc()).limit(10)))
    elif section=='attendance':
        page=parse_id(request.query_params.get('page','1'),'page')
        if not page or page>100000:raise HTTPException(400,'Invalid page')
        events=list(db.execute(select(AttendanceEvent,Lesson.starts_at,Lesson.id).join(LessonAttendance,LessonAttendance.lesson_id==AttendanceEvent.lesson_id).join(Lesson,Lesson.id==AttendanceEvent.lesson_id).where(LessonAttendance.student_id==student_id).order_by(AttendanceEvent.created_at.desc(),AttendanceEvent.id.desc()).offset((page-1)*PAGE_SIZE).limit(PAGE_SIZE+1)).all())
        context.update(attendance_events=events[:PAGE_SIZE],attendance_more=len(events)>PAGE_SIZE,attendance_page=page)
    elif section=='homework':
        page=parse_id(request.query_params.get('page','1'),'page')
        if not page or page>100000:raise HTTPException(400,'Invalid page')
        targets=list(db.scalars(select(AssignmentTarget).options(selectinload(AssignmentTarget.assignment),selectinload(AssignmentTarget.submissions)).join(Assignment,Assignment.id==AssignmentTarget.assignment_id).where(AssignmentTarget.student_id==student_id).order_by(Assignment.due_at.desc(),Assignment.id.desc()).offset((page-1)*PAGE_SIZE).limit(PAGE_SIZE+1)))
        from ..logic import status
        context.update(homework=[(target,status(target,now)) for target in targets[:PAGE_SIZE]],homework_more=len(targets)>PAGE_SIZE,homework_page=page)
    elif section=='tests':
        page=parse_id(request.query_params.get('page','1'),'page')
        if not page or page>100000:raise HTTPException(400,'Invalid page')
        assignments=list(db.execute(select(QuizAssignment,QuizVersion.title).join(QuizVersion,QuizVersion.id==QuizAssignment.version_id).where(QuizAssignment.student_id==student_id).order_by(QuizAssignment.id.desc()).offset((page-1)*PAGE_SIZE).limit(PAGE_SIZE+1)).all())
        attempts=list(db.execute(select(QuizAttempt,QuizVersion.title).join(QuizVersion,QuizVersion.id==QuizAttempt.version_id).where(QuizAttempt.student_id==student_id,QuizAttempt.state!='submitted').order_by(QuizAttempt.started_at.desc(),QuizAttempt.id.desc()).limit(10)).all())
        context.update(quiz_assignments_page=assignments[:PAGE_SIZE],quiz_assignments_more=len(assignments)>PAGE_SIZE,quiz_page=page,quiz_attempts=attempts)
    elif section=='progress':
        page=parse_id(request.query_params.get('page','1'),'page')
        if not page or page>100000:raise HTTPException(400,'Invalid page')
        rows=list(db.scalars(select(StudentTopic).options(selectinload(StudentTopic.topic)).where(StudentTopic.student_id==student_id).order_by(StudentTopic.updated_at.desc(),StudentTopic.id.desc()).offset((page-1)*20).limit(21)))
        context.update(progress_topics=rows[:20],progress_more=len(rows)>20,progress_page=page,topic_statuses=topic_logic.STATUSES)
        context.update(topic_logic.summary(db,student_id))
        topic_ids=[row.id for row in rows[:20]]
        if topic_ids:
            ranked=select(LessonTopic.student_topic_id.label('topic_id'),LessonRecord.id.label('record_id'),
                func.row_number().over(partition_by=LessonTopic.student_topic_id,order_by=(LessonRecord.lesson_date.desc(),
                    func.coalesce(LessonRecord.lesson_time,time(0,0)).desc(),LessonRecord.id.desc())).label('rank')
            ).join(LessonRecord,LessonRecord.id==LessonTopic.lesson_record_id).where(
                LessonTopic.student_topic_id.in_(topic_ids),LessonRecord.deleted_at.is_(None)).subquery('ranked_topic_lessons')
            latest_rows=db.execute(select(ranked.c.topic_id,LessonRecord).join(LessonRecord,LessonRecord.id==ranked.c.record_id)
                .where(ranked.c.rank<=5).order_by(ranked.c.topic_id,ranked.c.rank)).all()
            latest={}
            for topic_id,record in latest_rows:latest.setdefault(topic_id,[]).append(record)
            context['latest_topic_lessons']=latest
        else:context['latest_topic_lessons']={}
    elif section=='finance':
        from ..finance import balances, rub
        from ..finance_models import FinanceOperation
        context['finance_balance']=balances(db,student_id)
        context['rub']=rub
        context['finance_operations']=list(db.scalars(select(FinanceOperation).where(FinanceOperation.student_id==student_id).order_by(FinanceOperation.id.desc()).limit(10)))
    elif section=='history':
        page=parse_id(request.query_params.get('history_page','1'),'history_page')
        if not page or page>10000:raise HTTPException(400,'Invalid page')
        timeline=union_all(*history_streams(student_id)).subquery('student_history')
        rows=list(db.execute(select(timeline).order_by(timeline.c.event_at.desc(),timeline.c.event_type,timeline.c.event_id.desc()).offset((page-1)*20).limit(21)).all())
        context.update(timeline=rows[:20],history_page=page,history_more=len(rows)>20)
        record_page=parse_id(request.query_params.get('records_page',request.query_params.get('page','1')),'records_page')
        note_page=parse_id(request.query_params.get('note_page','1'),'note_page')
        if not record_page or not note_page or max(record_page,note_page)>100000:raise HTTPException(400,'Invalid page')
        records=list(db.scalars(select(LessonRecord).where(LessonRecord.student_id==student_id,LessonRecord.deleted_at.is_(None)).order_by(LessonRecord.lesson_date.desc(),func.coalesce(LessonRecord.lesson_time,time(0,0)).desc(),LessonRecord.id.desc()).offset((record_page-1)*PAGE_SIZE).limit(PAGE_SIZE+1)))
        notes=list(db.scalars(select(StudentNote).where(StudentNote.student_id==student_id,StudentNote.deleted_at.is_(None)).order_by(StudentNote.created_at.desc(),StudentNote.id.desc()).offset((note_page-1)*PAGE_SIZE).limit(PAGE_SIZE+1)))
        context.update(records=records[:PAGE_SIZE],notes=notes[:PAGE_SIZE],records_more=len(records)>PAGE_SIZE,notes_more=len(notes)>PAGE_SIZE,records_page=record_page,note_page=note_page,creation_key=uuid4().hex)
    if section=='attendance':
        context['attendance_counts']=dict(db.execute(select(LessonAttendance.outcome,func.count()).where(LessonAttendance.student_id==student_id).group_by(LessonAttendance.outcome)).all())
    if section=='overview':
        context['student_status_events']=list(db.scalars(select(StudentStatusEvent).where(StudentStatusEvent.student_id==student_id).order_by(StudentStatusEvent.id.desc()).limit(3)))
        context['video_history']=list(db.scalars(select(VideoMeeting).where(
            VideoMeeting.student_id==student_id, VideoMeeting.teacher_id==user.id,
            VideoMeeting.state=='ended'
        ).order_by(VideoMeeting.ended_at.desc(), VideoMeeting.id.desc()).limit(10)))
    return render(request,'admin/student_card.html',user=user,student=student,status_label=STATUS_LABELS[student.learning_status],**context)
@router.get('/{student_id}/learning')
def profile_form(request: Request,student_id:int,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    student=pupil(db,student_id)
    return_section=request.query_params.get('section','overview')
    if return_section not in CARD_SECTIONS:return_section='overview'
    return render(request,'admin/learning_form.html',user=user,student=student,fields=PROFILE,
                  return_section=return_section,values={**{key:getattr(student,key) for key in PROFILE},'profile_version':student.profile_version})


@router.post('/{student_id}/learning')
async def profile_save(request: Request,student_id:int,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    student=pupil(db,student_id)
    form=await validated_form(request)
    values=dict(form)
    try:
        data={key:clean_text(form.get(key),limit,label,required=False) for key,(label,limit) in PROFILE.items()}
        expected=parse_id(form.get('profile_version'),'версии')
        result=db.execute(update(User).where(User.id==student_id,User.profile_version==expected).values(
            **data,profile_version=User.profile_version+1))
        if result.rowcount!=1:
            raise HTTPException(409,'Карточка изменена в другой вкладке. Ваш текст сохранён ниже; сначала откройте актуальную версию.')
        db.commit()
    except (HTTPException,SQLAlchemyError,OSError) as exc:
        db.rollback()
        error=exc.detail if isinstance(exc,HTTPException) else 'Не удалось сохранить. Ваш текст остался в форме. Проверьте результат перед повторной отправкой.'
        return_section=form.get('return_section','overview')
        if return_section not in CARD_SECTIONS:return_section='overview'
        response=render(request,'admin/learning_form.html',user=user,student=student,fields=PROFILE,values=values,return_section=return_section,form_error=error)
        response.status_code=exc.status_code if isinstance(exc,HTTPException) else 503
        return response
    return destination(student_id,section=form.get('return_section','overview'))


@router.get('/{student_id}/{kind}/new')
def new_record(request:Request,student_id:int,kind:str,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    model_for(kind)
    student=pupil(db,student_id)
    lesson_id=parse_id(request.query_params.get('lesson_id'),'занятия') if kind=='records' else None
    if lesson_id:
        lesson=db.get(Lesson,lesson_id)
        if not lesson or lesson.student_id!=student_id:raise HTTPException(404)
        completion=db.get(LessonCompletion,lesson_id)
        if completion:return RedirectResponse(f'/admin/students/{student_id}/records/{completion.record_id}/edit',status_code=303)
        if lesson.state!='scheduled' or lesson.starts_at>utc_now():raise HTTPException(400,'Запишите итог после начала активного занятия')
        at=scheduling.local(lesson.starts_at,lesson.timezone_name or request.app.state.settings.timezone)
        values={'lesson_date':str(at.date()),'lesson_time':at.strftime('%H:%M'),'planned_lesson_id':str(lesson_id),'return_to_schedule':'yes'}
        return form_response(request,user,student,kind,values=values)
    return form_response(request,user,student,kind)


@router.get('/{student_id}/{kind}/{record_id}/edit')
def edit_record(request:Request,student_id:int,kind:str,record_id:int,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    return form_response(request,user,pupil(db,student_id),kind,record_for(db,model_for(kind),student_id,record_id))


async def save(request,student_id,kind,db,user,record_id=None):
    model=model_for(kind)
    student=pupil(db,student_id)
    row=record_for(db,model,student_id,record_id) if record_id else None
    form=await validated_form(request)
    values=dict(form)
    if kind=='records':
        values['student_topic_ids']=form.getlist('student_topic_ids')
    key=str(form.get('creation_key',''))
    try:
        if kind=='records':scheduling.lock(db)
        if not re.fullmatch('[a-f0-9]{32}',key):
            raise HTTPException(400,'Неверный ключ формы. Откройте новую форму и перенесите сохранённый ниже текст.')
        if row is None:
            existing=db.scalar(select(model).where(model.creation_key==key))
            if existing:
                if existing.student_id!=student_id or existing.deleted_at:
                    raise HTTPException(409,'Эта форма уже использована. Откройте новую форму.')
                return destination(student_id,kind,form.get('return_section'))
        data={key:clean_text(form.get(key),4000,label,required=(kind=='notes')) for key,label in FIELDS[kind].items()}
        if kind=='records':
            planned_id=parse_id(form.get('planned_lesson_id'),'занятия')
            planned=db.get(Lesson,planned_id) if planned_id else None
            if planned_id and (not planned or planned.student_id!=student_id):raise HTTPException(400,'Занятие другого ученика недоступно')
            record_zone=row.timezone_name if row else ((planned.timezone_name or request.app.state.settings.timezone) if planned else request.app.state.settings.timezone)
            try:
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',str(form.get('lesson_date',''))):
                    raise ValueError()
                data['lesson_date']=date.fromisoformat(form['lesson_date'])
                raw=str(form.get('lesson_time',''))
                if raw and not re.fullmatch(r'\d{2}:\d{2}(:00)?',raw):
                    raise ValueError()
                data['lesson_time']=time.fromisoformat(raw) if raw else None
                if data['lesson_date']>scheduling.local(utc_now(),record_zone).date():
                    raise ValueError()
            except (ValueError,TypeError):
                raise HTTPException(400,'Укажите корректную дату состоявшегося занятия (не позже сегодняшней) и время ЧЧ:ММ либо оставьте время пустым.')
            data['timezone_name']=record_zone
        old_values={'lesson_date':str(row.lesson_date),'summary':row.topic} if row and kind=='records' else None
        if row:
            changed(db,row,model,student_id,parse_id(form.get('version'),'версии'),data,user.id,'edit')
        else:
            saved=model(student_id=student_id,creation_key=key,**data)
            db.add(saved);db.flush()
        if kind=='records':
            if row:
                db.refresh(row)
                saved=row
            topic_logic.sync_lesson(db,saved,form.getlist('student_topic_ids'),user.id,str(form.get('new_topic_title','')),str(form.get('new_topic_category','')),old_values)
            linked=parse_id(form.get('planned_lesson_id'),'занятия')
            if linked:scheduling.connect_record(db,saved,linked,user.id)
        db.commit()
    except (HTTPException,SQLAlchemyError,OSError) as exc:
        db.rollback()
        if isinstance(exc,IntegrityError) and row is None:
            existing=db.scalar(select(model).where(model.creation_key==key,model.student_id==student_id,model.deleted_at.is_(None)))
            if existing:
                    return destination(student_id,kind,form.get('return_section'))
        error=exc.detail if isinstance(exc,HTTPException) else 'Не удалось сохранить. Ваш текст остался в форме. Проверьте результат перед повторной отправкой.'
        return form_response(request,user,student,kind,row,values,error,exc.status_code if isinstance(exc,HTTPException) else 503)
    if form.get('return_to_schedule')=='yes':return RedirectResponse('/admin/lessons?notice=saved',status_code=303)
    return destination(student_id,kind,form.get('return_section'))


@router.post('/{student_id}/{kind}')
async def create_record(request:Request,student_id:int,kind:str,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    return await save(request,student_id,kind,db,user)


@router.post('/{student_id}/{kind}/{record_id}/edit')
async def update_record(request:Request,student_id:int,kind:str,record_id:int,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    return await save(request,student_id,kind,db,user,record_id)


@router.get('/{student_id}/{kind}/{record_id}/delete')
def confirm_delete(request:Request,student_id:int,kind:str,record_id:int,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    row=record_for(db,model_for(kind),student_id,record_id)
    section=request.query_params.get('section','history')
    if section not in CARD_SECTIONS:section='history'
    return render(request,'admin/teaching_delete.html',user=user,student=pupil(db,student_id),record=row,kind=kind,return_section=section)


@router.post('/{student_id}/{kind}/{record_id}/delete')
async def delete_record(request:Request,student_id:int,kind:str,record_id:int,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    model=model_for(kind)
    pupil(db,student_id)
    row=record_for(db,model,student_id,record_id)
    form=await validated_form(request)
    if form.get('confirm')!='yes':
        raise HTTPException(400,'Подтвердите удаление записи')
    if kind=='records':scheduling.lock(db)
    changed(db,row,model,student_id,parse_id(form.get('version'),'версии'),{'deleted_at':utc_now()},user.id,'delete')
    if kind=='records':
        topic_logic.unlink_deleted_lesson(db,row,user.id)
        completion=db.scalar(select(LessonCompletion).where(LessonCompletion.record_id==row.id))
        if completion:
            scheduling.event(db,db.get(Lesson,completion.lesson_id),'record_removed',user.id)
            db.delete(completion)
    db.commit()
    return destination(student_id,kind,form.get('return_section'))
