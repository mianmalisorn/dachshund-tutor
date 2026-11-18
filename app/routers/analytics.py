"""Administrator-only, live pedagogical analytics and printable reports."""
from urllib.parse import urlencode
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..analytics import build, teacher_actions
from ..attendance import CATEGORY_LABELS, period as validate_period
from ..logic import STATUS_LABELS, to_local
from ..models import Topic, User, utc_now
from ..quiz_models import QuizVersion, QuizVersionTopic
from ..progress import STATUSES
from ..quizzes import display
from ..web import TEMPLATES, db_session, render, require_admin

router = APIRouter()

ACTION_TITLES = {
    "homework": "Домашние работы на проверку",
    "quizzes": "Тесты на ручную оценку",
    "overdue": "Просроченные задания без сдачи",
    "attendance": "Прошедшие занятия без результата",
    "absences": "Повторные документированные пропуски",
}
ACTION_KEYS = {
    "homework": "pending_homework",
    "quizzes": "quiz_reviews",
    "overdue": "overdue_homework",
    "attendance": "missing_attendance",
    "absences": "repeated_absences",
}
ACTION_FILTERS = {"all", *ACTION_TITLES}


def action_filters(db, params, student_id, now):
    kind = params.get("action_type", "all") or "all"
    state = params.get("action_status", "all") or "all"
    subject = params.get("subject", "").strip()[:120]
    raw_topic = params.get("topic_id", "").strip()
    if kind not in ACTION_FILTERS or state not in {"all", "pending", "overdue"}:
        raise HTTPException(400, "Проверьте фильтр действий")
    if raw_topic and (not raw_topic.isascii() or not raw_topic.isdigit() or len(raw_topic) > 10):
        raise HTTPException(400, "Проверьте тему")
    topic_id = int(raw_topic) if raw_topic else None
    actions = teacher_actions(db, now, student_id, subject=subject or None, topic_id=topic_id)
    if state == "pending":
        actions["overdue_homework"] = []
    elif state == "overdue":
        actions["pending_homework"] = []
    subjects = list(db.scalars(select(QuizVersion.subject_title).where(
        QuizVersion.state == "published", QuizVersion.subject_title.is_not(None), QuizVersion.subject_title != ""
    ).distinct().order_by(QuizVersion.subject_title)))
    topics = list(db.execute(select(Topic.id, Topic.title).join(
        QuizVersionTopic, QuizVersionTopic.topic_id == Topic.id
    ).join(QuizVersion, QuizVersion.id == QuizVersionTopic.version_id).where(
        QuizVersion.state == "published"
    ).distinct().order_by(Topic.title)))
    return actions, dict(action_type=kind, action_status=state, subject=subject,
                         topic_id=topic_id, subjects=subjects, topics=topics)


def page(request, db, admin, student_id=None, report=False):
    student = None
    if student_id is not None:
        if not 1 <= student_id <= 2**63 - 1:
            raise HTTPException(404, "Ученик не найден")
        student = db.execute(select(User.id, User.display_name, User.learning_status)
                             .where(User.id == student_id, User.role == "student")).one_or_none()
        if student is None:
            raise HTTPException(404, "Ученик не найден")
    tz = request.app.state.settings.timezone
    # Start a consistent read snapshot even with sqlite3 legacy transaction mode.
    connection = db.connection()
    if not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN")
    data = build(db, request.query_params, tz, student_id)
    filters = None
    if not report:
        actions, filters = action_filters(db, request.query_params, student_id, data["generated"])
    else:
        actions = None
    action_metrics = None
    if actions is not None:
        action_metrics = {
            "homework_students": len({row.student_id for row in actions["pending_homework"]}),
            "quiz_students": len({row[0].student_id for row in actions["quiz_reviews"]}),
            "overdue_students": len({row.student_id for row in actions["overdue_homework"]}),
            "attendance_students": len({row[0].student_id for row in actions["missing_attendance"]}),
        }
    action_query = ""
    if filters is not None:
        query_values = {"period": data["preset"], "from": data["first"].isoformat(), "to": data["last"].isoformat()}
        if student_id is not None:
            query_values["student_id"] = student_id
        if filters["action_status"] != "all":
            query_values["action_status"] = filters["action_status"]
        if filters["action_type"] != "all":
            query_values["action_type"] = filters["action_type"]
        if filters["subject"]:
            query_values["subject"] = filters["subject"]
        if filters["topic_id"] is not None:
            query_values["topic_id"] = filters["topic_id"]
        action_query = urlencode(query_values)
    students = [] if report else db.execute(select(User.id,User.display_name,User.learning_status)
        .where(User.role == "student").order_by(User.display_name,User.id)).all()
    context = dict(data=data, student=student, actions=actions, action_metrics=action_metrics,
                  action_filters=filters, action_query=action_query,
                  students=students, printable=report, categories=CATEGORY_LABELS,
                  homework_labels=STATUS_LABELS, topic_statuses=STATUSES, points=display)
    db.commit()
    if report:
        context["localtime"] = lambda stamp: to_local(stamp, tz)
        return TEMPLATES.TemplateResponse(request=request, name="admin/analytics.html", context=context)
    return render(request,"admin/analytics.html",user=admin,**context)


@router.get("/admin/analytics")
def overview(request: Request, student_id: str = "", db: Session = Depends(db_session),
             admin: User = Depends(require_admin)):
    if student_id and (not student_id.isascii() or not student_id.isdigit() or len(student_id)>10):
        raise HTTPException(400, "Проверьте ученика")
    return page(request, db, admin, int(student_id) if student_id else None)


@router.get("/admin/analytics/students/{student_id}")
def individual(request: Request, student_id: int, db: Session = Depends(db_session),
               admin: User = Depends(require_admin)):
    return page(request, db, admin, student_id)


@router.get("/admin/analytics/students/{student_id}/report")
def report(request: Request, student_id: int, db: Session = Depends(db_session),
           admin: User = Depends(require_admin)):
    return page(request, db, admin, student_id, report=True)


@router.get("/admin/analytics/actions/{kind}")
def action_list(request: Request, kind: str, student_id: str = "",
                db: Session = Depends(db_session), admin: User = Depends(require_admin)):
    if kind not in ACTION_TITLES:
        raise HTTPException(404)
    sid = None
    if student_id:
        if not student_id.isascii() or not student_id.isdigit() or len(student_id) > 10:
            raise HTTPException(400, "Проверьте ученика")
        sid = int(student_id)
        student = db.execute(select(User.id, User.display_name, User.learning_status)
                             .where(User.id == sid, User.role == "student")).one_or_none()
        if student is None:
            raise HTTPException(404, "Ученик не найден")
    else:
        student = None
    tz = request.app.state.settings.timezone
    first, last, _, _, preset = validate_period(request.query_params, tz)
    now = utc_now()
    rows, filters = action_filters(db, request.query_params, sid, now)
    back_params = {"period": preset}
    if preset == "custom":
        back_params.update({"from": first.isoformat(), "to": last.isoformat()})
    if sid is not None:
        back_params["student_id"] = sid
    if filters["action_type"] != "all":
        back_params["action_type"] = filters["action_type"]
    if filters["action_status"] != "all":
        back_params["action_status"] = filters["action_status"]
    if filters["subject"]:
        back_params["subject"] = filters["subject"]
    if filters["topic_id"] is not None:
        back_params["topic_id"] = filters["topic_id"]
    back_url = "/admin/analytics" + ("?" + urlencode(back_params) if back_params else "")
    return render(request, "admin/analytics_actions.html", user=admin,
                      kind=kind, title=ACTION_TITLES[kind], rows=rows[ACTION_KEYS[kind]],
                      student=student, back_url=back_url, generated=now, tz=tz,
                      filters=filters)

