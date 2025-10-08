from django.contrib import admin
from django.utils.html import format_html
from .models import Test, Question, AnswerOption, UserTestSession, UserAnswer


class AnswerOptionInline(admin.TabularInline):
    model = AnswerOption
    extra = 2
    fields = ("text", "image_preview", "image", "is_correct")
    readonly_fields = ("image_preview",)

    def image_preview(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" width="80" style="border-radius:8px;"/>', obj.image.url
            )
        return "—"

    image_preview.short_description = "Миниатюра"


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("text_short", "test", "question_type", "image_preview")
    list_filter = ("test", "question_type")
    search_fields = ("text",)
    inlines = [AnswerOptionInline]
    readonly_fields = ("image_preview",)

    fieldsets = (
        (None, {"fields": ("test", "text", "question_type")}),
        ("Изображение", {"fields": ("image_preview", "image")}),
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
        (
            None,
            {
                "fields": (
                    "title",
                    "description",
                    "groups",
                )
            },
        ),
        ("Настройки", {"fields": ("duration_minutes", "max_warnings")}),
    )

    def groups_preview(self, obj):
        return ", ".join(g.name for g in obj.groups.all())


@admin.register(UserTestSession)
class UserTestSessionAdmin(admin.ModelAdmin):
    list_display = (
        "get_user_full_name",
        "test",
        "tab_switches",
        "submitted_due_to_violation",
        "score_percent",
    )
    readonly_fields = ("client_token", "last_heartbeat")

    list_filter = ("test", "submitted_due_to_violation")
    search_fields = ("user__get_ful_name", "test__title")

    def get_user_full_name(self, obj):
        return obj.user.get_full_name()

    get_user_full_name.short_description = "User"
    get_user_full_name.admin_order_field = "user__last_name"


@admin.register(UserAnswer)
class UserAnswerAdmin(admin.ModelAdmin):
    list_display = ("session", "question", "selected_options_display", "file_preview")
    list_filter = ("question__test",)
    search_fields = ("session__user__username", "question__text")
    readonly_fields = ("file_preview",)

    def selected_options_display(self, obj):
        return ", ".join(a.text for a in obj.selected_options.all())

    selected_options_display.short_description = "Выбранные ответы"

    def file_preview(self, obj):
        if obj.uploaded_file:
            file_url = obj.uploaded_file.url
            return format_html(
                '<a href="{}" target="_blank">Скачать файл</a>', file_url
            )
        return "—"

    file_preview.short_description = "Ответ (файл)"
