from django.contrib import admin
from django.utils.html import format_html
from .models import Test, Question, AnswerOption, UserTestSession, UserAnswer
from .forms import QuestionAdminForm


class AnswerOptionInline(admin.TabularInline):
    model = AnswerOption
    extra = 2
    fields = ("display_id", "text", "image_preview", "image", "is_correct")
    readonly_fields = ("display_id", "image_preview")

    def display_id(self, obj):
        return format_html('<span style="color: #888;">{}</span>', obj.id)

    display_id.short_description = "ID"

    def image_preview(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" width="80" style="border-radius:8px;"/>', obj.image.url
            )
        return "—"

    image_preview.short_description = "Миниатюра"


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    form = QuestionAdminForm
    list_display = ("text_short", "test", "question_type", "image_preview")
    list_filter = ("test", "question_type")
    search_fields = ("text",)
    inlines = [AnswerOptionInline]
    readonly_fields = ("image_preview",)

    fieldsets = (
        (
            None,
            {"fields": ("test", "text", "question_type", "correct_order")},
        ),
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

    image_preview.short_description = "Миниатюра"


@admin.register(Test)
class TestAdmin(admin.ModelAdmin):
    list_display = ("title", "groups_preview", "duration_minutes", "created_at")
    search_fields = ("title",)
    inlines = [QuestionInline]
    fieldsets = (
        (None, {"fields": ("title", "description", "groups")}),
        ("Настройки", {"fields": ("duration_minutes", "max_warnings")}),
    )

    def groups_preview(self, obj):
        return ", ".join(g.name for g in obj.groups.all()) or "—"

    groups_preview.short_description = "Группы"


@admin.register(UserTestSession)
class UserTestSessionAdmin(admin.ModelAdmin):
    list_display = (
        "get_user_full_name",
        "test",
        "score_percent",
        "tab_switches",
        "time_outside_seconds",
        "submitted_due_to_violation",
        "finished_at",
    )
    readonly_fields = ("client_token", "last_heartbeat")
    list_filter = ("test", "test__groups", "submitted_due_to_violation")
    search_fields = ("user__first_name", "user__last_name", "test__title")

    def get_user_full_name(self, obj):
        return obj.user.get_full_name()

    get_user_full_name.short_description = "Пользователь"
    get_user_full_name.admin_order_field = "user__last_name"


@admin.register(UserAnswer)
class UserAnswerAdmin(admin.ModelAdmin):
    list_display = (
        "session",
        "question",
        "selected_options_display",
        "short_text_answer",
    )
    list_filter = ("question__test",)
    search_fields = ("session__user__username", "question__text")

    def selected_options_display(self, obj):
        opts = [opt.text for opt in obj.selected_options.all()]
        return ", ".join(opts) if opts else "—"

    selected_options_display.short_description = "Выбранные ответы"

    def short_text_answer(self, obj):
        if obj.text_answer:
            return (
                (obj.text_answer[:70] + "...")
                if len(obj.text_answer) > 70
                else obj.text_answer
            )
        return "—"

    short_text_answer.short_description = "Текстовый ответ"
