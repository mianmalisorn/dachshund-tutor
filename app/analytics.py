"""Live pedagogical reports. All queries project educational fields explicitly."""
from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, literal, select, union_all
from sqlalchemy.orm import selectinload

from . import attendance
from .models import (
    Assignment, AssignmentTarget, Lesson, LessonRecord, LessonTopic, ReviewEvent,
    ScheduleEvent, StudentTopic, Submission, Topic, TopicEvent, User, utc_now,
)
from .attendance_models import AttendanceEvent, LessonAttendance
from .quiz_models import QuizAnswer, QuizAssignment, QuizAttempt, QuizGradeEvent, QuizVersion, QuizVersionTopic
from .scheduling import local
from .logic import status as homework_status


def percent(numerator, denominator):
    if not denominator:
        return None
    return rounded(Decimal(numerator) * 100 / Decimal(denominator))


def rounded(value):
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def scoped(query, column, student_id):
    return query.where(column == student_id) if student_id is not None else query


def within(column, start, end):
    return (column >= start) & (column < end)


def attendance_summary(db, student_id, start, end, now, tz):
    query = select(Lesson.starts_at, Lesson.duration_minutes, Lesson.state,
                   LessonAttendance.outcome).outerjoin(
        LessonAttendance, LessonAttendance.lesson_id == Lesson.id
    ).where(within(Lesson.starts_at, start, end), Lesson.state != "suppressed")
    counts = Counter()
    weekly = defaultdict(Counter)
    for lesson in db.execute(scoped(query, Lesson.student_id, student_id)):
        outcome = lesson.outcome
        if not outcome or outcome == "unknown":
            if lesson.state == "cancelled":
                outcome = "cancelled_unknown"
            elif lesson.starts_at + timedelta(minutes=lesson.duration_minutes) > now:
                outcome = "not_finished"
            else:
                outcome = "unknown"
        counts[outcome] += 1
        day = local(lesson.starts_at, tz).date()
        week = day - timedelta(days=day.weekday())
        weekly[week][outcome] += 1
    moves = select(func.count(func.distinct(ScheduleEvent.lesson_id))).join(
        Lesson, Lesson.id == ScheduleEvent.lesson_id
    ).where(ScheduleEvent.kind == "move", within(ScheduleEvent.created_at, start, end),
            func.json_extract(ScheduleEvent.before_json,"$.starts_at") !=
            func.json_extract(ScheduleEvent.after_json,"$.starts_at"))
    denominator = counts["conducted"] + counts["student_absent"]
    trend = []
    for week, values in sorted(weekly.items()):
        evaluated = values["conducted"] + values["student_absent"]
        trend.append(dict(week=week, conducted=values["conducted"], absent=values["student_absent"],
                          evaluated=evaluated, rate=percent(values["conducted"], evaluated)))
    return dict(counts=counts, planned=sum(counts.values()),
                moved=db.scalar(scoped(moves, Lesson.student_id, student_id)),
                denominator=denominator, rate=percent(counts["conducted"], denominator), trend=trend)


def deadline_state(due, first_submission, snapshot, now):
    if first_submission is None:
        overdue = due is not None and due < now
        return overdue, overdue
    return False, snapshot is not None and first_submission > snapshot


def homework_summary(db, student_id, start, end, now, tz):
    targets = scoped(select(AssignmentTarget.id, Assignment.title, Assignment.due_at, Assignment.created_at)
                     .join(Assignment).where(within(Assignment.created_at, start, end)),
                     AssignmentTarget.student_id, student_id).subquery()
    submissions = select(Submission.id, Submission.target_id, Submission.created_at,
                         Submission.review_state, Submission.due_at_snapshot).join(
        targets, targets.c.id == Submission.target_id
    ).order_by(Submission.created_at, Submission.id)
    by_target = defaultdict(list)
    for row in db.execute(submissions):
        by_target[row.target_id].append(row)
    history = defaultdict(list)
    if student_id:
        reviews = select(Submission.target_id, ReviewEvent.created_at, ReviewEvent.state).join(
            Submission, Submission.id == ReviewEvent.submission_id
        ).join(targets, targets.c.id == Submission.target_id).order_by(ReviewEvent.created_at, ReviewEvent.id)
        for row in db.execute(reviews):
            history[row.target_id].append((row.created_at, row.state))
    counts = Counter()
    details = []
    weekly = defaultdict(Counter)
    for target in db.execute(select(targets).order_by(targets.c.id)):
        sends = by_target[target.id]
        first = sends[0] if sends else None
        state = sends[-1].review_state if sends else "todo"
        overdue, missed = deadline_state(target.due_at, first.created_at if first else None,
                                         first.due_at_snapshot if first else None, now)
        counts["issued"] += 1
        counts["submitted"] += bool(sends)
        counts[state] += 1
        counts["overdue"] += overdue
        counts["missed"] += missed
        counts["missing_snapshot"] += bool(first and first.due_at_snapshot is None)
        day = local(target.created_at, tz).date()
        week = day - timedelta(days=day.weekday())
        weekly[week]["issued"] += 1
        weekly[week]["submitted"] += bool(sends)
        if student_id:
            details.append(dict(title=target.title, due=target.due_at, state="overdue" if overdue else state,
                                submissions=[row.created_at for row in sends], reviews=history[target.id]))
    trend = [dict(week=week, issued=values["issued"], submitted=values["submitted"],
                  rate=percent(values["submitted"], values["issued"])) for week, values in sorted(weekly.items())]
    return dict(counts=counts, details=details, rate=percent(counts["submitted"], counts["issued"]), trend=trend)


def quiz_summary(db, student_id, start, end, now):
    issued = select(func.count()).select_from(QuizAssignment).where(within(QuizAssignment.created_at, start, end))
    counts = Counter(assigned=db.scalar(scoped(issued, QuizAssignment.student_id, student_id)))
    query = select(QuizAttempt.assignment_id, QuizAttempt.number, QuizAttempt.state, QuizAttempt.started_at,
                   QuizAttempt.submitted_at, QuizAttempt.expires_at, QuizAttempt.total_points,
                   QuizAttempt.max_points, QuizVersion.id.label("version_id"), QuizVersion.title,
                   QuizVersion.number.label("version_number"), QuizVersion.subject_title).join(
        QuizVersion, QuizVersion.id == QuizAttempt.version_id
    ).where(within(QuizAttempt.started_at, start, end)).order_by(QuizAttempt.assignment_id, QuizAttempt.number)
    assignments = {}
    for row in db.execute(scoped(query, QuizAttempt.student_id, student_id)):
        counts["started"] += 1
        counts[row.state] += 1
        if row.state == "in_progress" and row.expires_at and row.expires_at <= now:
            counts["awaiting_finalization"] += 1
        group = assignments.setdefault(row.assignment_id, dict(title=row.title, version=row.version_number,
            version_id=row.version_id, subject=row.subject_title, activity=row.started_at, used=0, results=[], last=None, best=None))
        group["used"] += 1
        group["activity"] = max(group["activity"], row.started_at)
        if row.state == "checked":
            ratio = Decimal(row.total_points) * 100 / row.max_points
            group["last"] = ratio
            group["best"] = max(group["best"], ratio) if group["best"] is not None else ratio
            group["results"].append(dict(number=row.number, date=row.submitted_at, points=row.total_points,
                                         maximum=row.max_points, percent=rounded(ratio)))
    results = [g["last"] for g in assignments.values() if g["last"] is not None]
    mean = rounded(sum(results) / len(results)) if len(results) >= 3 else None
    subjects = defaultdict(list)
    for group in assignments.values():
        if group["subject"] and group["last"] is not None:
            subjects[group["subject"]].append(group["last"])
    trend = []
    for group in assignments.values():
        if group["last"] is not None:
            group["last"] = rounded(group["last"])
            group["best"] = rounded(group["best"])
            trend.append((group["results"][-1]["date"], group["title"], group["last"]))
    return dict(counts=counts, groups=list(assignments.values()), sample=len(results), mean=mean, trend=sorted(trend),
                subjects={name:(len(values), rounded(sum(values)/len(values)) if len(values)>=3 else None)
                          for name, values in subjects.items()})


def topic_summary(db, student_id, first, last, start, end, quizzes, tz):
    topics = {}
    def topic(key, title):
        return topics.setdefault(key, dict(title=title, status=None, lessons=[], results=[], activity=None))

    def activity(item, day):
        if item["activity"] is None or item["activity"] < day:
            item["activity"] = day

    links = select(StudentTopic.topic_id, Topic.title, StudentTopic.status, StudentTopic.updated_at).join(
        Topic, Topic.id == StudentTopic.topic_id).where(StudentTopic.student_id == student_id)
    for row in db.execute(links):
        item = topic(row.topic_id, row.title)
        item["status"] = row.status
        if start <= row.updated_at < end:
            activity(item, local(row.updated_at,tz).date())

    changes = select(StudentTopic.topic_id,func.max(TopicEvent.created_at)).join(
        TopicEvent, TopicEvent.student_topic_id == StudentTopic.id
    ).where(StudentTopic.student_id == student_id, within(TopicEvent.created_at,start,end)).group_by(StudentTopic.topic_id)
    for key, stamp in db.execute(changes):
        activity(topics[key],local(stamp,tz).date())

    lessons = select(StudentTopic.topic_id, LessonRecord.lesson_date, LessonRecord.id).join(
        LessonTopic, LessonTopic.student_topic_id == StudentTopic.id
    ).join(LessonRecord, LessonRecord.id == LessonTopic.lesson_record_id).where(
        StudentTopic.student_id == student_id, LessonRecord.student_id == student_id,
        LessonRecord.deleted_at.is_(None), LessonRecord.lesson_date.between(first,last)
    ).order_by(LessonRecord.lesson_date,LessonRecord.id)
    for key, day, record_id in db.execute(lessons):
        topics[key]["lessons"].append((day, record_id))
        activity(topics[key], day)

    versions = {g["version_id"] for g in quizzes["groups"]}
    if versions:
        # Restrict by subquery instead of an IN list proportional to the report size.
        version_query = select(QuizAttempt.version_id).where(QuizAttempt.student_id == student_id,
                                                               within(QuizAttempt.started_at,start,end))
        tags = defaultdict(list)
        for tag in db.scalars(select(QuizVersionTopic).where(QuizVersionTopic.version_id.in_(version_query))):
            tags[tag.version_id].append(tag)
        for group in quizzes["groups"]:
            for tag in tags[group["version_id"]]:
                item = topic(tag.topic_id,tag.title_snapshot)
                activity(item,local(group["activity"],tz).date())
                item["results"].append((group["title"], rounded(group["last"]) if group["last"] is not None else None))
                for result in group["results"]:
                    if start <= result["date"] < end:
                        activity(item, local(result["date"],tz).date())
    # A late manual correction is a topic activity even when the attempt started earlier.
    grades = select(QuizVersionTopic.topic_id, QuizVersionTopic.title_snapshot,
                    func.max(QuizGradeEvent.created_at)).select_from(QuizGradeEvent).join(
        QuizAnswer, QuizAnswer.id == QuizGradeEvent.answer_id).join(
        QuizAttempt, QuizAttempt.id == QuizAnswer.attempt_id).join(
        QuizVersionTopic, QuizVersionTopic.version_id == QuizAttempt.version_id).where(
        QuizAttempt.student_id == student_id, within(QuizGradeEvent.created_at,start,end)
    ).group_by(QuizVersionTopic.topic_id, QuizVersionTopic.title_snapshot)
    for topic_id, title, stamp in db.execute(grades):
        activity(topic(topic_id,title), local(stamp,tz).date())
    active = (item for item in topics.values() if item["activity"] is not None)
    return sorted(active,key=lambda item:item["title"].casefold())


def recent_events(db, student_id, start, end):
    """Event dates are independent of the date a work or attempt was assigned."""
    def fields(stamp, label, identity):
        return stamp.label("stamp"), label.label("label"), identity.label("identity")

    sends = select(*fields(Submission.created_at, literal("Сдана работа · ") + Assignment.title,
                           Submission.id)).join(AssignmentTarget, AssignmentTarget.id == Submission.target_id
        ).join(Assignment, Assignment.id == AssignmentTarget.assignment_id).where(
            AssignmentTarget.student_id == student_id, within(Submission.created_at, start, end))
    reviews = select(*fields(ReviewEvent.created_at, literal("Проверка домашней работы · ") + Assignment.title,
                             ReviewEvent.id)).join(Submission, Submission.id == ReviewEvent.submission_id
        ).join(AssignmentTarget, AssignmentTarget.id == Submission.target_id
        ).join(Assignment, Assignment.id == AssignmentTarget.assignment_id).where(
            AssignmentTarget.student_id == student_id, within(ReviewEvent.created_at, start, end))
    attendance_changes = select(*fields(AttendanceEvent.created_at, literal("Обновлена посещаемость"),
                                        AttendanceEvent.id)).join(Lesson, Lesson.id == AttendanceEvent.lesson_id
        ).where(Lesson.student_id == student_id, within(AttendanceEvent.created_at, start, end))
    attempts = []
    for stamp, label in [(QuizAttempt.started_at, "Начат тест · "), (QuizAttempt.submitted_at, "Завершён тест · ")]:
        attempts.append(select(*fields(stamp, literal(label) + QuizVersion.title, QuizAttempt.id)).join(
            QuizVersion, QuizVersion.id == QuizAttempt.version_id).where(
            QuizAttempt.student_id == student_id, within(stamp, start, end)))
    grades = select(*fields(QuizGradeEvent.created_at, literal("Оценён ответ · ") + QuizVersion.title,
                            QuizGradeEvent.id)).join(QuizAnswer, QuizAnswer.id == QuizGradeEvent.answer_id
        ).join(QuizAttempt, QuizAttempt.id == QuizAnswer.attempt_id
        ).join(QuizVersion, QuizVersion.id == QuizAttempt.version_id).where(
            QuizAttempt.student_id == student_id, within(QuizGradeEvent.created_at, start, end))
    feed = union_all(sends, reviews, attendance_changes, *attempts, grades).subquery()
    return db.execute(select(feed.c.stamp, feed.c.label).order_by(feed.c.stamp.desc(),feed.c.label,
                                                                  feed.c.identity.desc()).limit(12)).all()


def build(db, params, tz, student_id=None, now=None):
    now = now or utc_now()
    first,last,start,end,preset = attendance.period(params,tz,now)
    lessons = attendance_summary(db,student_id,start,end,now,tz)
    homework = homework_summary(db,student_id,start,end,now,tz)
    quizzes = quiz_summary(db,student_id,start,end,now)
    topics = topic_summary(db,student_id,first,last,start,end,quizzes,tz) if student_id else []
    events = recent_events(db,student_id,start,end) if student_id else []
    return dict(first=first,last=last,preset=preset,generated=now,tz=tz,lessons=lessons,
                homework=homework,quizzes=quizzes,topics=topics,events=events)


def teacher_actions(db, now=None, student_id=None, include_homework=True, include_quizzes=True,
                    include_attendance=True, include_absences=True, subject=None, topic_id=None):
    """Return factual, actionable rows using the same current-state rules as Tutor pages.

    This is intentionally a compact cross-section for attention panels. It does not
    replace the pedagogical report formulas above or include financial/private data.
    """
    now = now or utc_now()
    pending_homework, overdue_homework = [], []
    if include_homework:
        targets_query = select(AssignmentTarget).options(
            selectinload(AssignmentTarget.assignment),
            selectinload(AssignmentTarget.student),
            selectinload(AssignmentTarget.submissions),
        ).join(Assignment, Assignment.id == AssignmentTarget.assignment_id)
        if student_id is not None:
            targets_query = targets_query.where(AssignmentTarget.student_id == student_id)
        targets = list(db.scalars(targets_query.order_by(Assignment.due_at, AssignmentTarget.id)))
        pending_homework = [t for t in targets if homework_status(t, now) == "pending"]
        overdue_homework = [t for t in targets if homework_status(t, now) == "overdue"]

    quiz_reviews = []
    if include_quizzes:
        quiz_query = select(QuizAttempt, User.display_name, QuizVersion.title).join(
            User, User.id == QuizAttempt.student_id
        ).join(QuizVersion, QuizVersion.id == QuizAttempt.version_id).where(
            QuizAttempt.state == "submitted"
        )
        if student_id is not None:
            quiz_query = quiz_query.where(QuizAttempt.student_id == student_id)
        if subject:
            quiz_query = quiz_query.where(QuizVersion.subject_title == subject)
        if topic_id is not None:
            quiz_query = quiz_query.where(QuizVersion.id.in_(select(QuizVersionTopic.version_id).where(
                QuizVersionTopic.topic_id == topic_id
            )))
        quiz_reviews = list(db.execute(quiz_query.order_by(QuizAttempt.submitted_at, QuizAttempt.id)))

    # Only lessons that have ended and remain active in the schedule can require
    # an attendance result. Future, cancelled and suppressed lessons are excluded.
    missing_attendance = []
    if include_attendance:
        missing_query = select(Lesson, User.display_name).join(
            User, User.id == Lesson.student_id
        ).outerjoin(LessonAttendance, LessonAttendance.lesson_id == Lesson.id).where(
            Lesson.state == "scheduled",
            literal_column_duration(Lesson.duration_minutes) <= now,
            (LessonAttendance.lesson_id.is_(None) | (LessonAttendance.outcome == "unknown")),
        )
        if student_id is not None:
            missing_query = missing_query.where(Lesson.student_id == student_id)
        missing_attendance = list(db.execute(missing_query.order_by(Lesson.starts_at, Lesson.id)))

    # Repeated absences are descriptive only: at least two documented outcomes
    # in the trailing 90 days, with exact rows available to open.
    repeated_absences = []
    if include_absences:
        absence_query = select(Lesson, User.display_name).join(
            User, User.id == Lesson.student_id
        ).join(LessonAttendance, LessonAttendance.lesson_id == Lesson.id).where(
            LessonAttendance.outcome == "student_absent",
            Lesson.starts_at >= now - timedelta(days=90), Lesson.starts_at < now,
        )
        if student_id is not None:
            absence_query = absence_query.where(Lesson.student_id == student_id)
        absences = list(db.execute(absence_query.order_by(Lesson.student_id, Lesson.starts_at.desc(), Lesson.id.desc())))
        by_student = defaultdict(list)
        for lesson, name in absences:
            by_student[lesson.student_id].append((lesson, name))
        repeated_absences = [rows for rows in by_student.values() if len(rows) >= 2]

    return {
        "pending_homework": pending_homework,
        "overdue_homework": overdue_homework,
        "quiz_reviews": quiz_reviews,
        "missing_attendance": missing_attendance,
        "repeated_absences": repeated_absences,
    }


def literal_column_duration(column):
    """SQLite-compatible end timestamp expression for naive UTC lesson datetimes."""
    return func.datetime(Lesson.starts_at, func.printf("+%d minutes", column))

