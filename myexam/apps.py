import os
from django.apps import AppConfig

class MyExamConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'myexam'

    def ready(self):
        domain = os.environ.get("SITE_DOMAIN", "localhost:8000")
        site_name = os.environ.get("SITE_NAME", "My Exam")

        try:
            from django.contrib.sites.models import Site
            Site.objects.update_or_create(
                id=1,
                defaults={"domain": domain, "name": site_name}
            )
        except Exception:
            pass
