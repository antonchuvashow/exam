from django import template
from django.db.models import Count, Avg
from ..models import UserTestSession, Test, Grade

register = template.Library()

@register.inclusion_tag('admin/dashboard/partials/latest_sessions.html')
def latest_sessions(count=10):
    sessions = UserTestSession.objects.select_related('user', 'test', 'grade').order_by('-finished_at')[:count]
    return {'sessions': sessions}

@register.inclusion_tag('admin/dashboard/partials/grade_distribution.html')
def grade_distribution_chart():
    grades = Grade.objects.annotate(session_count=Count('usertestsession')).filter(session_count__gt=0)
    return {
        'labels': [g.grade_name for g in grades],
        'data': [g.session_count for g in grades],
    }

@register.inclusion_tag('admin/dashboard/partials/average_score.html')
def average_score_chart():
    tests = Test.objects.annotate(avg_score=Avg('usertestsession__score_percent')).filter(avg_score__isnull=False)
    return {
        'labels': [t.title for t in tests],
        'data': [round(t.avg_score, 2) for t in tests],
    }
