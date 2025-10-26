import numpy as np
from django.db import models
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from .utils.scoring import score_open_answer
import secrets
import logging

logger = logging.getLogger(__name__)

User = get_user_model()


class Test(models.Model):
    title = models.CharField("Название теста", max_length=200)
    description = models.TextField("Описание", blank=True)
    duration_minutes = models.PositiveIntegerField("Длительность (мин)", null=True, blank=True)
    max_warnings = models.PositiveIntegerField("Максимум предупреждений", default=3)
    created_at = models.DateTimeField("Дата создания", auto_now_add=True)
    groups = models.ManyToManyField("auth.Group", verbose_name="Доступные группы", blank=True)
    show_answers = models.BooleanField("Показывать ответы после прохождения", default=False)
    show_grade = models.BooleanField("Отображать оценку", default=False)

    class Meta:
        verbose_name = "Тест"
        verbose_name_plural = "Тесты"

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

    test = models.ForeignKey(Test, on_delete=models.CASCADE, related_name="questions", verbose_name="Тест")
    text = models.TextField("Текст вопроса")
    image = models.ImageField("Изображение", upload_to="questions/", null=True, blank=True)
    question_type = models.CharField("Тип вопроса", max_length=20, choices=QUESTION_TYPES)
    points = models.PositiveIntegerField("Баллы за вопрос", default=1)
    metadata = models.JSONField("Дополнительные параметры", default=dict, blank=True)

    class Meta:
        verbose_name = "Вопрос"
        verbose_name_plural = "Вопросы"

    def __str__(self):
        return self.text[:50]


class AnswerOption(models.Model):
    question = models.ForeignKey(
        Question, on_delete=models.CASCADE, related_name="options", verbose_name="Вопрос"
    )
    text = models.CharField("Текст варианта", max_length=500, blank=True)
    image = models.ImageField("Изображение", upload_to="answers/", null=True, blank=True)
    is_correct = models.BooleanField("Правильный ответ", default=False)

    class Meta:
        verbose_name = "Вариант ответа"
        verbose_name_plural = "Варианты ответов"

    def __str__(self):
        return self.text[:50]


class GradingSystem(models.Model):
    group = models.OneToOneField(Group, on_delete=models.CASCADE, related_name='grading_system', verbose_name="Группа")
    name = models.CharField("Название системы", max_length=100)

    class Meta:
        verbose_name = "Система оценивания"
        verbose_name_plural = "Системы оценивания"

    def __str__(self):
        return self.name

    def suggested_thresholds(self):
        grade_count = self.grades.count()
        if grade_count < 2:
            return "Нужно как минимум 2 оценки для предложений."

        # Get all completed sessions of users in this group
        user_ids = self.group.user_set.values_list("id", flat=True)
        sessions = UserTestSession.objects.filter(user_id__in=user_ids, score_percent__isnull=False)

        if not sessions.exists():
            # fallback evenly spaced
            thresholds = np.linspace(0, 100, grade_count + 1)[1:]
        else:
            scores = list(sessions.values_list("score_percent", flat=True))
            thresholds = np.quantile(scores, np.linspace(0, 1, grade_count + 1)[1:])

        thresholds = [round(t, 2) for t in thresholds]

        result = []
        for grade, t in zip(self.grades.order_by('order'), thresholds):
            result.append(f"{grade.grade_name}: от {t}%")

        return "Предлагаемые пороги: " + ", ".join(result)


class Grade(models.Model):
    grading_system = models.ForeignKey(
        GradingSystem, on_delete=models.CASCADE, related_name="grades", verbose_name="Система оценивания"
    )
    grade_name = models.CharField("Оценка", max_length=50)
    min_percent = models.FloatField("Минимальный процент")
    order = models.PositiveIntegerField("Порядок", default=0, help_text="Чем больше число, тем выше оценка.")

    class Meta:
        verbose_name = "Оценка"
        verbose_name_plural = "Оценки"
        ordering = ["-order"]

    def __str__(self):
        return f"{self.grade_name} (от {self.min_percent}%)"


class UserTestSession(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="Пользователь")
    test = models.ForeignKey(Test, on_delete=models.CASCADE, verbose_name="Тест")
    started_at = models.DateTimeField("Начало прохождения", auto_now_add=True)
    finished_at = models.DateTimeField("Завершено", null=True, blank=True)

    tab_switches = models.PositiveIntegerField("Переключения вкладок", default=0)
    time_outside_seconds = models.PositiveIntegerField("Время вне вкладки (сек)", default=0)
    last_left_at = models.DateTimeField("Последний выход", null=True, blank=True)
    submitted_due_to_violation = models.BooleanField("Завершён из-за нарушения", default=False)
    last_heartbeat = models.DateTimeField("Последний сигнал клиента", null=True, blank=True)
    client_token = models.CharField("Токен клиента", max_length=128, blank=True, null=True, unique=True)

    score_percent = models.FloatField("Процент правильных ответов", null=True, blank=True)
    earned_points = models.FloatField("Набрано баллов", null=True, blank=True)
    total_points = models.FloatField("Всего баллов", null=True, blank=True)
    grade = models.ForeignKey("Grade", on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Оценка")

    class Meta:
        verbose_name = "Сессия прохождения теста"
        verbose_name_plural = "Сессии прохождения тестов"

    def save(self, *args, **kwargs):
        if not self.client_token:
            self.client_token = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user.get_full_name()} — {self.test.title}"
    
    def recalc_score_from_answers(self):
        """
        Итоговый пересчёт баллов сессии по текущим points_scored ответов,
        без изменения их значений.
        """
        answers = self.answers.select_related("question").all()
        total_points = sum(ans.question.points for ans in answers)
        if total_points == 0:
            self.total_points = 0
            self.score_percent = 0
            self.earned_points = 0
        else:
            earned_points = sum(ans.points_scored for ans in answers if ans.points_scored)
            self.score_percent = round(earned_points / total_points * 100)
            self.earned_points = earned_points
            self.total_points = total_points

        self.save(update_fields=["earned_points", "score_percent", "total_points"])
        self.calculate_grade()

    def calculate_grade(self):
        if self.score_percent is None:
            self.grade = None
            self.save(update_fields=["grade"])
            return

        user_groups = self.user.groups.all()
        grading_system = GradingSystem.objects.filter(group__in=user_groups).first()

        if grading_system:
            for grade in grading_system.grades.all():
                if self.score_percent >= grade.min_percent:
                    self.grade = grade
                    self.save(update_fields=["grade"])
                    return
        
        self.grade = None
        self.save(update_fields=["grade"])


class UserAnswer(models.Model):
    session = models.ForeignKey(
        UserTestSession, on_delete=models.CASCADE, related_name="answers", verbose_name="Сессия"
    )
    question = models.ForeignKey(Question, on_delete=models.CASCADE, verbose_name="Вопрос")
    selected_options = models.ManyToManyField(AnswerOption, blank=True, verbose_name="Выбранные варианты")
    text_answer = models.TextField("Текстовый ответ", blank=True, null=True)
    order_answer = models.JSONField("Порядок элементов", blank=True, null=True, default=list)
    points_scored = models.FloatField("Полученные баллы", blank=True, null=True)

    class Meta:
        verbose_name = "Ответ пользователя"
        verbose_name_plural = "Ответы пользователей"

    def __str__(self):
        return f"Ответ на: {self.question.text[:40]}"

    def recalc_points_auto(self):
        q = self.question
        points = getattr(q, "points", 1) or 1
        self.points_scored = 0

        try:
            # --- Один верный ответ ---
            if q.question_type == "single":
                correct = q.options.filter(is_correct=True).first()
                selected = self.selected_options.first()
                if correct and selected and correct.id == selected.id:
                    self.points_scored = points
                logger.debug(f"[single] Q{q.id}: score={self.points_scored}")

            # --- Несколько верных ---
            elif q.question_type == "multiple":
                correct_ids = set(q.options.filter(is_correct=True).values_list("id", flat=True))
                selected_ids = set(self.selected_options.values_list("id", flat=True))

                correct_selected = len(correct_ids & selected_ids)
                incorrect_selected = len(selected_ids - correct_ids)

                if correct_ids:
                    score_fraction = (correct_selected - incorrect_selected) / len(correct_ids)
                    score_fraction = max(score_fraction, 0)
                else:
                    score_fraction = 0

                self.points_scored = round(points * score_fraction, 2)
                logger.debug(f"[multiple] Q{q.id}: correct={correct_selected}, incorrect={incorrect_selected}, score={self.points_scored}")


            # --- Числовой ---
            elif q.question_type == "number":
                correct_opt = q.options.filter(is_correct=True).first()
                if correct_opt:
                    correct_value = float(correct_opt.text.strip())
                    tolerance = float(q.metadata.get("tolerance", 0.05 * correct_value))
                    answer_value = float(self.text_answer or 0)
                    if abs(answer_value - correct_value) <= tolerance:
                        self.points_scored = points
                logger.debug(f"[number] Q{q.id}: score={self.points_scored}")

            # --- Упорядочивание ---
            elif q.question_type == "order":
                options = list(q.options.all().order_by("id"))  
                correct_order_indices = q.metadata.get("correct_order", [])

                correct_order_ids = [options[i - 1].id for i in correct_order_indices if 0 < i <= len(options)]

                user_order = self.order_answer or []
                if user_order and all(isinstance(x, int) for x in user_order):
                    user_order_ids = [options[i - 1].id for i in user_order if 0 < i <= len(options)]
                else:
                    user_order_ids = user_order

                if user_order_ids == correct_order_ids:
                    self.points_scored = points
                else:
                    matches = sum(1 for a, b in zip(user_order_ids, correct_order_ids) if a == b)
                    self.points_scored = round(points * matches / len(correct_order_ids), 2)

                logger.debug(f"[order] Q{q.id}: score={self.points_scored}")

            # --- текстовые ---
            elif q.question_type in ("text", "long_text"):
                user_ans = (self.text_answer or "").strip()
                if not user_ans:
                    self.points_scored = 0
                    self.save(update_fields=["points_scored"])
                    logger.debug(f"[text] Q{q.id}: пустой ответ -> 0")
                    return self.points_scored

                correct_texts = [opt.text.strip() for opt in q.options.filter(is_correct=True) if opt.text and opt.text.strip()]
                incorrect_texts = [opt.text.strip() for opt in q.options.filter(is_correct=False) if opt.text and opt.text.strip()]

                try:
                    score = score_open_answer(
                        question_text=q.text,
                        user_ans=user_ans,
                        correct_texts=correct_texts,
                        incorrect_texts=incorrect_texts,
                        points=points,
                        threshold=float(q.metadata.get("semantic_threshold", 0.65)),
                        full_credit_threshold=float(q.metadata.get("full_credit_threshold", 0.92)),
                        incorrect_threshold=float(q.metadata.get("incorrect_threshold", 0.92)),
                        penalty_weight=float(q.metadata.get("penalty_weight", 1.0)),
                        correction_factor=float(q.metadata.get("correction_factor", 0.6)),
                        min_partial=float(q.metadata.get("min_partial", 0.0)),
                        topk_incorrect=int(q.metadata.get("topk_incorrect", 3)),
                        aspect_weight=float(q.metadata.get("aspect_weight", 0.5)),
                        length_penalty_min_ratio=float(q.metadata.get("length_penalty_min_ratio", 0.35)),
                    )
                    self.points_scored = score
                except Exception as e:
                    logger.exception(f"Ошибка при семантической оценке Q{q.id}: {e}")
                    self.points_scored = 0

                self.save(update_fields=["points_scored"])
                return self.points_scored

        except Exception as e:
            logger.exception(f"Ошибка при проверке правильности ответа для вопроса {q.id}: {e}")

        self.save(update_fields=["points_scored"])
        return self.points_scored
