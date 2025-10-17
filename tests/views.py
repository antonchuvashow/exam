import json
import logging
import secrets as _secrets
from datetime import timedelta
from typing import List

from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Prefetch
from django.http import JsonResponse, HttpResponseForbidden, HttpResponseBadRequest, HttpResponseNotAllowed
from django.db import transaction
from django.core.exceptions import ValidationError

from .models import Test, AnswerOption, UserTestSession, UserAnswer, Question
from .utils.safe import _safe_json_loads, _token_equal, _to_int_list

logger = logging.getLogger(__name__)

@login_required
def test_list(request):
    # показываем тесты, доступные группам пользователя
    user_groups = request.user.groups.all()
    tests = (
        Test.objects.filter(groups__in=user_groups)
        .annotate(question_count=Count("questions", distinct=True))
        .distinct()
    )

    # завершённые сессии пользователя (для отметки "уже пройден")
    user_sessions = UserTestSession.objects.filter(user=request.user, finished_at__isnull=False)
    session_map = {s.test_id: s for s in user_sessions}

    for test in tests:
        test.user_session = session_map.get(test.id)

    return render(request, "tests/test_list.html", {"tests": tests})


@login_required
def start_test(request, test_id):
    # проверяем, что пользователь имеет доступ к тесту (по группам)
    test = get_object_or_404(Test.objects.prefetch_related("groups"), id=test_id)
    user_groups = set(request.user.groups.values_list("id", flat=True))
    test_group_ids = set(test.groups.values_list("id", flat=True))
    if not (user_groups & test_group_ids) and not request.user.is_staff:
        return HttpResponseForbidden("Access denied")

    # не даём повторно пройти уже завершённый тест
    existing_session = UserTestSession.objects.filter(user=request.user, test=test, finished_at__isnull=False).first()
    if existing_session:
        return render(request, "tests/already_passed.html", {"test": test})

    # безопасное создание сессии — генерируем client_token если создаём
    session, created = UserTestSession.objects.get_or_create(user=request.user, test=test, finished_at=None)
    if created:
        session.client_token = _secrets.token_urlsafe(32)
        session.last_heartbeat = timezone.now()
        session.started_at = session.started_at or timezone.now()
        session.save(update_fields=["client_token", "last_heartbeat", "started_at"])
    return redirect("tests:take_test", session.id)


@login_required
def take_test(request, session_id):
    # проверяем, что сессия принадлежит пользователю (или staff)
    session = get_object_or_404(UserTestSession.objects.select_related("test"), id=session_id)
    if session.user != request.user and not request.user.is_staff:
        return HttpResponseForbidden("Access denied")

    if session.finished_at:
        return redirect("tests:test_result", session_id=session.id)

    test = session.test
    questions = test.questions.prefetch_related("options").all()
    server_now = timezone.now()

    # защита: если started_at необъявлен — инициализируем
    if not session.started_at:
        session.started_at = server_now
        session.save(update_fields=["started_at"])

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
        return HttpResponseNotAllowed(["POST"])
    payload = _safe_json_loads(request.body)
    if payload is None:
        return HttpResponseBadRequest("invalid body")

    token = payload.get("token")
    session = get_object_or_404(UserTestSession, id=session_id, user=request.user)
    if not _token_equal(token, session.client_token):
        return JsonResponse({"ok": False, "error": "invalid token"}, status=403)

    session.last_heartbeat = timezone.now()
    session.save(update_fields=["last_heartbeat"])
    return JsonResponse({"ok": True, "server_time": int(timezone.now().timestamp())})


@login_required
def warn(request, session_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    payload = _safe_json_loads(request.body)
    if payload is None:
        return HttpResponseBadRequest("invalid body")

    token = payload.get("token")
    session = get_object_or_404(UserTestSession, id=session_id, user=request.user)
    if not _token_equal(token, session.client_token):
        return JsonResponse({"ok": False, "error": "invalid token"}, status=403)

    now = timezone.now()
    submit = False
    action = payload.get("action")  # "blur" или "focus"

    # защитные границы — не даём бесконечно увеличивать счётчик
    if action == "blur":
        session.tab_switches = (session.tab_switches or 0) + 1
        session.last_left_at = now
        session.save(update_fields=["tab_switches", "last_left_at"])

    elif action == "focus":
        if session.last_left_at:
            delta = now - session.last_left_at
            add_seconds = int(delta.total_seconds())
            session.time_outside_seconds = (session.time_outside_seconds or 0) + add_seconds
            session.last_left_at = None
            session.save(update_fields=["time_outside_seconds", "last_left_at"])

    max_tab_switches = getattr(session.test, "max_warnings", 3) or 3
    max_outside_seconds = max_tab_switches * 60  # 1 мин на warning

    if (
        (session.tab_switches or 0) >= max_tab_switches
        or (session.time_outside_seconds or 0) >= max_outside_seconds
    ):
        session.submitted_due_to_violation = True
        session.save(update_fields=["submitted_due_to_violation"])
        submit = True

    return JsonResponse(
        {
            "ok": True,
            "submit": submit,
            "tab_switches": session.tab_switches,
            "max_warnings": max_tab_switches,
            "time_outside_seconds": session.time_outside_seconds,
        }
    )


@login_required
def submit_test(request, session_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    session = get_object_or_404(UserTestSession.objects.select_related("test"), id=session_id)
    if session.user != request.user and not request.user.is_staff:
        return HttpResponseForbidden("Access denied")

    # защита от двойной отправки: если уже завершена, редиректим
    if session.finished_at:
        logger.info("Resubmit attempt: user=%s session=%s", request.user.pk, session.id)
        return redirect("tests:test_result", session_id=session.id)

    token = request.POST.get("client_token")
    if not _token_equal(token, session.client_token):
        logger.warning("Invalid client token for user=%s session=%s", request.user.pk, session.id)
        return HttpResponseForbidden("Invalid session token")

    test = session.test
    logger.info("Submitting test id=%s for user=%s", test.id, request.user.pk)

    now = timezone.now()

    if test.duration_minutes:
        allowed = timedelta(minutes=test.duration_minutes) + timedelta(seconds=5)
        if now > (session.started_at or now) + allowed:
            session.finished_at = now
            session.submitted_due_to_violation = True
            session.save(update_fields=["finished_at", "submitted_due_to_violation"])
            logger.warning("Late submission flagged: user=%s session=%s", request.user.pk, session.id)

    # heartbeat timeout check
    if session.last_heartbeat and (now - session.last_heartbeat) > timedelta(seconds=60):
        session.submitted_due_to_violation = True
        session.save(update_fields=["submitted_due_to_violation"])
        logger.warning("Heartbeat timeout flagged: user=%s session=%s", request.user.pk, session.id)

    # process answers
    with transaction.atomic():
        # lock the session row to avoid race conditions (works with select_for_update)
        session = UserTestSession.objects.select_for_update().select_related("test").get(id=session.id)

        # double-check we didn't finish while waiting for lock
        if session.finished_at:
            logger.warning("Session finished during submit: user=%s session=%s", request.user.pk, session.id)
            return redirect("tests:test_result", session_id=session.id)

        # Map question_id -> question (with its options)
        questions = list(test.questions.prefetch_related("options").all())
        questions_map = {q.id: q for q in questions}

        # iterate questions and validate inputs
        for question in questions:
            # get or create user answer
            answer, _ = UserAnswer.objects.get_or_create(session=session, question=question)

            field_name = f"q_{question.id}"
            qtype = question.question_type

            if qtype in ("single", "multiple"):
                # getlist is safe — returns [] if not present
                selected_ids_raw = request.POST.getlist(field_name)
                try:
                    selected_ids = _to_int_list(selected_ids_raw, limit=50)
                except ValueError:
                    logger.warning("Invalid selected ids from user=%s q=%s", request.user.pk, question.id)
                    return HttpResponseBadRequest("Invalid option ids")

                # restrict to options belonging to this question
                valid_options = AnswerOption.objects.filter(question=question, id__in=selected_ids).values_list("id", flat=True)
                valid_ids = list(valid_options)

                # set selected options (clears previous)
                answer.selected_options.set(valid_ids)

                logger.debug("User %s answer q=%s selected=%s", request.user.pk, question.id, valid_ids)

            elif qtype in ("text", "long_text", "number"):
                raw = request.POST.get(field_name, "")
                # защитные ограничения по длине
                if isinstance(raw, str):
                    trimmed = raw.strip()
                    if len(trimmed) > 5000:
                        trimmed = trimmed[:5000]
                    answer.text_answer = trimmed
                else:
                    answer.text_answer = ""
                logger.debug("User %s answer q=%s text_len=%d", request.user.pk, question.id, len(answer.text_answer or ""))

            elif qtype == "order":
                order_str = request.POST.get(field_name, "").strip()
                if not order_str:
                    answer.order_answer = []
                else:
                    try:
                        parsed = json.loads(order_str)
                        if not isinstance(parsed, list):
                            raise ValueError("order must be list")
                        # convert to ints with limit
                        parsed_ids = _to_int_list(parsed, limit=100)
                    except (ValueError, TypeError):
                        logger.warning("Invalid order payload user=%s q=%s", request.user.pk, question.id)
                        return HttpResponseBadRequest("Invalid order payload")

                    answer.order_answer = parsed_ids

                    logger.debug("User %s answer q=%s order=%s", request.user.pk, question.id, answer.order_answer)

            else:
                # неизвестный тип — игнорируем
                logger.warning("Unknown question type '%s' for q=%s", qtype, question.id)

            # save answer
            answer.save()

        # помечаем сессию завершённой
        session.finished_at = now
        session.save(update_fields=["finished_at", "submitted_due_to_violation", "last_heartbeat"])

    # После атомарного блока — пересчёт баллов (вне транзакции, чтобы не держать блокировку долго)
    for ans in session.answers.all():
        try:
            ans.recalc_points_auto()
        except Exception as e:
            logger.exception("Failed to recalc points for answer id=%s: %s", getattr(ans, "id", None), e)

    try:
        session.recalc_score_from_answers()
    except Exception:
        logger.exception("Failed to recalc session score for session id=%s", session.id)

    logger.info("User %s finished test %s: earned=%s total=%s", request.user.pk, test.id, session.earned_points, session.total_points)

    return redirect("tests:test_result", session_id=session.id)


@login_required
def test_result(request, session_id):
    # показываем результаты только владельцу сессии (или staff)
    try:
        session = (
            UserTestSession.objects.select_related("test", "user")
            .prefetch_related(
                Prefetch(
                    "answers",
                    queryset=UserAnswer.objects.select_related("question").prefetch_related("selected_options"),
                )
            )
            .get(id=session_id)
        )
    except UserTestSession.DoesNotExist:
        return get_object_or_404(UserTestSession, id=0)  # вызовет 404

    if session.user != request.user and not request.user.is_staff:
        return HttpResponseForbidden("Access denied")

    # Подготовка опций и вопросов (как было, но с валидацией)
    all_options = (
        AnswerOption.objects.filter(question__test=session.test)
        .select_related("question")
    )
    options_dict = {}
    for option in all_options:
        options_dict.setdefault(option.question_id, {})[option.id] = {
            "text": option.text,
            "image": option.image,
        }

    questions = session.test.questions.prefetch_related(
        Prefetch(
            "options",
            queryset=AnswerOption.objects.filter(is_correct=True),
            to_attr="correct_options",
        )
    ).all()

    user_answers_dict = {answer.question_id: answer for answer in session.answers.all()}

    questions_data = []
    for question in questions:
        user_answer = user_answers_dict.get(question.id)
        correct_order_texts = []
        user_order_texts = []

        if question.question_type == "order":
            # guard: get options for this question
            opts = list(AnswerOption.objects.filter(question=question).order_by("id"))
            correct_order_indices = question.metadata.get("correct_order", []) or []
            user_order_indices = user_answer.order_answer if user_answer else []

            # safe conversion: indices -> texts (1-based indices are expected)
            def idx_to_text(i):
                if isinstance(i, int) and 0 < i <= len(opts):
                    return opts[i - 1].text
                return f"#{i}"

            correct_order_texts = [idx_to_text(i) for i in correct_order_indices]
            user_order_texts = [idx_to_text(i) for i in user_order_indices]

        questions_data.append(
            {
                "question": question,
                "user_answer": user_answer,
                "correct_options": getattr(question, "correct_options", []),
                "points_scored": user_answer.points_scored if user_answer else 0,
                "correct_order_texts": correct_order_texts,
                "user_order_texts": user_order_texts,
            }
        )

    context = {"test": session.test, "session": session, "questions_data": questions_data}
    return render(request, "tests/test_result.html", context)
