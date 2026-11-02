from datetime import date
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import F
from django.forms.models import model_to_dict
from django.utils import timezone
from .models import Case, EpidNumber, Notification, Route, Audit, Transfer
from .access import can_manage, is_epi, service_ids


class ConflictError(ValidationError):
    pass


def audit(user, action, case=None, detail='', before=None, after=None):
    return Audit.objects.create(actor=user, action=action, case=case, detail=detail, before=before or {}, after=after or {})


def snapshot(case):
    fields = ['disease_id', 'status', 'diagnosis', 'territory_id', 'residence', 'registered_address', 'stay_address',
              'infection_address', 'onset_date', 'detected_date', 'confirmed_date', 'clinic', 'family_doctor',
              'education_type', 'education_name', 'workplace', 'hospitalized', 'severity', 'outcome', 'vaccination',
              'vaccination_source', 'vaccination_reason', 'notes', 'answers', 'questionnaire_snapshot', 'responsible_service_id', 'version']
    return {key: (getattr(case, key).isoformat() if isinstance(getattr(case, key), date) else getattr(case, key)) for key in fields}


def claim_version(case, expected):
    if not Case.objects.filter(pk=case.pk, version=expected).update(version=F('version') + 1):
        raise ConflictError('Запись уже изменена другим сотрудником. Откройте актуальную версию; введённые сведения сохранены на этой странице.')
    case.version = expected + 1


def capture_age(case):
    reference = case.onset_date or case.detected_date
    birth = case.birth_date_snapshot
    if birth:
        case.age_at_onset = reference.year - birth.year - ((reference.month, reference.day) < (birth.month, birth.day))


def route_case(case):
    routes = Route.objects.filter(territory=case.territory).select_related('service')
    lead = routes.filter(is_lead=True).first()
    if lead:
        case.responsible_service = lead.service
    for route in routes:
        Notification.objects.get_or_create(case=case, service=route.service)
    return bool(lead)


def revisions(case):
    Notification.objects.filter(case=case).update(revision=F('revision') + 1, viewed_at=None)


@transaction.atomic
def transition(case_id, user, action, version, reason='', target=None, duplicate=None):
    case = Case.objects.select_for_update().get(pk=case_id)
    before = snapshot(case)
    is_manager = can_manage(user, case)
    pending = case.transfers.filter(status='pending').first()
    if action == 'submit':
        if case.creator_id != user.pk and not user.is_superuser:
            raise PermissionDenied
        if case.status != Case.Status.DRAFT:
            raise ValidationError('Отправить можно только черновик.')
        if not case.residence or not case.territory:
            raise ValidationError('Для отправки заполните фактический адрес и территорию. Если адрес неизвестен, укажите это явно.')
        case.status = Case.Status.SUBMITTED
        route_case(case)
    elif action == 'accept':
        if not is_manager or case.status != Case.Status.SUBMITTED:
            raise PermissionDenied
        # Explicit prototype policy: number issued by leading epidemiologist on acceptance.
        sequence, _ = EpidNumber.objects.get_or_create(case=case)
        case.number = f'EP-{timezone.localdate().year}-{sequence.pk:08d}'
        case.registered_at = timezone.now()
        case.status = Case.Status.ACTIVE
        case.assignee = user
        Notification.objects.filter(case=case, service=case.responsible_service).update(accepted_at=timezone.now())
    elif action in ['cancel', 'complete', 'reopen', 'transfer', 'duplicate', 'assign']:
        if not is_manager and not (action == 'assign' and user.is_superuser):
            raise PermissionDenied
        if not reason.strip():
            raise ValidationError('Укажите основание и подробный комментарий.')
        if action == 'cancel':
            if case.status in [Case.Status.CANCELLED, Case.Status.TRANSFER]:
                raise ValidationError('Сначала завершите передачу; отменённую запись нельзя отменить повторно.')
            case.status = Case.Status.CANCELLED
            case.cancellation_reason = reason
        elif action == 'complete':
            if case.status != Case.Status.ACTIVE:
                raise ValidationError('Завершить можно расследование в работе.')
            if case.measures.filter(completed_at__isnull=True).exists():
                raise ValidationError('Есть невыполненные мероприятия.')
            case.status = Case.Status.COMPLETED
        elif action == 'reopen':
            if case.status != Case.Status.COMPLETED:
                raise ValidationError('Повторно открыть можно завершённое расследование.')
            case.status = Case.Status.ACTIVE
        elif action == 'transfer':
            if case.status != Case.Status.ACTIVE or not target or target == case.responsible_service or target.level == 'hospital':
                raise ValidationError('Выберите другую СЭС для случая в работе.')
            Transfer.objects.create(case=case, source=case.responsible_service, target=target, author=user, reason=reason)
            Notification.objects.get_or_create(case=case, service=target)
            case.status = Case.Status.TRANSFER
        elif action == 'assign':
            if case.status != Case.Status.SUBMITTED or case.responsible_service_id or not target or target.level == 'hospital':
                raise ValidationError('Назначение доступно для ненаправленного извещения.')
            case.responsible_service = target
            Notification.objects.get_or_create(case=case, service=target)
        elif action == 'duplicate':
            if case.status not in [Case.Status.SUBMITTED, Case.Status.ACTIVE] or case.duplicate_of_id:
                raise ValidationError('Этот случай нельзя отметить дублем.')
            if not duplicate or duplicate.pk == case.pk or duplicate.duplicate_of_id or duplicate.status in [Case.Status.CANCELLED, Case.Status.DRAFT] or duplicate.patient_id != case.patient_id or duplicate.disease_id != case.disease_id:
                raise ValidationError('Основной случай должен относиться к тому же пациенту и инфекции и не быть дублем, черновиком или отменённым.')
            if case.duplicates.exists():
                raise ValidationError('У записи уже есть связанные дубли. Сначала разберите связи.')
            case.duplicate_of = duplicate
            case.status = Case.Status.COMPLETED
    elif action in ['transfer_accept', 'transfer_return']:
        if not pending or not is_epi(user) or (not user.is_superuser and pending.target_id not in service_ids(user)):
            raise PermissionDenied
        if not reason.strip():
            raise ValidationError('Укажите комментарий к решению.')
        pending.status = 'accepted' if action == 'transfer_accept' else 'returned'
        pending.response = reason
        pending.decided_at = timezone.now()
        pending.save()
        if action == 'transfer_accept':
            case.responsible_service = pending.target
            case.assignee = user
            Notification.objects.filter(case=case, service=pending.target).update(accepted_at=timezone.now())
        case.status = Case.Status.ACTIVE
    else:
        raise ValidationError('Неизвестное действие.')
    claim_version(case, version)
    case.save()
    audit(user, action, case, reason, before, snapshot(case))
    return case
