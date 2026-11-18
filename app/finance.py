"""Explicit financial decisions. Every write is serialized with attendance/calendar.

Balances are derived from immutable money/allocation/package journals, never float.
Callers must commit all of execute() or roll back all of it.
"""
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib, json, re
from fastapi import HTTPException
from sqlalchemy import select, func
from .models import User, Lesson, utc_now
from .attendance_models import LessonAttendance
from .finance_models import (FinanceAccount, FinanceOperation, StudentRate, BillingPolicy,
    LessonPackage, FinancialEntry, PaymentAllocation, LessonSettlement, PackageEntry)
from . import scheduling
from .progress import creation_key
from .web import clean_text

KINDS={'rate':'Тариф','policy':'Правило оплаты','payment':'Получена оплата','package':'Покупка абонемента',
 'settle':'Расчёт занятия','reconcile':'Подтверждение сверки','reverse':'Отмена расчёта','allocate':'Распределение оплаты','release':'Снятие распределения',
 'reallocate':'Перераспределение','refund':'Возврат денег','credit':'Уменьшение начисления',
 'payment_void':'Исправление оплаты','package_adjust':'Корректировка занятий','package_close':'Закрытие абонемента','package_extend':'Изменение срока'}
METHODS={'charge':'Начислить оплату','package':'Использовать абонемент','free':'Без оплаты','defer':'Решить позже'}
DEFAULTS={'conducted':'charge','cancelled_teacher':'free','teacher_absent':'free','cancelled_student':'manual','student_absent':'manual','other':'manual','unknown':'manual'}
RECOMMENDATIONS={'charge':'Предложить оплату','free':'Предложить без оплаты','manual':'Ручное решение'}
MAX_MONEY=100000000000

def fail(message,code=400):raise HTTPException(code,message)

def money(raw):
    value=str(raw or '').strip().replace(',','.')
    if not re.fullmatch(r'\d{1,10}(?:\.\d{1,2})?',value):fail('Сумма: положительное число рублей, не более двух знаков после запятой.')
    try:result=int(Decimal(value)*100)
    except (InvalidOperation,ValueError):fail('Проверьте сумму')
    if not 0<result<=MAX_MONEY:fail('Сумма должна быть больше нуля и не больше 1 000 000 000 рублей.')
    return result

def rub(value):
    sign='−' if value<0 else '';value=abs(value)
    return f'{sign}{value//100:,}'.replace(',',' ')+f',{value%100:02d} ₽'

def number(raw,label,low=1,high=2147483647):
    try:value=int(str(raw))
    except (ValueError,TypeError):fail('Проверьте поле: '+label)
    if not low<=value<=high:fail('Недопустимое значение: '+label)
    return value

def day(raw,label='дата'):
    try:return date.fromisoformat(str(raw))
    except (ValueError,TypeError):fail('Проверьте поле: '+label)

def student(db,sid):
    user=db.get(User,sid)
    if not user or user.role!='student':fail('Ученик не найден',404)
    return user

def owned(db,model,raw,sid):
    row=db.get(model,number(raw,'идентификатор'))
    if not row or row.student_id!=sid:fail('Запись не найдена у этого ученика',404)
    return row

def version(db,sid):
    row=db.get(FinanceAccount,sid);return row.version if row else 0

def sum_of(db,model,column,*where):return db.scalar(select(func.coalesce(func.sum(column),0)).select_from(model).where(*where)) or 0

def allocated(db,payment=None,charge=None):
    filters=[]
    if payment is not None:filters.append(PaymentAllocation.payment_id==payment)
    if charge is not None:filters.append(PaymentAllocation.charge_id==charge)
    return sum_of(db,PaymentAllocation,PaymentAllocation.amount,*filters)

def net(db,entry):
    return entry.amount-sum_of(db,FinancialEntry,FinancialEntry.amount,FinancialEntry.original_id==entry.id)

def available(db,payment):return net(db,payment)-allocated(db,payment=payment.id)
def due(db,charge):return net(db,charge)-allocated(db,charge=charge.id)
def remaining(db,package):return sum_of(db,PackageEntry,PackageEntry.quantity,PackageEntry.package_id==package.id)

def student_balances(db, student_ids):
    ids = tuple(student_ids)
    if not ids:
        return {}
    entries = select(FinancialEntry.student_id, FinancialEntry.kind, func.sum(FinancialEntry.amount))
    entries = entries.where(FinancialEntry.student_id.in_(ids)).group_by(FinancialEntry.student_id, FinancialEntry.kind)
    totals = {sid: {} for sid in ids}
    for sid, kind, amount in db.execute(entries):
        totals[sid][kind] = amount
    allocations = select(PaymentAllocation.student_id, func.sum(PaymentAllocation.amount))
    assigned = dict(db.execute(allocations.where(PaymentAllocation.student_id.in_(ids)).group_by(PaymentAllocation.student_id)).all())
    return {sid: {
        "debt": sums.get("charge", 0) - sums.get("credit", 0) - assigned.get(sid, 0),
        "advance": sums.get("payment", 0) - sums.get("refund", 0) - sums.get("payment_void", 0) - assigned.get(sid, 0),
    } for sid, sums in totals.items()}


def balances(db, sid):
    return student_balances(db, [sid])[sid]


def select_rate(db,sid,on,duration):
    rows=list(db.scalars(select(StudentRate).where(StudentRate.student_id==sid,StudentRate.starts_on<=on).order_by(StudentRate.starts_on.desc(),StudentRate.id.desc())))
    for scope in (duration,None):
        for row in rows:
            if row.duration_minutes==scope:return row
    return None

def recommendation(db,sid,outcome):
    row=db.scalar(select(BillingPolicy).where(BillingPolicy.student_id==sid,BillingPolicy.outcome==outcome).order_by(BillingPolicy.id.desc()).limit(1))
    return row.recommendation if row else DEFAULTS.get(outcome,'manual')

def lesson_rate(db,lesson,tz='Europe/Moscow'):return select_rate(db,lesson.student_id,scheduling.local(lesson.starts_at,lesson.timezone_name or tz).date(),lesson.duration_minutes)

def mismatch(db,settlement,tz='Europe/Moscow'):
    lesson=db.get(Lesson,settlement.lesson_id);a=db.get(LessonAttendance,lesson.id);rate=lesson_rate(db,lesson,tz)
    return (not a or a.outcome!=settlement.outcome or a.actual_minutes!=settlement.actual_minutes
        or lesson.starts_at!=settlement.starts_at or lesson.duration_minutes!=settlement.planned_minutes
        or (settlement.rate_id is not None and (not rate or rate.id!=settlement.rate_id))
        or (settlement.charge_id is not None and net(db,db.get(FinancialEntry,settlement.charge_id))!=settlement.amount_snapshot))

def entry(db,op,kind,amount,**kw):
    row=FinancialEntry(student_id=op.student_id,operation_id=op.id,kind=kind,amount=amount,**kw);db.add(row);db.flush();return row

def allocation(db,op,pay,charge,amount):
    if pay.kind!='payment' or charge.kind!='charge':fail('Нужны платёж и начисление')
    if op.effective_date<max(db.get(FinanceOperation,pay.operation_id).effective_date,db.get(FinanceOperation,charge.operation_id).effective_date):fail('Распределение не может быть раньше исходного платежа и обязательства')
    if amount>0 and (amount>available(db,pay) or amount>due(db,charge)):fail('Недостаточно свободных денег или сумма превышает остаток обязательства',409)
    if amount<0 and -amount>sum_of(db,PaymentAllocation,PaymentAllocation.amount,PaymentAllocation.payment_id==pay.id,PaymentAllocation.charge_id==charge.id):fail('Такой суммы в выбранном распределении нет',409)
    db.add(PaymentAllocation(student_id=op.student_id,operation_id=op.id,payment_id=pay.id,charge_id=charge.id,amount=amount));db.flush()

def release_needed(db,op,target,amount):
    """Release newest charge/payment pairs only as needed; preview lists every release."""
    is_payment=target.kind=='payment'
    free=available(db,target) if is_payment else due(db,target)
    needed=max(0,amount-free)
    column=PaymentAllocation.charge_id if is_payment else PaymentAllocation.payment_id
    condition=PaymentAllocation.payment_id==target.id if is_payment else PaymentAllocation.charge_id==target.id
    pairs=db.execute(select(column,func.sum(PaymentAllocation.amount)).where(condition).group_by(column).order_by(column.desc())).all()
    for ident,balance in pairs:
        part=min(needed,max(0,balance))
        if part:
            other=db.get(FinancialEntry,ident)
            allocation(db,op,target if is_payment else other,other if is_payment else target,-part);needed-=part
    if needed:fail('Распределения требуют сверки',409)

def reduce_entry(db,op,original,amount,kind):
    expected='charge' if kind=='credit' else 'payment'
    if original.kind!=expected:fail('Неверный тип исходной операции')
    if op.effective_date<db.get(FinanceOperation,original.operation_id).effective_date:fail('Исправление или возврат не может быть раньше исходной операции')
    if amount>net(db,original):fail('Сумма больше оставшейся суммы исходной операции',409)
    release_needed(db,op,original,amount)
    return entry(db,op,kind,amount,original_id=original.id,lesson_id=original.lesson_id,package_id=original.package_id)

def execute(db,sid,form,actor,tz='Europe/Moscow'):
    """One atomic command. No internal commit, network calls or automatic settlements."""
    scheduling.lock(db);db.expire_all();student(db,sid)
    admin=db.get(User,actor)
    if not admin or admin.role!='admin' or not admin.active:fail('Нет доступа',403)
    key=creation_key(form.get('creation_key'))
    fields={k:str(v) for k,v in form.items() if k not in {'csrf','confirm'}}
    digest=hashlib.sha256(json.dumps([sid,actor,fields],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    previous=db.scalar(select(FinanceOperation).where(FinanceOperation.request_key==key))
    if previous:
        if previous.request_hash!=digest:fail('Ключ формы уже использован с другими данными',409)
        return previous
    expected=number(form.get('version'),'версия',0)
    if expected!=version(db,sid):fail('Финансовая карточка изменилась. Поля сохранены; откройте актуальную форму и проверьте остатки.',409)
    kind=form.get('kind','')
    if kind not in KINDS:fail('Неизвестное действие')
    today=scheduling.local(utc_now(),tz).date()
    on=day(form.get('date'))
    if kind!='rate' and on>today:fail('Финансовая операция не может быть датирована будущим')
    reason=clean_text(form.get('reason'),1000,'Комментарий / причина',kind not in {'payment','package','rate','settle'})
    account=db.get(FinanceAccount,sid)
    if not account:account=FinanceAccount(student_id=sid,version=0);db.add(account)
    account.version+=1
    op=FinanceOperation(student_id=sid,actor_id=actor,effective_date=on,kind=kind,reason=reason,request_key=key,request_hash=digest,result_version=account.version)
    db.add(op);db.flush();notes=[]
    duration=number(form['duration'],'длительность',1,1440) if form.get('duration') else None
    if kind=='rate':
        amount=money(form.get('amount'))
        db.add(StudentRate(student_id=sid,operation_id=op.id,amount=amount,duration_minutes=duration,starts_on=on))
        notes.append(f'Тариф {rub(amount)}, с {on}, длительность: {duration or "любая"}. Прежние начисления остаются прежними.')
    elif kind=='policy':
        outcome=form.get('outcome');rec=form.get('recommendation')
        if outcome not in DEFAULTS or rec not in RECOMMENDATIONS:fail('Проверьте правило оплаты')
        db.add(BillingPolicy(student_id=sid,operation_id=op.id,outcome=outcome,recommendation=rec))
        notes.append(f'Правило {outcome}: {RECOMMENDATIONS[rec]}. Только рекомендация.')
    elif kind=='payment':
        pay=entry(db,op,'payment',money(form.get('amount')))
        if form.get('charge_id'):
            charge=owned(db,FinancialEntry,form['charge_id'],sid)
            allocation(db,op,pay,charge,min(pay.amount,due(db,charge))) if due(db,charge)>0 else fail('Обязательство уже оплачено')
    elif kind=='package':
        qty=number(form.get('quantity'),'количество',1,10000);amount=money(form.get('amount'))
        expiry=day(form['expires']) if form.get('expires') else None
        if expiry and expiry<on:fail('Срок окончания раньше покупки')
        package=LessonPackage(student_id=sid,operation_id=op.id,title=clean_text(form.get('title'),160,'Название'),quantity=qty,price=amount,purchased_on=on,expires_on=expiry,duration_minutes=duration)
        db.add(package);db.flush()
        entry(db,op,'charge',amount,package_id=package.id)
        db.add(PackageEntry(student_id=sid,package_id=package.id,operation_id=op.id,kind='grant',quantity=qty))
        notes.append(f'Абонемент «{package.title}»: {qty} занятий. Оплата регистрируется отдельно.')
    elif kind in {'allocate','release','reallocate'}:
        pay=owned(db,FinancialEntry,form.get('payment_id'),sid);charge=owned(db,FinancialEntry,form.get('charge_id'),sid);amount=money(form.get('amount'))
        allocation(db,op,pay,charge,-amount if kind in {'release','reallocate'} else amount)
        if kind=='reallocate':
            target=owned(db,FinancialEntry,form.get('target_id'),sid)
            if target.id==charge.id:fail('Выберите другое обязательство')
            allocation(db,op,pay,target,amount)
        notes.append('Распределение денег изменено. Фактического возврата нет.')
    elif kind in {'refund','credit','payment_void'}:
        original=owned(db,FinancialEntry,form.get('entry_id'),sid)
        reduce_entry(db,op,original,money(form.get('amount')),kind)
        notes.append('Занятия и остатки абонементов не изменяются.')
    elif kind=='settle':
        lesson=owned(db,Lesson,form.get('lesson_id'),sid);a=db.get(LessonAttendance,lesson.id)
        if not a or a.outcome=='unknown':fail('Сначала отметьте фактический результат занятия')
        if lesson.state=='suppressed':fail('Встреча исключена из расписания')
        if number(form.get('attendance_version'),'версия посещаемости')!=a.version or number(form.get('lesson_version'),'версия занятия')!=lesson.version:fail('Посещаемость или расписание изменились. Обновите форму.',409)
        if db.scalar(select(LessonSettlement.id).where(LessonSettlement.lesson_id==lesson.id,LessonSettlement.active.is_(True))):fail('У занятия уже есть расчёт. Сначала явно отмените его.',409)
        method=form.get('method');rate=lesson_rate(db,lesson,tz);rec=recommendation(db,sid,a.outcome)
        if method not in METHODS:fail('Выберите способ расчёта')
        override=(rec=='free' and method in {'charge','package'}) or (rec=='charge' and method=='free')
        settlement=LessonSettlement(student_id=sid,lesson_id=lesson.id,operation_id=op.id,method=method,outcome=a.outcome,actual_minutes=a.actual_minutes,planned_minutes=lesson.duration_minutes,starts_at=lesson.starts_at)
        if method=='charge':
            amount=money(form.get('amount'))
            if rate and amount==rate.amount:settlement.rate_id=rate.id
            else:override=True
            charge=entry(db,op,'charge',amount,lesson_id=lesson.id);settlement.charge_id=charge.id;settlement.amount_snapshot=amount
        if override and not reason:fail('Для решения, отличающегося от рекомендации или тарифа, укажите причину')
        if method=='package':
            package=owned(db,LessonPackage,form.get('package_id'),sid)
            lesson_day=scheduling.local(lesson.starts_at,lesson.timezone_name or tz).date()
            if package.status!='active' or remaining(db,package)<1:fail('Абонемент закрыт или исчерпан',409)
            if package.purchased_on>lesson_day or (package.expires_on and (lesson_day>package.expires_on or today>package.expires_on)):fail('Абонемент не действует на эту дату. Измените срок отдельно.')
            if package.duration_minutes and package.duration_minutes!=lesson.duration_minutes:fail('Длительность занятия не соответствует абонементу')
            charge=db.scalar(select(FinancialEntry).where(FinancialEntry.package_id==package.id,FinancialEntry.kind=='charge'))
            if due(db,charge)>0 and form.get('allow_unpaid')!='yes':fail('Подтвердите использование неоплаченного абонемента')
            settlement.package_id=package.id
        db.add(settlement);db.flush()
        if method=='package':db.add(PackageEntry(student_id=sid,package_id=package.id,operation_id=op.id,settlement_id=settlement.id,kind='use',quantity=-1))
        notes.append(f'Занятие №{lesson.id}: {METHODS[method]}. Посещаемость и расписание не меняются.')
    elif kind=='reconcile':
        old=owned(db,LessonSettlement,form.get('settlement_id'),sid)
        if not old.active:fail('Расчёт уже изменён',409)
        lesson=db.get(Lesson,old.lesson_id);mark=db.get(LessonAttendance,lesson.id)
        if not mark or mark.outcome=='unknown':fail('Сначала уточните результат посещаемости или отмените расчёт')
        if number(form.get('attendance_version'),'версия посещаемости')!=mark.version or number(form.get('lesson_version'),'версия занятия')!=lesson.version:fail('Данные занятия изменились; повторите сверку',409)
        old.active=False;db.flush()
        rate=lesson_rate(db,lesson,tz)
        amount=net(db,db.get(FinancialEntry,old.charge_id)) if old.charge_id else None
        row=LessonSettlement(student_id=sid,lesson_id=lesson.id,operation_id=op.id,method=old.method,
            charge_id=old.charge_id,package_id=old.package_id,rate_id=rate.id if rate and amount==rate.amount else None,
            outcome=mark.outcome,actual_minutes=mark.actual_minutes,planned_minutes=lesson.duration_minutes,starts_at=lesson.starts_at,amount_snapshot=amount)
        db.add(row)
        notes.append(f'Сверка занятия №{lesson.id}: сохраняется решение «{METHODS[old.method]}» после изменения исходных данных. Прежний снимок №{old.id} сохранён. Деньги и количество занятий не меняются.')
    elif kind=='reverse':
        settlement=owned(db,LessonSettlement,form.get('settlement_id'),sid)
        if not settlement.active:fail('Расчёт уже отменён',409)
        if on<db.get(FinanceOperation,settlement.operation_id).effective_date:fail('Отмена расчёта не может предшествовать его созданию')
        settlement.active=False
        if settlement.charge_id:
            charge=db.get(FinancialEntry,settlement.charge_id);amount=net(db,charge)
            if amount:reduce_entry(db,op,charge,amount,'credit')
        if settlement.package_id:db.add(PackageEntry(student_id=sid,package_id=settlement.package_id,operation_id=op.id,settlement_id=settlement.id,kind='return',quantity=1))
        notes.append(f'Расчёт №{settlement.id} отменён; исходная запись сохранена. Деньги не возвращаются автоматически.')
    elif kind in {'package_adjust','package_close','package_extend'}:
        package=owned(db,LessonPackage,form.get('package_id'),sid)
        if on<package.purchased_on:fail('Изменение абонемента не может быть раньше покупки')
        if package.status=='closed' and kind!='package_adjust':fail('Абонемент уже закрыт; его история и остаток сохранены',409)
        if kind=='package_close':
            package.status='closed';notes.append(f'Абонемент №{package.id} закрыт. Неиспользовано: {remaining(db,package)}. Деньги и обязательство не меняются.')
        elif kind=='package_adjust':
            qty=number(form.get('quantity'),'изменение количества',-10000,10000)
            if qty==0 or not 0<=remaining(db,package)+qty<=10000:fail('Корректировка должна оставлять от 0 до 10 000 занятий')
            db.add(PackageEntry(student_id=sid,package_id=package.id,operation_id=op.id,kind='adjust',quantity=qty));notes.append(f'Абонемент №{package.id}: {qty:+d} занятий. Денежные операции отдельно.')
        else:
            expires=day(form['expires']) if form.get('expires') else None
            if expires and expires<package.purchased_on:fail('Срок раньше покупки')
            notes.append(f'Абонемент №{package.id}: срок {package.expires_on or "без ограничения"} → {expires or "без ограничения"}.')
            package.expires_on=expires
    db.flush()
    for e in db.scalars(select(FinancialEntry).where(FinancialEntry.operation_id==op.id)):
        notes.append(f'{KINDS.get(e.kind,"Начисление" if e.kind=="charge" else e.kind)}: {rub(e.amount)}; запись №{e.id}'+(f', исходная №{e.original_id}' if e.original_id else ''))
    for alloc in db.scalars(select(PaymentAllocation).where(PaymentAllocation.operation_id==op.id)):
        notes.append(f'Платёж №{alloc.payment_id} → обязательство №{alloc.charge_id}: {rub(alloc.amount)} (распределение).')
    for item in db.scalars(select(PackageEntry).where(PackageEntry.operation_id==op.id)):
        notes.append(f'Абонемент №{item.package_id}: {item.quantity:+d} занятий ({item.kind}).')
    current=balances(db,sid)
    if current['debt']<0 or current['advance']<0:fail('Остатки требуют сверки',409)
    notes.append(f'После операции: долг {rub(current["debt"])}, свободная предоплата {rub(current["advance"])}.')
    op.summary='\n'.join(notes);db.flush();return op
