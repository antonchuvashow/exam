from django.db import models
from django.contrib.auth import get_user_model
import secrets

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
        ("mixed", "Один или несколько верных ответов"),
        ("file", "Ответ файлом"),
        ("code", "Ответ кодом"),
    ]

    test = models.ForeignKey(Test, on_delete=models.CASCADE, related_name="questions")
    text = models.TextField()
    image = models.ImageField(upload_to="questions/", null=True, blank=True)
    question_type = models.CharField(max_length=20, choices=QUESTION_TYPES)

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
    submitted_due_to_violation = models.BooleanField(default=False)

    last_heartbeat = models.DateTimeField(null=True, blank=True)
    client_token = models.CharField(max_length=128, blank=True, null=True, unique=True)

    # result info
    score_percent = models.FloatField(null=True, blank=True)

    # Google Classroom integration
    google_student_submission_id = models.CharField(
        max_length=128, blank=True, null=True
    )

    def save(self, *args, **kwargs):
        if not self.client_token:
            self.client_token = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user.get_full_name()} — {self.test.title}"


class UserAnswer(models.Model):
    session = models.ForeignKey(
        UserTestSession, on_delete=models.CASCADE, related_name="answers"
    )
    question = models.ForeignKey(Question, on_delete=models.CASCADE)
    selected_options = models.ManyToManyField(AnswerOption, blank=True)
    uploaded_file = models.FileField(upload_to="user_files/", null=True, blank=True)
    code_answer = models.TextField(blank=True)

    def __str__(self):
        return f"Ответ {self.session.user.get_full_name()} — {self.question.id}"
