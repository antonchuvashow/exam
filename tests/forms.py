from django import forms
from django.core.exceptions import ValidationError
from .models import Question

class QuestionAdminForm(forms.ModelForm):
    correct_order = forms.CharField(
        required=False,
        label="Правильный порядок (через запятую)",
        help_text="Введите номера ответов в правильном порядке, например: 1, 2, 3",
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
                num = int(p)
                if num < 1:
                    raise ValueError
                parsed.append(num)
            except ValueError:
                raise ValidationError(
                    "Все номера должны быть положительными целыми числами, разделёнными запятыми."
                )

        if self.instance and self.instance.pk:
            total_options = self.instance.options.count()
            out_of_range = [str(i) for i in parsed if i > total_options]
            if out_of_range:
                raise ValidationError(
                    f"Указанные номера выходят за диапазон доступных вариантов (1–{total_options}): {', '.join(out_of_range)}"
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


class TestImportForm(forms.Form):
    json_data = forms.CharField(
        label="JSON-тест",
        widget=forms.Textarea(attrs={"rows": 20, "cols": 100}),
        initial="""{
  "title": "Пример теста со всеми видами вопросов",
  "description": "Описание теста, должно в себя включать полезную информацию для тестируемых",
  "duration_minutes": 20,
  "max_warnings": 3,
  "show_answers": true,
  "questions": [
    {
      "text": "Какой язык мы используем для этого теста?",
      "question_type": "single",
      "points": 1,
      "options": [
        {"text": "Python", "is_correct": true},
        {"text": "Java", "is_correct": false},
        {"text": "C++", "is_correct": false},
        {"text": "JavaScript", "is_correct": false}
      ]
    },
    {
      "text": "Выберите правильные утверждения о Python:",
      "question_type": "multiple",
      "points": 3,
      "options": [
        {"text": "Python — интерпретируемый язык", "is_correct": true},
        {"text": "Python компилируется в байт-код (интерпретатор создаёт байт-код)", "is_correct": true},
        {"text": "Python строго типизированный язык", "is_correct": false},
        {"text": "Python поддерживает многопоточность (GIL ограничивает параллелизм в CPython)", "is_correct": true},
        {"text": "Python нельзя использовать для веб-разработки", "is_correct": false}
      ],
      "metadata": {
        "partial_scoring": true
      }
    },
    {
      "text": "Кто создал язык Python?",
      "question_type": "text",
      "points": 1,
      "options": [
        {"text": "Guido van Rossum", "is_correct": true},
        {"text": "Гвидо ван Россум", "is_correct": true},
        {"text": "Не знаю", "is_correct": false}
      ],
      "metadata": {
        "semantic_threshold": 0.65,
        "full_credit_threshold": 0.92,
        "incorrect_threshold": 0.90,
        "penalty_weight": 1.0,
        "correction_factor": 0.6,
        "min_partial": 0.6,
        "topk_incorrect": 3,
        "aspect_weight": 0.3,
        "length_penalty_min_ratio": 0.4
      }
    },
    {
      "text": "Опишите ключевые особенности языка Python",
      "question_type": "long_text",
      "points": 4,
      "options": [
        {"text": "Высокоуровневый, интерпретируемый язык с динамической типизацией.", "is_correct": true},
        {"text": "Отлично подходит для прототипирования, анализа данных и научных вычислений.", "is_correct": true},
        {"text": "Поддерживает ООП и функциональные возможности.", "is_correct": true},
        {"text": "Синтаксис прост и читаем.", "is_correct": true},
        {"text": "Низкоуровневый компилируемый язык.", "is_correct": false},
        {"text": "Предназначен только для мобильных приложений.", "is_correct": false}
      ],
      "metadata": {
        "semantic_threshold": 0.60,
        "full_credit_threshold": 0.92,
        "incorrect_threshold": 0.90,
        "penalty_weight": 1.0,
        "correction_factor": 0.6,
        "min_partial": 0.6,
        "topk_incorrect": 3,
        "aspect_weight": 0.6,
        "length_penalty_min_ratio": 0.5
      }
    },
    {
      "text": "Сколько байт обычно занимает тип int в Python (32-битная архитектура)?",
      "question_type": "number",
      "points": 1,
      "options": [
        {"text": "4", "is_correct": true}
      ],
      "metadata": {
        "tolerance": 0
      }
    },
    {
      "text": "Упорядочите шаги для запуска Python скрипта (подразумевается, что Python ещё не установлен на системе):",
      "question_type": "order",
      "points": 3,
      "options": [
        {"text": "Установите Python, если он не установлен"},
        {"text": "Откройте терминал"},
        {"text": "Перейдите в каталог с файлом"},
        {"text": "Выполните команду python filename.py"}
      ],
      "metadata": {
        "correct_order": [1, 2, 3, 4],
        "allow_partial": true
      }
    }
  ]
}

"""
    )
