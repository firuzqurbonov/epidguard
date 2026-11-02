from django.db.models import Q
from .models import Case, Outbreak, Signal, Service


def profile(user):
    return getattr(user, 'profile', None)


def is_epi(user):
    p = profile(user)
    return user.is_superuser or bool(p and p.approved and p.role == 'epidemiologist')


def service_ids(user):
    if user.is_superuser:
        return Service.objects.values_list('id', flat=True)
    p = profile(user)
    return p.services.values_list('id', flat=True) if p and p.approved else []


def cases_for(user):
    qs = Case.objects.select_related('patient', 'disease', 'territory', 'creator', 'responsible_service', 'questionnaire')
    if not user.is_authenticated:
        return qs.none()
    if user.is_superuser:
        return qs
    p = profile(user)
    if not p or not p.approved or p.role == 'administrator':
        return qs.none()
    if p.role == 'epidemiologist':
        ids = service_ids(user)
        return qs.filter(Q(responsible_service_id__in=ids) | Q(notifications__service_id__in=ids) | Q(creator=user)).distinct()
    return qs.filter(creator=user)


def can_edit(user, case):
    if case.status in [Case.Status.CANCELLED, Case.Status.COMPLETED]:
        return False
    if user.is_superuser:
        return True
    p = profile(user)
    if not p or not p.approved:
        return False
    if p.role == 'epidemiologist':
        return case.responsible_service_id in service_ids(user) or (case.creator_id == user.pk and case.status == Case.Status.DRAFT)
    return p.role in ['clinician', 'laboratory'] and case.creator_id == user.pk and case.status in [Case.Status.DRAFT, Case.Status.SUBMITTED]


def can_manage(user, case):
    return is_epi(user) and (user.is_superuser or case.responsible_service_id in service_ids(user))


def outbreaks_for(user):
    qs = Outbreak.objects.select_related('disease', 'territory', 'service')
    return qs if user.is_superuser else qs.filter(service_id__in=service_ids(user)) if is_epi(user) else qs.none()


def signals_for(user):
    qs = Signal.objects.select_related('territory', 'service', 'outbreak')
    if user.is_superuser:
        return qs
    return qs.filter(service_id__in=service_ids(user)) if is_epi(user) else qs.none()
