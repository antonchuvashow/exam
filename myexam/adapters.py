from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib.auth.models import Group


class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    def save_user(self, request, sociallogin, form=None):
        """
        Переопределяем сохранение пользователя для обработки дополнительных полей
        """
        user = super().save_user(request, sociallogin, form)

        # Сохраняем дополнительные поля из формы
        if form and hasattr(form, "cleaned_data"):
            user.first_name = form.cleaned_data.get("first_name", "")
            user.last_name = form.cleaned_data.get("last_name", "")
            user.save()

            # Добавляем в группу
            group = form.cleaned_data.get("group")
            if group:
                user.groups.add(group)

        return user

    def is_auto_signup_allowed(self, request, sociallogin):
        """
        Запрещаем авто-регистрацию, чтобы использовать нашу форму
        """
        return False
