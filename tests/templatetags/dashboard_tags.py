from django import template
from django.db.models import Count, Avg, F
from django.contrib.auth.models import Group
from ..models import UserTestSession, Test, Grade, User

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

@register.inclusion_tag('admin/dashboard/partials/group_average_scores.html')
def group_average_scores():
    groups = Group.objects.annotate(avg_score=Avg('user__usertestsession__score_percent')).filter(avg_score__isnull=False)
    return {
        'labels': [g.name for g in groups],
        'data': [round(g.avg_score, 2) for g in groups],
    }

@register.inclusion_tag('admin/dashboard/partials/test_performance.html')
def test_performance_summary():
    tests = Test.objects.annotate(avg_score=Avg('usertestsession__score_percent')).filter(avg_score__isnull=False).order_by('-avg_score')
    return {
        'best_tests': tests[:5],
        'worst_tests': tests.reverse()[:5],
    }

@register.inclusion_tag('admin/dashboard/partials/top_students.html')
def top_students(count=10):
    students = User.objects.annotate(
        avg_score=Avg('usertestsession__score_percent'),
        test_count=Count('usertestsession')
    ).filter(avg_score__isnull=False).order_by('-avg_score')[:count]
    return {'students': students}
