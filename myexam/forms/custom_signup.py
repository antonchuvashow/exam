from django import forms
from allauth.account.forms import SignupForm
from django.contrib.auth.models import Group

class CustomSignupForm(SignupForm):
    first_name = forms.CharField(
        max_length=30,
        label='Имя',
        widget=forms.TextInput(attrs={'placeholder': 'Введите ваше имя'})
    )
    last_name = forms.CharField(
        max_length=30,
        label='Фамилия',
        widget=forms.TextInput(attrs={'placeholder': 'Введите вашу фамилию'})
    )
    group = forms.ModelChoiceField(
        queryset=Group.objects.all(),
        label='Группа',
        required=True
    )
    email = forms.EmailField(required=False)
    username = forms.CharField(
        max_length=20,
        label='Имя Пользователя',
        widget=forms.TextInput(attrs={'placeholder': 'Придумайте имя пользователя'})
    )

    def save(self, request):
        user = super().save(request)
        user.first_name = self.cleaned_data['first_name']
        user.last_name = self.cleaned_data['last_name']
        user.save()
        group = self.cleaned_data['group']
        user.groups.add(group)
        return user

