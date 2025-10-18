from django.contrib import admin, messages
from django.utils.html import format_html
from django.core.exceptions import ValidationError
from django.urls import path
from django.shortcuts import render, redirect
from .models import Test, Question, AnswerOption, UserTestSession, UserAnswer
from .forms import QuestionAdminForm, TestImportForm
import json


@admin.action(description="Пересчитать баллы ответов автоматически")
def recalc_answers_auto(modeladmin, request, queryset):
    for ans in queryset:
        ans.recalc_points_auto()
    modeladmin.message_user(request, f"Авто-баллы пересчитаны для {queryset.count()} ответов")

@admin.action(description="Пересчитать баллы сессии по текущим баллам ответов")
def recalc_sessions_from_answers(modeladmin, request, queryset):
    for session in queryset:
        session.recalc_score_from_answers()
    modeladmin.message_user(request, f"Баллы пересчитаны для {queryset.count()} сессий")

@admin.action(description="Пересчитать сессии автоматически по правильным ответам")
def recalc_sessions_auto(modeladmin, request, queryset):
    for session in queryset:
        for ans in session.answers.all():
            ans.recalc_points_auto()
        session.recalc_score_from_answers()
    modeladmin.message_user(request, f"Авто-баллы пересчитаны для {queryset.count()} сессий")


class AnswerOptionInline(admin.TabularInline):
    model = AnswerOption
    extra = 2
    fields = ("display_index", "text", "image", "is_correct")
    readonly_fields = ("display_index",)

    def display_index(self, obj):
        options = list(AnswerOption.objects.filter(question=obj.question).order_by("id"))
        try:
            idx = options.index(obj) + 1
        except ValueError:
            idx = "—"
        return idx

    display_index.short_description = "Позиция (относительный номер)"

    def image_preview(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" width="80" style="border-radius:8px;"/>', obj.image.url
            )
        return "—"

    image_preview.short_description = "Миниатюра изображения"

@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    form = QuestionAdminForm
    list_display = ("text_short", "test", "question_type", "image_preview")
    list_filter = ("test", "question_type")
    search_fields = ("text",)
    inlines = [AnswerOptionInline]
    readonly_fields = ("image_preview",)

    fieldsets = (
        ("Основное", {
        "fields": ("test", "text", "question_type", "points", "correct_order"),
        "description": "Для вопросов типа 'Упорядочивание' указывайте позиции опций через запятую: 1,2,3…"}),
        ("Изображение", {"fields": ("image_preview", "image")}),
        ("Дополнительно", {"fields": ("metadata",)}),
    )

    def text_short(self, obj):
        return (obj.text[:60] + "...") if len(obj.text) > 60 else obj.text

    text_short.short_description = "Текст вопроса"

    def image_preview(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" width="120" style="border-radius:8px;"/>', obj.image.url
            )
        return "—"

    image_preview.short_description = "Миниатюра"


class QuestionInline(admin.TabularInline):
    model = Question
    extra = 0
    fields = ("text", "question_type", "image_preview", "image")
    readonly_fields = ("image_preview",)
    show_change_link = True

    def image_preview(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" width="60" style="border-radius:6px;"/>', obj.image.url
            )
        return "—"

    image_preview.short_description = "Миниатюра изображения"


@admin.register(Test)
class TestAdmin(admin.ModelAdmin):
    list_display = ("title", "groups_preview", "duration_minutes", "created_at")
    search_fields = ("title",)
    inlines = [QuestionInline]
    fieldsets = (
        ("Основное", {"fields": ("title", "description", "groups")}),
        ("Настройки теста", {"fields": ("duration_minutes", "max_warnings", "show_answers")}),
    )

    def groups_preview(self, obj):
        return ", ".join(g.name for g in obj.groups.all()) or "—"

    groups_preview.short_description = "Группы доступа"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "import-json/",
                self.admin_site.admin_view(self.import_json_view),
                name="tests_test_import_json",
            ),
        ]
        return custom_urls + urls

    def import_json_view(self, request):
        """Страница импорта теста из JSON"""
        form = TestImportForm(request.POST or None)

        if request.method == "POST" and form.is_valid():
            try:
                content = form.cleaned_data["json_data"]
                test = self.import_test_from_json(content)
                messages.success(request, f"Тест «{test.title}» успешно импортирован!")
                return redirect("..")
            except Exception as e:
                messages.error(request, f"Ошибка при импорте: {e}")

        context = dict(
            self.admin_site.each_context(request),
            title="Импорт теста из JSON-файла",
            form=form,
        )
        return render(request, "admin/import_test.html", context)

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["custom_button"] = True
        return super().changelist_view(request, extra_context=extra_context)

    def import_test_from_json(self, json_data):
        """Импортирует тест из JSON-объекта или строки."""
        if isinstance(json_data, str):
            try:
                data = json.loads(json_data)
            except json.JSONDecodeError as e:
                raise ValidationError(f"Ошибка парсинга JSON: {e}")
        else:
            data = json_data

        # Создание теста
        test_fields = {f.name for f in Test._meta.fields}
        test_data = {k: v for k, v in data.items() if k in test_fields}
        test = Test.objects.create(**test_data)

        # Создание вопросов и вариантов
        for q_data in data.get("questions", []):
            options_data = q_data.pop("options", [])
            metadata = q_data.pop("metadata", {}) or {}
            question = Question.objects.create(test=test, metadata=metadata, **q_data)

            for opt_data in options_data:
                AnswerOption.objects.create(question=question, **opt_data)

        return test


@admin.register(UserTestSession)
class UserTestSessionAdmin(admin.ModelAdmin):
    list_display = (
        "get_user_full_name",
        "test",
        "score_percent",
        "tab_switches",
        "time_outside_seconds",
        "submitted_due_to_violation",
        "started_at",
        "finished_at",
    )
    readonly_fields = ("client_token", "last_heartbeat")
    list_filter = ("test", "test__groups", "submitted_due_to_violation")
    search_fields = ("user__first_name", "user__last_name", "test__title")
    actions = [recalc_sessions_from_answers, recalc_sessions_auto]

    def get_user_full_name(self, obj):
        return obj.user.get_full_name()

    get_user_full_name.short_description = "Пользователь"
    get_user_full_name.admin_order_field = "user__last_name"


@admin.register(UserAnswer)
class UserAnswerAdmin(admin.ModelAdmin):
    list_display = (
        "session",
        "question",
        "points_scored",
        "max_points_display"
    )
    list_filter = ("question__test", "question__test__groups")
    search_fields = ("session__user__first_name", "session__user__last_name", "question__test__groups__name", "question__text")
    actions = [recalc_answers_auto]
    readonly_fields = ("max_points_display",)

    def max_points_display(self, obj):
        if obj.question:
            return obj.question.points
        return "—"
    max_points_display.short_description = "Макс. балл"

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)

        if obj and obj.question_id:
            form.base_fields['selected_options'].queryset = AnswerOption.objects.filter(
                question=obj.question
            )
            max_points = obj.question.points
            points_field = form.base_fields['points_scored']
            points_field.widget.attrs['max'] = max_points
            points_field.widget.attrs['step'] = 0.01
            points_field.help_text = f"Максимум: {max_points}"
        return form
