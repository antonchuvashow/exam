from django.urls import path
from . import views

app_name = "tests"

urlpatterns = [
    path("", views.test_list, name="test_list"),
    path("<int:test_id>/start/", views.start_test, name="start_test"),
    path("session/<int:session_id>/result/", views.test_result, name="test_result"),
    path("session/<int:session_id>/", views.take_test, name="take_test"),
    path("session/<int:session_id>/submit/", views.submit_test, name="submit_test"),
    # AJAX endpoints
    path("session/<int:session_id>/heartbeat/", views.heartbeat, name="heartbeat"),
    path("session/<int:session_id>/warn/", views.warn, name="warn"),
]
