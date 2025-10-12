import json
import logging
from datetime import timedelta
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Prefetch
from django.http import JsonResponse, HttpResponseForbidden, HttpResponseBadRequest

from .models import Test, AnswerOption, UserTestSession, UserAnswer, Question

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
        session.save(update_fields=["submitted_due_to_violation"])
        submit = True

    return JsonResponse(
        {
            "ok": True,
            "submit": submit,
            "tab_switches": session.tab_switches,
            "max_warnings": session.test.max_warnings,
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
    for ans in session.answers.all():
        ans.recalc_points_auto()
    session.recalc_score_from_answers()

    logger.info(
        f"User {request.user} finished test {test.id}: score {session.earned_points}/{session.total_points}"
    )

    return test_result(request, session_id)


@login_required
def test_result(request, session_id):
    # Получаем сессию со всеми связанными данными
    session = UserTestSession.objects.select_related('test', 'user').prefetch_related(
        Prefetch(
            'answers',
            queryset=UserAnswer.objects.select_related('question').prefetch_related('selected_options')
        )
    ).get(id=session_id)

    all_options = AnswerOption.objects.filter(question__test=session.test).select_related('question')
    options_dict = {}
    for option in all_options:
        if option.question_id not in options_dict:
            options_dict[option.question_id] = {}
        options_dict[option.question_id][option.id] = {
            'text': option.text,
            'image': option.image
        }

    questions = session.test.questions.prefetch_related(
        Prefetch(
            'options',
            queryset=AnswerOption.objects.filter(is_correct=True),
            to_attr='correct_options'
        )
    ).all()

    user_answers_dict = {}
    for answer in session.answers.all():
        user_answers_dict[answer.question_id] = answer

    # Подготавливаем данные для шаблона
    questions_data = []
    for question in questions:
        user_answer = user_answers_dict.get(question.id)
        
        # Для типа "order" преобразуем ID в тексты
        correct_order_texts = []
        user_order_texts = []
        
        if question.question_type == "order":
            all_options = list(AnswerOption.objects.filter(question=question).order_by("id"))

            correct_order_indices = question.metadata.get("correct_order", [])
            user_order_indices = user_answer.order_answer if user_answer else []

            # Преобразуем относительные индексы в тексты
            correct_order_texts = [
                all_options[i - 1].text if 0 < i <= len(all_options) else f"#{i}"
                for i in correct_order_indices
            ]
            user_order_texts = [
                all_options[i - 1].text if 0 < i <= len(all_options) else f"#{i}"
                for i in user_order_indices
            ]

                
        questions_data.append({
            'question': question,
            'user_answer': user_answer,
            'correct_options': question.correct_options,
            'points_scored': user_answer.points_scored if user_answer else 0,
            'correct_order_texts': correct_order_texts,
            'user_order_texts': user_order_texts,
        })

    context = {
        'test': session.test,
        'session': session,
        'questions_data': questions_data,
    }
    
    return render(request, 'tests/test_result.html', context)