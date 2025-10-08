# tests/views.py
import json
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.db.models import Count, F
from django.http import JsonResponse, HttpResponseForbidden, HttpResponseBadRequest
from datetime import timedelta

from .models import Test, AnswerOption, UserTestSession, UserAnswer, User
from allauth.socialaccount.models import SocialToken, SocialApp
from googleapiclient.discovery import build
import logging
from google.oauth2.credentials import Credentials

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
        test.user_session = session_map.get(test.id)  # либо None, если ещё не проходил

    return render(request, "tests/test_list.html", {"tests": tests})


@login_required
def start_test(request, test_id):
    test = get_object_or_404(Test, id=test_id)

    # Проверка: уже завершал ли пользователь этот тест
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
        # уже завершено — показываем результат
        return redirect(
            "tests:test_result", test_id=session.id
        )  # предполагается страница просмотра результата

    test = session.test
    questions = test.questions.prefetch_related("options").all()
    server_now = timezone.now()

    if test.duration_minutes:
        allowed_seconds = test.duration_minutes * 60
        elapsed = (server_now - session.started_at).total_seconds()
        remaining_seconds = int(max(0, allowed_seconds - elapsed))
    else:
        remaining_seconds = None

    # Передаём токен, оставшееся время и server timestamp (чтобы JS мог синхронизоваться)
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
    # ожидаем POST с JSON {"token": "..."}
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
    # уведомление об уходе со страницы: POST JSON {"token": "..."}
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

    # атомарно инкрементим
    UserTestSession.objects.filter(pk=session.pk).update(
        tab_switches=F("tab_switches") + 1
    )
    session.refresh_from_db()

    # если превышен лимит — помечаем сессию как завершённую по нарушению
    if session.tab_switches >= session.test.max_warnings:
        session.submitted_due_to_violation = True
        session.finished_at = timezone.now()
        session.save(update_fields=["submitted_due_to_violation", "finished_at"])
        return JsonResponse(
            {"ok": True, "submit": True, "tab_switches": session.tab_switches}
        )

    return JsonResponse(
        {"ok": True, "submit": False, "tab_switches": session.tab_switches}
    )


@login_required
def submit_test(request, session_id):
    session = get_object_or_404(UserTestSession, id=session_id, user=request.user)
    if session.finished_at:
        return redirect("tests:test_result", session_id=session.id)

    # Проверяем токен сессии
    token = request.POST.get("client_token")
    if token != session.client_token:
        return HttpResponseForbidden("Invalid session token")

    test = session.test

    # Проверка по времени
    if test.duration_minutes:
        allowed = timedelta(minutes=test.duration_minutes) + timedelta(seconds=5)
        if timezone.now() > session.started_at + allowed:
            session.finished_at = timezone.now()
            session.submitted_due_to_violation = True
            session.save(update_fields=["finished_at", "submitted_due_to_violation"])
            return render(request, "tests/late_submitted.html", {"test": test})

    # Проверка heartbeat
    if session.last_heartbeat and (timezone.now() - session.last_heartbeat) > timedelta(
        seconds=60
    ):
        session.submitted_due_to_violation = True

    # Сохраняем ответы
    questions = test.questions.all()
    for question in questions:
        selected_ids = request.POST.getlist(f"q_{question.id}")
        answer, _ = UserAnswer.objects.get_or_create(session=session, question=question)
        answer.selected_options.set(AnswerOption.objects.filter(id__in=selected_ids))
        answer.save()

    session.finished_at = timezone.now()
    session.save(
        update_fields=[
            "finished_at",
            "submitted_due_to_violation",
            "tab_switches",
            "last_heartbeat",
            "client_token",
        ]
    )

    # Подсчёт результатов
    correct = 0
    total = 0
    for q in questions:
        if q.question_type in ("single", "multiple", "mixed"):
            user_ans = session.answers.filter(question=q).first()
            if not user_ans:
                continue
            correct_set = set(
                q.options.filter(is_correct=True).values_list("id", flat=True)
            )
            selected_set = set(user_ans.selected_options.values_list("id", flat=True))
            if correct_set == selected_set:
                correct += 1
            total += 1

    score = int(correct / total * 100) if total > 0 else 0

    return render(
        request,
        "tests/result.html",
        {
            "test": test,
            "score": score,
            "correct": correct,
            "total": total,
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

    total_questions = test.questions.count()
    correct_answers = 0

    for ans in UserAnswer.objects.filter(session=session):
        correct_options = set(
            ans.question.options.filter(is_correct=True).values_list("id", flat=True)
        )
        selected = set(ans.selected_options.values_list("id", flat=True))
        if correct_options == selected:
            correct_answers += 1

    score_percent = (
        round((correct_answers / total_questions) * 100, 1) if total_questions else 0
    )

    if session.score_percent != score_percent:
        session.score_percent = score_percent
        session.save()

    context = {
        "test": test,
        "session": session,
        "correct_answers": correct_answers,
        "total_questions": total_questions,
        "score_percent": score_percent,
    }
    return render(request, "tests/test_result.html", context)
