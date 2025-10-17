from allauth.socialaccount.forms import SignupForm as SignupForm
from django import forms
from django.contrib.auth.models import Group
from django.utils.translation import gettext_lazy as _


class SocialSignupForm(SignupForm):
    first_name = forms.CharField(max_length=30, required=True, label=_("Имя"))
    last_name = forms.CharField(max_length=30, required=True, label=_("Фамилия"))
    group = forms.ModelChoiceField(
        queryset=Group.objects.all(),
        required=True,
        label=_("Группа"),
        empty_label=_("Выберите группу"),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop("username")

    def save(self, request):
        user = super().save(request)

        user.first_name = self.cleaned_data.get("first_name", "")
        user.last_name = self.cleaned_data.get("last_name", "")
        group = self.cleaned_data.get("group")
        user.groups.add(group)

        return user