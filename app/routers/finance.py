"""Admin-only finance pages; all writes have a transactional preview."""
from uuid import uuid4
import hashlib, hmac, json
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select, func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from ..web import db_session, require_admin, validated_form, render
from ..models import User, Lesson, utc_now
from ..attendance_models import LessonAttendance
from ..finance_models import *
from .. import finance as f, attendance, scheduling
router=APIRouter(prefix='/admin/finance')


def preview_signature(request, values):
    payload={k:str(v) for k,v in values.items() if k not in {"csrf","confirm","back","preview_token"}}
    return hmac.new(request.app.state.settings.secret_key.encode(),json.dumps([request.url.path,request.session.get("uid"),payload],sort_keys=True,ensure_ascii=False).encode(),hashlib.sha256).hexdigest()

def common():return dict(rub=f.rub,kinds=f.KINDS,methods=f.METHODS,labels=attendance.LABELS,recommendations=f.RECOMMENDATIONS)

def details(db,sid,tz="Europe/Moscow"):
    today=scheduling.local(utc_now(),tz).date()
    charges=list(db.scalars(select(FinancialEntry).where(FinancialEntry.student_id==sid,FinancialEntry.kind=='charge').order_by(FinancialEntry.id.desc())))
    payments=list(db.scalars(select(FinancialEntry).where(FinancialEntry.student_id==sid,FinancialEntry.kind=='payment').order_by(FinancialEntry.id.desc())))
    packages=list(db.scalars(select(LessonPackage).where(LessonPackage.student_id==sid).order_by(LessonPackage.id.desc())))
    allocations=db.execute(select(PaymentAllocation.payment_id,PaymentAllocation.charge_id,func.sum(PaymentAllocation.amount)).where(PaymentAllocation.student_id==sid).group_by(PaymentAllocation.payment_id,PaymentAllocation.charge_id).having(func.sum(PaymentAllocation.amount)>0)).all()
    corrections=dict(db.execute(select(FinancialEntry.original_id,func.sum(FinancialEntry.amount)).where(FinancialEntry.student_id==sid,FinancialEntry.original_id.is_not(None)).group_by(FinancialEntry.original_id)).all())
    charge_alloc=dict(db.execute(select(PaymentAllocation.charge_id,func.sum(PaymentAllocation.amount)).where(PaymentAllocation.student_id==sid).group_by(PaymentAllocation.charge_id)).all())
    payment_alloc=dict(db.execute(select(PaymentAllocation.payment_id,func.sum(PaymentAllocation.amount)).where(PaymentAllocation.student_id==sid).group_by(PaymentAllocation.payment_id)).all())
    units=dict(db.execute(select(PackageEntry.package_id,func.sum(PackageEntry.quantity)).where(PackageEntry.student_id==sid).group_by(PackageEntry.package_id)).all())
    used=dict(db.execute(select(PackageEntry.package_id,func.sum(PackageEntry.quantity)).where(PackageEntry.student_id==sid,PackageEntry.kind.in_(['use','return'])).group_by(PackageEntry.package_id)).all())
    net={x.id:x.amount-corrections.get(x.id,0) for x in charges+payments}
    return dict(charges=charges,payments=payments,packages=packages,allocations=allocations,
        due={x.id:net[x.id]-charge_alloc.get(x.id,0) for x in charges},net=net,free={x.id:net[x.id]-payment_alloc.get(x.id,0) for x in payments},
        package_states={x.id:('Закрыт' if x.status=='closed' else 'Срок истёк' if x.expires_on and x.expires_on<today else 'Ещё не действует' if x.purchased_on>today else 'Занятия закончились' if units.get(x.id,0)==0 else 'Действует') for x in packages},
        remaining={x.id:units.get(x.id,0) for x in packages},used={x.id:-used.get(x.id,0) for x in packages},balance=f.balances(db,sid))

@router.get('')
def overview(request:Request,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    first,last,_,_,preset=attendance.period(request.query_params,request.app.state.settings.timezone)
    sid=f.number(request.query_params.get('student_id') or '0','ученик',0)
    if sid:f.student(db,sid)
    kind=request.query_params.get('kind','')
    if kind and kind not in f.KINDS:raise HTTPException(400,'Неизвестный тип операции')
    page=f.number(request.query_params.get('page','1'),'страница',1,100000)
    students=list(db.scalars(select(User).where(User.role=='student').order_by(User.display_name,User.id)))
    selected=[s for s in students if not sid or s.id==sid];balances=f.student_balances(db,(s.id for s in selected))
    query=select(FinanceOperation).where(FinanceOperation.effective_date>=first,FinanceOperation.effective_date<=last)
    money_query=select(FinancialEntry.kind,func.sum(FinancialEntry.amount)).join(FinanceOperation,FinancialEntry.operation_id==FinanceOperation.id).where(FinanceOperation.effective_date>=first,FinanceOperation.effective_date<=last)
    if sid:query=query.where(FinanceOperation.student_id==sid);money_query=money_query.where(FinanceOperation.student_id==sid)
    # Period indicators deliberately do not depend on history-type filter.
    totals=dict(db.execute(money_query.group_by(FinancialEntry.kind)).all())
    if kind:query=query.where(FinanceOperation.kind==kind)
    operations=list(db.scalars(query.order_by(FinanceOperation.id.desc()).offset((page-1)*30).limit(31)))
    return render(request,'admin/finance_overview.html',user=user,students=students,selected=selected,balances=balances,totals=totals,
        operations=operations[:30],more=len(operations)>30,page=page,first=first,last=last,preset=preset,student_id=sid,kind=kind,
        debt=sum(b['debt'] for b in balances.values()),advance=sum(b['advance'] for b in balances.values()),**common())

@router.get('/students/{sid}')
def student_page(request:Request,sid:int,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    student=f.student(db,sid);page=f.number(request.query_params.get('page','1'),'страница',1,100000)
    operations=list(db.scalars(select(FinanceOperation).where(FinanceOperation.student_id==sid).order_by(FinanceOperation.id.desc()).offset((page-1)*30).limit(31)))
    rates=list(db.scalars(select(StudentRate).where(StudentRate.student_id==sid).order_by(StudentRate.starts_on.desc(),StudentRate.id.desc())))
    settlements=list(db.scalars(select(LessonSettlement).where(LessonSettlement.student_id==sid,LessonSettlement.active.is_(True))))
    issues=[s for s in settlements if f.mismatch(db,s,request.app.state.settings.timezone)]
    authors={u.id:u.display_name for u in db.scalars(select(User).where(User.id.in_([op.actor_id for op in operations])))}
    policies={key:f.recommendation(db,sid,key) for key in f.DEFAULTS}
    return render(request,'admin/finance_student.html',user=user,student=student,operations=operations[:30],more=len(operations)>30,page=page,rates=rates,policies=policies,issues=issues,authors=authors,**details(db,sid,request.app.state.settings.timezone),**common())

@router.get('/lessons/{lid}')
def lesson_page(request:Request,lid:int,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    lesson=db.get(Lesson,lid)
    if not lesson:raise HTTPException(404,'Занятие не найдено')
    mark=db.get(LessonAttendance,lid)
    settlements=list(db.scalars(select(LessonSettlement).where(LessonSettlement.lesson_id==lid).order_by(LessonSettlement.id.desc())))
    current=next((s for s in settlements if s.active),None)
    return render(request,'admin/finance_lesson.html',user=user,lesson=lesson,mark=mark,current=current,settlements=settlements,
        needs_review=f.mismatch(db,current,request.app.state.settings.timezone) if current else False,rate=f.lesson_rate(db,lesson,request.app.state.settings.timezone),recommendation=f.recommendation(db,lesson.student_id,mark.outcome if mark else 'unknown'),**common())

def form_page(request,db,user,sid,values=None,error=None,code=200,preview=None):
    student=f.student(db,sid);today=scheduling.local(utc_now(),request.app.state.settings.timezone).date()
    if values is None:
        values=dict(request.query_params);values.update(creation_key=uuid4().hex,version=str(f.version(db,sid)),date=str(today))
    kind=values.get('kind','payment')
    if kind not in f.KINDS:raise HTTPException(400,'Неизвестное действие')
    values.setdefault('kind',kind)
    lesson=None;mark=None;rate=None
    if kind=='reconcile':
        saved=f.owned(db,LessonSettlement,values.get('settlement_id'),sid)
        values['lesson_id']=str(saved.lesson_id)
    if kind in {'settle','reconcile'}:
        lesson=f.owned(db,Lesson,values.get('lesson_id'),sid);mark=db.get(LessonAttendance,lesson.id);rate=f.lesson_rate(db,lesson,request.app.state.settings.timezone)
        values.setdefault('attendance_version',str(mark.version if mark else 0));values.setdefault('lesson_version',str(lesson.version))
        if rate:values.setdefault('amount',str(rate.amount//100)+f'.{rate.amount%100:02d}')
    response=render(request,'admin/finance_form.html',user=user,student=student,values=values,kind=kind,form_error=error,preview=preview,preview_token=preview_signature(request,values),lesson=lesson,mark=mark,rate=rate,**details(db,sid,request.app.state.settings.timezone),**common())
    response.status_code=code;return response

@router.get('/students/{sid}/action')
def action_get(request:Request,sid:int,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    return form_page(request,db,user,sid)

@router.post('/students/{sid}/action')
async def action_post(request:Request,sid:int,db:Session=Depends(db_session),user:User=Depends(require_admin)):
    values=dict(await validated_form(request));summary=None
    if values.get("back")=="yes":
        values.pop("back",None);values.pop("confirm",None);values.pop("preview_token",None)
        return form_page(request,db,user,sid,values)
    if values.get("confirm")=="yes" and not hmac.compare_digest(values.get("preview_token",""),preview_signature(request,values)):
        raise HTTPException(400,"Сначала проверьте последствия именно этих данных")
    values.pop("preview_token",None)
    try:
        operation=f.execute(db,sid,values,user.id,request.app.state.settings.timezone)
        if values.get('confirm')=='yes':
            db.commit();return RedirectResponse(f'/admin/finance/students/{sid}?notice=saved',303)
        summary=operation.summary
        db.rollback()
    except (HTTPException,SQLAlchemyError) as exc:
        db.rollback()
        if isinstance(exc,HTTPException) and exc.status_code in (403,404):raise
        return form_page(request,db,user,sid,values,exc.detail if isinstance(exc,HTTPException) else 'Сохранение не завершено. Поля сохранены. Проверьте историю перед повтором.',exc.status_code if isinstance(exc,HTTPException) else 503)
    return form_page(request,db,user,sid,values,preview=summary)
