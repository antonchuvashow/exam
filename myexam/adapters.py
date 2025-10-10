from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib.auth import get_user_model

User = get_user_model()


class GoogleAccountAdapter(DefaultSocialAccountAdapter):
    def pre_social_login(self, request, sociallogin):
        email = sociallogin.account.extra_data.get("email")
        if not email:
            return
        try:
            existing_user = User.objects.get(email__iexact=email)
            sociallogin.connect(request, existing_user)
        except User.DoesNotExist:
            pass
