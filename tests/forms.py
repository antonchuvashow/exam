from django import forms
from django.core.exceptions import ValidationError
from .models import Question, AnswerOption


class QuestionAdminForm(forms.ModelForm):
    correct_order = forms.CharField(
        required=False,
        label="Правильный порядок (через запятую)",
        help_text="Введите ID ответов в правильном порядке, например: 10, 5, 11",
    )

    class Meta:
        model = Question
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        q = self.instance
        if (
            q
            and q.question_type == "order"
            and q.metadata
            and q.metadata.get("correct_order")
        ):
            self.fields["correct_order"].initial = ", ".join(
                map(str, q.metadata["correct_order"])
            )

    def clean_correct_order(self):
        order_str = self.cleaned_data.get("correct_order", "").strip()
        if not order_str:
            self._parsed_correct_order = []
            return ""

        parts = [p.strip() for p in order_str.split(",") if p.strip()]
        parsed = []
        for p in parts:
            try:
                parsed.append(int(p))
            except ValueError:
                raise ValidationError(
                    "Все идентификаторы должны быть целыми числами, разделёнными запятыми."
                )

        if self.instance and self.instance.pk:
            existing_ids = set(self.instance.options.values_list("id", flat=True))
            unknown = [str(i) for i in parsed if i not in existing_ids]
            if unknown:
                raise ValidationError(
                    f"Указаны несуществующие ID вариантов для этого вопроса: {', '.join(unknown)}"
                )

        self._parsed_correct_order = parsed
        return ", ".join(map(str, parsed))

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("question_type") == "order":
            metadata = cleaned_data.get("metadata") or {}
            metadata = dict(metadata)
            parsed = getattr(self, "_parsed_correct_order", [])
            metadata["correct_order"] = parsed

            cleaned_data["metadata"] = metadata
            self.instance.metadata = metadata

        return cleaned_data
