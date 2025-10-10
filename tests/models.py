from django.db import models
from django.contrib.auth import get_user_model
from .utils import semantic_similarity
import secrets
import logging

logger = logging.getLogger(__name__)

User = get_user_model()


class Test(models.Model):
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    duration_minutes = models.PositiveIntegerField(null=True, blank=True)
    max_warnings = models.PositiveIntegerField(default=3)
    created_at = models.DateTimeField(auto_now_add=True)
    groups = models.ManyToManyField("auth.Group", blank=True)

    def __str__(self):
        return self.title


class Question(models.Model):
    QUESTION_TYPES = [
        ("single", "Один верный ответ"),
        ("multiple", "Несколько верных ответов"),
        ("text", "Короткий текстовый ответ"),
        ("long_text", "Развёрнутый ответ"),
        ("number", "Числовой ответ"),
        ("order", "Упорядочивание"),
    ]

    test = models.ForeignKey(Test, on_delete=models.CASCADE, related_name="questions")
    text = models.TextField()
    image = models.ImageField(upload_to="questions/", null=True, blank=True)
    question_type = models.CharField(max_length=20, choices=QUESTION_TYPES)
    points = models.PositiveIntegerField(default=1)
    metadata = models.JSONField(
        default=dict, blank=True
    )  # например, {"tolerance": 0.1}

    def __str__(self):
        return f"{self.test.title} — {self.text[:50]}"


class AnswerOption(models.Model):
    question = models.ForeignKey(
        Question, on_delete=models.CASCADE, related_name="options"
    )
    text = models.CharField(max_length=500, blank=True)
    image = models.ImageField(upload_to="answers/", null=True, blank=True)
    is_correct = models.BooleanField(default=False)

    def __str__(self):
        return self.text[:50]


class UserTestSession(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    test = models.ForeignKey(Test, on_delete=models.CASCADE)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    tab_switches = models.PositiveIntegerField(default=0)
    time_outside_seconds = models.PositiveIntegerField(default=0)
    last_left_at = models.DateTimeField(null=True, blank=True)
    submitted_due_to_violation = models.BooleanField(default=False)
    last_heartbeat = models.DateTimeField(null=True, blank=True)
    client_token = models.CharField(max_length=128, blank=True, null=True, unique=True)

    score_percent = models.FloatField(null=True, blank=True)
    earned_points = models.FloatField(null=True, blank=True)
    total_points = models.FloatField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.client_token:
            self.client_token = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user.get_full_name()} — {self.test.title}"

    def calculate_score(self):
        answers = self.answers.select_related("question").all()

        total_points = sum(getattr(ans.question, "points", 1) for ans in answers)
        if total_points == 0:
            self.score_percent = 0
            self.earned_points = 0
            self.total_points = 0
            self.save(update_fields=["score_percent", "earned_points", "total_points"])
            return self.score_percent

        earned_points = sum(ans.check_correctness() for ans in answers)
        self.score_percent = round(earned_points / total_points, 2) * 100
        self.earned_points = earned_points
        self.total_points = total_points
        self.save(update_fields=["score_percent", "earned_points", "total_points"])
        return self.score_percent, earned_points, total_points


class UserAnswer(models.Model):
    session = models.ForeignKey(
        "UserTestSession", on_delete=models.CASCADE, related_name="answers"
    )
    question = models.ForeignKey("Question", on_delete=models.CASCADE)
    selected_options = models.ManyToManyField("AnswerOption", blank=True)
    text_answer = models.TextField(blank=True, null=True)
    order_answer = models.JSONField(blank=True, null=True, default=list)

    def __str__(self):
        return f"Ответ на: {self.question.text[:40]}"

    def check_correctness(self):
        q = self.question
        points = getattr(q, "points", 1) or 1
        score = 0

        try:
            if q.question_type == "single":
                correct = q.options.filter(is_correct=True).first()
                selected = self.selected_options.first()
                if correct and selected and correct.id == selected.id:
                    score = points
                logger.debug(
                    f"[single] Q{q.id}: correct={correct}, selected={selected}, score={score}"
                )

            elif q.question_type == "multiple":
                correct_ids = set(
                    q.options.filter(is_correct=True).values_list("id", flat=True)
                )
                selected_ids = set(self.selected_options.values_list("id", flat=True))
                if correct_ids:
                    score = (
                        points
                        * len(correct_ids.intersection(selected_ids))
                        / len(correct_ids)
                    )
                logger.debug(
                    f"[multiple] Q{q.id}: correct={correct_ids}, selected={selected_ids}, score={score}"
                )

            elif q.question_type == "number":
                correct_opt = q.options.filter(is_correct=True).first()
                if correct_opt:
                    correct_value = float(correct_opt.text.strip())
                    tolerance = float(q.metadata.get("tolerance", 0.05 * correct_value))
                    answer_value = float(self.text_answer)
                    if abs(answer_value - correct_value) <= tolerance:
                        score = points
                logger.debug(f"[number] Q{q.id}: score={score}")

            elif q.question_type == "order":
                correct_order = q.metadata.get("correct_order", [])
                user_order = self.order_answer or []
                if user_order == correct_order:
                    score = points
                logger.debug(
                    f"[order] Q{q.id}: user={user_order}, correct={correct_order}, score={score}"
                )

            elif q.question_type in ("text", "long_text"):
                correct_texts = [
                    opt.text.strip()
                    for opt in q.options.filter(is_correct=True)
                    if opt.text
                ]
                if correct_texts and self.text_answer:
                    similarities = [
                        semantic_similarity(self.text_answer.strip(), corr)
                        for corr in correct_texts
                    ]
                    max_sim = max(similarities) if similarities else 0
                    threshold = float(q.metadata.get("semantic_threshold", 0.70))
                    if max_sim >= threshold:
                        score = min(points, points * (max_sim + 0.05))
                logger.debug(f"[text] Q{q.id}: score={score}")

        except Exception as e:
            logger.exception(
                f"Ошибка при проверке правильности ответа для вопроса {q.id}: {e}"
            )

        return round(score, 2)
