import json
import logging
from datetime import timedelta
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.db.models import Count, F
from django.http import JsonResponse, HttpResponseForbidden, HttpResponseBadRequest

from .models import Test, AnswerOption, UserTestSession, UserAnswer, User

logger = logging.getLogger(__name__)


@login_required
def test_list(request):
    user_groups = request.user.groups.all()
    tests = Test.objects.filter(groups__in=user_groups).annotate(
        question_count=Count("questions")
    )
    user_sessions = UserTestSession.objects.filter(
        user=request.user, finished_at__isnull=False
    )
    session_map = {s.test_id: s for s in user_sessions}
    for test in tests:
        test.user_session = session_map.get(test.id)
    return render(request, "tests/test_list.html", {"tests": tests})


@login_required
def start_test(request, test_id):
    test = get_object_or_404(Test, id=test_id)
    existing_session = UserTestSession.objects.filter(
        user=request.user, test=test, finished_at__isnull=False
    ).first()
    if existing_session:
        return render(request, "tests/already_passed.html", {"test": test})

    session, created = UserTestSession.objects.get_or_create(
        user=request.user, test=test, finished_at=None
    )
    if created:
        session.last_heartbeat = timezone.now()
        session.save(update_fields=["last_heartbeat", "client_token"])
    return redirect("tests:take_test", session.id)


@login_required
def take_test(request, session_id):
    session = get_object_or_404(UserTestSession, id=session_id, user=request.user)
    if session.finished_at:
        return redirect("tests:test_result", test_id=session.id)

    test = session.test
    questions = test.questions.prefetch_related("options").all()
    server_now = timezone.now()

    if test.duration_minutes:
        allowed_seconds = test.duration_minutes * 60
        elapsed = (server_now - session.started_at).total_seconds()
        remaining_seconds = int(max(0, allowed_seconds - elapsed))
    else:
        remaining_seconds = None

    return render(
        request,
        "tests/take_test.html",
        {
            "test": test,
            "session": session,
            "questions": questions,
            "remaining_seconds": remaining_seconds,
            "server_now_ts": int(server_now.timestamp()),
        },
    )


@login_required
def heartbeat(request, session_id):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")
    try:
        payload = json.loads(request.body.decode())
    except Exception:
        return HttpResponseBadRequest("invalid body")

    token = payload.get("token")
    session = get_object_or_404(UserTestSession, id=session_id, user=request.user)
    if token != session.client_token:
        return JsonResponse({"ok": False, "error": "invalid token"}, status=403)

    session.last_heartbeat = timezone.now()
    session.save(update_fields=["last_heartbeat"])
    return JsonResponse({"ok": True, "server_time": int(timezone.now().timestamp())})


@login_required
def warn(request, session_id):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")

    try:
        payload = json.loads(request.body.decode())
    except Exception:
        return HttpResponseBadRequest("invalid body")

    token = payload.get("token")
    session = get_object_or_404(UserTestSession, id=session_id, user=request.user)
    if token != session.client_token:
        return JsonResponse({"ok": False, "error": "invalid token"}, status=403)

    now = timezone.now()
    submit = False
    action = payload.get("action")  # "blur" или "focus"

    if action == "blur":
        session.tab_switches += 1
        session.last_left_at = now
        session.save(update_fields=["tab_switches", "last_left_at"])

    elif action == "focus":
        if session.last_left_at:
            delta = now - session.last_left_at
            session.time_outside_seconds += int(delta.total_seconds())
            session.last_left_at = None
            session.save(update_fields=["time_outside_seconds", "last_left_at"])

    max_tab_switches = session.test.max_warnings
    max_outside_seconds = session.test.max_warnings * 60  # 1 мин на warning
    if (
        session.tab_switches >= max_tab_switches
        or session.time_outside_seconds >= max_outside_seconds
    ):
        session.submitted_due_to_violation = True
        session.finished_at = now
        session.save(update_fields=["submitted_due_to_violation", "finished_at"])
        submit = True

    return JsonResponse(
        {
            "ok": True,
            "submit": submit,
            "tab_switches": session.tab_switches,
            "time_outside_seconds": session.time_outside_seconds,
        }
    )


@login_required
def submit_test(request, session_id):
    session = get_object_or_404(UserTestSession, id=session_id, user=request.user)
    if session.finished_at:
        logger.info(f"User {request.user} tried to resubmit session {session.id}")
        return redirect("tests:test_result", session_id=session.id)

    token = request.POST.get("client_token")
    if token != session.client_token:
        logger.warning(
            f"Invalid session token for user {request.user}, session {session.id}"
        )
        return HttpResponseForbidden("Invalid session token")

    test = session.test
    logger.info(
        f"Submitting test '{test.title}' (id={test.id}) for user {request.user}"
    )

    if test.duration_minutes:
        allowed = timedelta(minutes=test.duration_minutes) + timedelta(seconds=5)
        if timezone.now() > session.started_at + allowed:
            session.finished_at = timezone.now()
            session.submitted_due_to_violation = True
            session.save(update_fields=["finished_at", "submitted_due_to_violation"])
            logger.warning(f"User {request.user} submitted late test {session.id}")
            return render(request, "tests/late_submitted.html", {"test": test})

    if session.last_heartbeat and (timezone.now() - session.last_heartbeat) > timedelta(
        seconds=60
    ):
        session.submitted_due_to_violation = True
        logger.warning(
            f"User {request.user} session {session.id} flagged due to heartbeat timeout"
        )

    # Сохраняем ответы пользователя и логируем
    for question in test.questions.all():
        answer, _ = UserAnswer.objects.get_or_create(session=session, question=question)
        field_name = f"q_{question.id}"
        qtype = question.question_type

        if qtype in ("single", "multiple"):
            selected_ids = request.POST.getlist(field_name)
            answer.selected_options.set(
                AnswerOption.objects.filter(id__in=selected_ids)
            )
            logger.debug(
                f"User {request.user} answered question {question.id} ({qtype}): {selected_ids}"
            )

        elif qtype in ("text", "long_text", "number"):
            text_value = request.POST.get(field_name, "").strip()
            answer.text_answer = text_value
            logger.debug(
                f"User {request.user} answered question {question.id} ({qtype}): {text_value}"
            )
        elif qtype == "order":
            order_str = request.POST.get(field_name, "").strip()
            answer.order_answer = json.loads(order_str) if order_str else []
            answer.order_answer = [int(x) for x in answer.order_answer]
            logger.debug(
                f"User {request.user} answered question {question.id} ({qtype}): {order_str}"
            )

        answer.save()

    session.finished_at = timezone.now()
    session.save(update_fields=["finished_at", "submitted_due_to_violation"])

    score, earned_points, total_points = session.calculate_score()
    logger.info(
        f"User {request.user} finished test {test.id}: score {earned_points}/{total_points}"
    )

    return render(
        request,
        "tests/result.html",
        {
            "test": test,
            "score": score,
            "correct": earned_points,
            "total": total_points,
            "flagged": session.submitted_due_to_violation,
            "tab_switches": session.tab_switches,
        },
    )


@login_required
def test_result(request, session_id):
    session = get_object_or_404(UserTestSession, id=session_id, user=request.user)
    test = session.test

    if not session.finished_at:
        return redirect("tests:take_test", session_id=session.id)

    return render(
        request,
        "tests/test_result.html",
        {"test": test, "session": session},
    )
