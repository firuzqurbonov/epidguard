import base64
import csv
import hashlib
import hmac
import io
import json
import secrets
import struct
import time
from collections import Counter
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction, IntegrityError
from django.db.models import Q, Count
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from .models import *
from .forms import *
from .access import cases_for, is_epi, can_edit, can_manage, service_ids, outbreaks_for, signals_for, profile
from .services import audit as log, transition, snapshot, capture_age, claim_version, revisions, ConflictError
from .reporting import filtered_report, aggregate_report, AGE_LABELS
from .i18n import tr


def language_for(request):
    p = profile(request.user)
    return p.language if p else 'ru'


def clinical_create_required(user):
    p = profile(user)
    if not user.is_superuser and (not p or not p.approved or p.role == 'administrator'):
        raise PermissionDenied


def epi_required(user):
    if not is_epi(user):
        raise PermissionDenied


def page(request, qs, size=20):
    return Paginator(qs, size).get_page(request.GET.get('page'))


@login_required
def dashboard(request):
    qs = cases_for(request.user)
    counted = qs.exclude(status__in=['draft', 'cancelled']).exclude(diagnosis='excluded').filter(duplicate_of__isnull=True)
    today = timezone.localdate()
    dates = [today - timedelta(days=i) for i in reversed(range(14))]
    counts = Counter(counted.filter(detected_date__gte=dates[0]).values_list('detected_date', flat=True))
    bars = [{'label': day.strftime('%d.%m'), 'value': counts[day]} for day in dates]
    disease_counts = counted.values('disease__name_' + language_for(request)).annotate(total=Count('id', distinct=True)).order_by('-total')[:5]
    top = [{'name': x['disease__name_' + language_for(request)], 'count': x['total']} for x in disease_counts]
    max_cases = max([x['count'] for x in top] or [1])
    for row in top:
        row['percentage'] = round(row['count'] / max_cases * 100)
    notifications = Notification.objects.filter(case__in=qs, viewed_at__isnull=True)
    if not request.user.is_superuser:
        notifications = notifications.filter(service_id__in=service_ids(request.user))
    return render(request, 'surveillance/dashboard.html', {'title': 'Обзор', 'active': 'dashboard',
        'total': counted.count(), 'children': counted.filter(age_at_onset__lt=18).count(),
        'active_count': counted.filter(status__in=['active', 'transfer']).count(), 'incoming': notifications.count(),
        'recent': qs[:7], 'chart': bars, 'top_diseases': top,
        'unrouted': qs.filter(status='submitted', responsible_service__isnull=True).count(),
        'overdue': Measure.objects.filter(case__in=qs, completed_at__isnull=True, due_date__lt=today).count(),
        'missing_data': counted.filter(Q(territory__isnull=True) | Q(age_at_onset__isnull=True)).count()})


@login_required
def case_list(request):
    qs = cases_for(request.user)
    term = request.GET.get('q', '').strip()[:200]
    if term:
        qs = qs.filter(Q(patient_name__icontains=term) | Q(patient__medical_id__icontains=term) | Q(number__icontains=term))
    disease = request.GET.get('disease', '')
    territory = request.GET.get('territory', '')
    status = request.GET.get('status', '')
    if disease.isdigit():
        qs = qs.filter(disease_id=disease)
    if territory.isdigit():
        qs = qs.filter(territory_id=territory)
    if status in Case.Status.values:
        qs = qs.filter(status=status)
    mode = request.GET.get('mode', 'active')
    if mode == 'active':
        qs = qs.exclude(status='cancelled')
    elif mode == 'cancelled':
        qs = qs.filter(status='cancelled')
    if request.GET.get('unrouted'):
        qs = qs.filter(status='submitted', responsible_service__isnull=True)
    return render(request, 'surveillance/cases.html', {'title': 'Реестр случаев', 'active': 'cases', 'records': page(request, qs),
        'diseases': Disease.objects.filter(active=True), 'territories': Territory.objects.all(), 'statuses': Case.Status.choices, 'mode': mode})


@login_required
def case_create(request):
    clinical_create_required(request.user)
    language = language_for(request)
    initial = {'disease': request.GET.get('disease'), 'detected_date': timezone.localdate()}
    form = CaseForm(request.POST or None, user=request.user, language=language, initial=initial)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                existing = Case.objects.filter(submission_key=form.cleaned_data['submission_key']).first()
                if existing:
                    get_object_or_404(cases_for(request.user), pk=existing.pk)
                    return redirect('case_detail', pk=existing.pk)
                patient = form.cleaned_data.get('existing_patient')
                if not patient:
                    fields = {key: form.cleaned_data.get(key) for key in ['full_name', 'birth_date', 'sex', 'phone', 'approximate_age']}
                    if form.cleaned_data.get('medical_id'):
                        fields['medical_id'] = form.cleaned_data['medical_id']
                    patient = Patient.objects.create(**fields)
                case = form.save(commit=False)
                case.patient = patient
                case.creator = request.user
                case.institution = profile(request.user).institution if profile(request.user) else None
                case.patient_name, case.patient_sex = patient.full_name, patient.sex
                case.birth_date_snapshot = patient.birth_date
                case.age_at_onset = patient.approximate_age
                case.age_approximate = not bool(patient.birth_date)
                case.submission_key = form.cleaned_data['submission_key']
                case.questionnaire = form.questionnaire
                case.questionnaire_snapshot = {'title': form.questionnaire.title, 'version': form.questionnaire.version, 'approved': form.questionnaire.approved, 'questions': form.questions} if form.questionnaire else {}
                case.answers = form.answers
                capture_age(case)
                case.save()
                log(request.user, 'Создание черновика', case, after=snapshot(case))
            messages.success(request, 'Черновик сохранён. Проверьте данные и отправьте извещение.')
            return redirect('case_detail', pk=case.pk)
        except IntegrityError:
            existing = Case.objects.filter(submission_key=form.cleaned_data['submission_key'], creator=request.user).first()
            if existing:
                return redirect('case_detail', pk=existing.pk)
            form.add_error(None, 'Этот медицинский ID уже зарегистрирован. Обновите список пациентов.')
    return render(request, 'surveillance/case_form.html', {'form': form, 'title': 'Добавить случай', 'active': 'cases', 'creating': True})


@login_required
def case_edit(request, pk):
    case = get_object_or_404(cases_for(request.user), pk=pk)
    if not can_edit(request.user, case):
        raise PermissionDenied
    # Keep an independent DB copy: ModelForm mutates its instance during validation.
    old_snapshot = snapshot(case)
    form = CaseForm(request.POST or None, instance=case, user=request.user, language=language_for(request))
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                current = Case.objects.select_for_update().get(pk=pk)
                if not can_edit(request.user, current):
                    raise PermissionDenied
                claim_version(current, form.cleaned_data['version'])
                updated = form.save(commit=False)
                updated.version = current.version
                updated.questionnaire = form.questionnaire
                updated.questionnaire_snapshot = {'title': form.questionnaire.title, 'version': form.questionnaire.version, 'approved': form.questionnaire.approved, 'questions': form.questions} if form.questionnaire else {}
                updated.answers = form.answers
                capture_age(updated)
                updated.save()
                if updated.status != 'draft':
                    revisions(updated)
                log(request.user, 'Исправление случая', updated, form.cleaned_data['comment'], old_snapshot, snapshot(updated))
            messages.success(request, 'Изменения сохранены в истории. При изменении адреса проверьте ответственную СЭС.')
            return redirect('case_detail', pk=pk)
        except ConflictError as exc:
            form.add_error(None, exc)
    return render(request, 'surveillance/case_form.html', {'form': form, 'case': case, 'title': 'Исправление случая', 'active': 'cases'})


@login_required
def case_detail(request, pk):
    case = get_object_or_404(cases_for(request.user), pk=pk)
    notices = case.notifications.select_related('service')
    if not request.user.is_superuser:
        notices.filter(service_id__in=service_ids(request.user), viewed_at__isnull=True).update(viewed_at=timezone.now())
    log(request.user, 'Просмотр случая', case)
    duplicates = cases_for(request.user).filter(patient=case.patient, disease=case.disease).exclude(pk=case.pk).exclude(status__in=['cancelled', 'draft'])
    pending = case.transfers.filter(status='pending').first()
    can_receive = pending and is_epi(request.user) and (request.user.is_superuser or pending.target_id in service_ids(request.user))
    q_answers = [{'label': q.get('label_' + language_for(request), q.get('label_ru')), 'value': case.answers.get(q['key'], '—')} for q in case.questionnaire_snapshot.get('questions', []) if q['key'] in case.answers]
    return render(request, 'surveillance/case_detail.html', {'title': str(case), 'active': 'cases', 'case': case,
        'editable': can_edit(request.user, case), 'manageable': can_manage(request.user, case),
        'can_submit': case.status == 'draft' and (request.user.is_superuser or case.creator_id == request.user.pk),
        'notices': notices, 'services': Service.objects.exclude(level='hospital'),
        'pending_transfer': pending, 'can_receive': can_receive, 'duplicates': duplicates,
        'question_answers': q_answers, 'history': case.audit_entries.select_related('actor')[:30],
        'contacts_list': case.contacts.select_related('patient', 'linked_case'), 'measures_list': case.measures.all(),
        'lab_list': case.labs.select_related('replaces', 'creator').prefetch_related('corrections').order_by('-created_at'),
        'attachment_form': AttachmentForm(), 'attachments': case.attachments.all()})


@login_required
@require_POST
def case_action(request, pk):
    case = get_object_or_404(cases_for(request.user), pk=pk)
    target = Service.objects.filter(pk=request.POST.get('target')).first() if request.POST.get('target', '').isdigit() else None
    duplicate = None
    if request.POST.get('duplicate'):
        try:
            duplicate = get_object_or_404(cases_for(request.user), pk=request.POST['duplicate'])
        except (ValueError, ValidationError):
            messages.error(request, 'Некорректный ID основного случая.')
            return redirect('case_detail', pk=pk)
    try:
        version = int(request.POST.get('version', '0'))
        transition(pk, request.user, request.POST.get('action'), version, request.POST.get('reason', ''), target, duplicate)
        messages.success(request, 'Действие выполнено и записано в журнал.')
    except (ValidationError, ValueError) as exc:
        messages.error(request, '; '.join(exc.messages) if isinstance(exc, ValidationError) else 'Некорректная версия записи.')
    return redirect('case_detail', pk=pk)


@login_required
def inbox(request):
    qs = Notification.objects.filter(case__in=cases_for(request.user)).select_related('case__disease', 'service')
    if not request.user.is_superuser:
        qs = qs.filter(service_id__in=service_ids(request.user))
    if request.GET.get('unread'):
        qs = qs.filter(viewed_at__isnull=True)
    return render(request, 'surveillance/inbox.html', {'title': 'Входящие извещения', 'active': 'inbox', 'records': page(request, qs.order_by('-created_at'))})


@login_required
def related_list(request, kind):
    scoped = cases_for(request.user)
    config = {'contacts': (Contact, 'Контактные лица'), 'measures': (Measure, 'Мероприятия'), 'laboratory': (LabResult, 'Лаборатория')}
    model, title = config[kind]
    qs = model.objects.filter(case__in=scoped).select_related('case__disease').order_by('-pk')
    if kind == 'measures' and request.GET.get('overdue'):
        qs = qs.filter(completed_at__isnull=True, due_date__lt=timezone.localdate())
    contacts_count = None
    if kind == 'contacts':
        contacts_count = len({str(c.patient_id or c.identity_key) for c in qs})
    return render(request, 'surveillance/related_list.html', {'title': title, 'active': kind, 'kind': kind, 'records': page(request, qs), 'unique_contacts': contacts_count})


@login_required
def related_create(request, pk, kind):
    case = get_object_or_404(cases_for(request.user), pk=pk)
    allowed = can_manage(request.user, case) or (kind == 'laboratory' and can_edit(request.user, case))
    if not allowed or case.status in ['draft', 'cancelled', 'completed']:
        raise PermissionDenied
    config = {'contacts': (ContactForm, 'Добавить контакт'), 'measures': (MeasureForm, 'Добавить мероприятие'), 'laboratory': (LabForm, 'Добавить результат')}
    form_cls, title = config[kind]
    kwargs = {'user': request.user} if kind == 'contacts' else {'case': case} if kind == 'laboratory' else {}
    form = form_cls(request.POST or None, **kwargs)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            locked = Case.objects.select_for_update().get(pk=pk)
            allowed = can_manage(request.user, locked) or (kind == 'laboratory' and can_edit(request.user, locked))
            if not allowed or locked.status in ['draft', 'cancelled', 'completed']:
                raise PermissionDenied
            obj = form.save(commit=False)
            obj.case = locked
            if kind == 'contacts' and form.cleaned_data.get('same_person'):
                same = form.cleaned_data['same_person']
                obj.identity_key = same.identity_key
                if not obj.patient_id:
                    obj.patient = same.patient
            if kind != 'contacts':
                obj.creator = request.user
            obj.save()
            revisions(locked)
            log(request.user, title, locked, after={key: str(value) for key, value in form.cleaned_data.items()})
        messages.success(request, 'Запись добавлена.')
        return redirect('case_detail', pk=pk)
    return render(request, 'surveillance/form.html', {'title': title, 'active': kind, 'form': form, 'back': reverse('case_detail', args=[pk])})


@login_required
@require_POST
def measure_complete(request, pk):
    obj = get_object_or_404(Measure.objects.select_related('case'), pk=pk, case__in=cases_for(request.user))
    with transaction.atomic():
        case = Case.objects.select_for_update().get(pk=obj.case_id)
        obj.refresh_from_db()
        if not can_manage(request.user, case) or case.status in ['cancelled', 'completed', 'draft']:
            raise PermissionDenied
        result = request.POST.get('result', '').strip()
        if not result:
            messages.error(request, 'Укажите результат мероприятия.')
        elif obj.completed_at:
            messages.info(request, 'Мероприятие уже выполнено.')
        else:
            obj.completed_at, obj.result = timezone.now(), result
            obj.save()
            log(request.user, 'Выполнение мероприятия', case, result, after={'measure_id': obj.pk})
    return redirect('case_detail', pk=obj.case_id)


@login_required
def contact_edit(request, pk):
    obj = get_object_or_404(Contact.objects.select_related('case'), pk=pk, case__in=cases_for(request.user))
    case = obj.case
    if not can_manage(request.user, case) or case.status in ['draft', 'cancelled', 'completed']:
        raise PermissionDenied
    before = {key: str(getattr(obj, key)) for key in ['full_name', 'category', 'observation_until', 'status', 'notes', 'patient_id', 'linked_case_id', 'identity_key']}
    form = ContactForm(request.POST or None, instance=obj, user=request.user)
    form.fields['reason'] = forms.CharField(label='Основание изменения', widget=forms.Textarea(attrs={'rows': 2}))
    form.fields['version'] = forms.IntegerField(initial=case.version, widget=forms.HiddenInput)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                current = Case.objects.select_for_update().get(pk=case.pk)
                if not can_manage(request.user, current) or current.status in ['draft', 'cancelled', 'completed']:
                    raise PermissionDenied
                claim_version(current, form.cleaned_data['version'])
                current.save(update_fields=['version', 'updated_at'])
                obj = form.save(commit=False)
                same = form.cleaned_data.get('same_person')
                if same:
                    obj.identity_key = same.identity_key
                    if not obj.patient_id:
                        obj.patient = same.patient
                obj.save()
                revisions(current)
                after = {key: str(getattr(obj, key)) for key in before}
                log(request.user, 'Наблюдение контактного лица', current, form.cleaned_data['reason'], before, after)
            messages.success(request, 'Наблюдение обновлено, прежние сведения сохранены в истории.')
            return redirect('case_detail', pk=case.pk)
        except ConflictError as exc:
            form.add_error(None, exc)
    return render(request, 'surveillance/form.html', {'title': 'Наблюдение контактного лица', 'active': 'contacts', 'form': form, 'back': reverse('case_detail', args=[case.pk])})


@login_required
def outbreaks(request):
    epi_required(request.user)
    return render(request, 'surveillance/outbreaks.html', {'title': 'Очаги', 'active': 'outbreaks', 'records': page(request, outbreaks_for(request.user).order_by('-opened_date'))})


@login_required
def outbreak_create(request):
    epi_required(request.user)
    initial = {}
    if request.GET.get('case'):
        case = get_object_or_404(cases_for(request.user), pk=request.GET['case'])
        initial = {'cases': [case.pk], 'disease': case.disease, 'territory': case.territory, 'service': case.responsible_service}
    form = OutbreakForm(request.POST or None, user=request.user, initial=initial)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            obj = form.save(commit=False)
            obj.creator = request.user
            obj.save()
            form.save_m2m()
            for case in obj.cases.all():
                log(request.user, 'Установлен очаг', case, obj.description, after={'outbreak_id': obj.pk})
            log(request.user, 'Создание очага', detail=f'ID {obj.pk}', after={'service_id': obj.service_id})
        return redirect('outbreak_detail', pk=obj.pk)
    return render(request, 'surveillance/form.html', {'title': 'Новый очаг', 'active': 'outbreaks', 'form': form, 'back': reverse('outbreaks')})


@login_required
def outbreak_detail(request, pk):
    obj = get_object_or_404(outbreaks_for(request.user), pk=pk)
    if request.method == 'POST':
        reason = request.POST.get('reason', '').strip()
        if reason and obj.status == 'active':
            with transaction.atomic():
                obj.status, obj.closed_reason = 'closed', reason
                obj.save()
                log(request.user, 'Завершение очага', detail=reason, after={'outbreak_id': obj.pk, 'service_id': obj.service_id})
            messages.success(request, 'Очаг завершён.')
        else:
            messages.error(request, 'Укажите основание завершения.')
        return redirect('outbreak_detail', pk=pk)
    scoped = cases_for(request.user).filter(outbreaks=obj)
    log(request.user, 'Просмотр очага', detail=f'ID {obj.pk}', after={'service_id': obj.service_id})
    return render(request, 'surveillance/outbreak_detail.html', {'title': obj.title, 'active': 'outbreaks', 'outbreak': obj, 'records': scoped})


@login_required
def signals(request):
    epi_required(request.user)
    return render(request, 'surveillance/signals.html', {'title': 'Сигналы', 'active': 'signals', 'records': page(request, signals_for(request.user).order_by('-created_at'))})


@login_required
def signal_edit(request, pk=None):
    epi_required(request.user)
    obj = get_object_or_404(signals_for(request.user), pk=pk) if pk else None
    form = SignalForm(request.POST or None, instance=obj, user=request.user)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            obj = form.save(commit=False)
            if not obj.pk:
                obj.creator = request.user
            obj.save()
            log(request.user, 'Сохранение сигнала', detail=f'ID {obj.pk}', after={'service_id': obj.service_id, 'status': obj.status})
        return redirect('signals')
    return render(request, 'surveillance/form.html', {'title': 'Новый сигнал' if not pk else 'Проверка сигнала', 'active': 'signals', 'form': form, 'back': reverse('signals')})


@login_required
def reports(request):
    lang = language_for(request)
    form, qs, missing = filtered_report(request.user, request.GET)
    group_by = form.cleaned_data.get('group_by') or 'disease' if form.is_valid() else 'disease'
    cases = list(qs)
    rows = aggregate_report(cases, group_by, lang)
    totals = {'total': len(cases), 'ages': [sum(row['ages'][i] for row in rows) for i in range(len(AGE_LABELS))], 'children': sum(row['children'] for row in rows)}
    selected = [{'label': tr(form[key].label, lang), 'value': str(value)} for key, value in (form.cleaned_data.items() if form.is_valid() else []) if value not in [None, '', False]]
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()[:160]
        if name and form.is_valid():
            ReportTemplate.objects.create(owner=request.user, name=name, filters=request.GET.dict())
            messages.success(request, 'Шаблон условий сохранён.')
        return redirect(request.get_full_path())
    if request.GET.get('export') == 'xlsx' and form.is_valid():
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        wb = Workbook()
        ws = wb.active
        ws.title = 'EpiGuard'
        ws.append(['EpiGuard · ' + tr('Отчёты', lang), timezone.localtime().isoformat()])
        for condition in selected:
            ws.append([condition['label'], condition['value']])
        ws.append(['Неизвестная выбранная дата до ограничения периодом', missing])
        ws.append(['0–17 — дополнительный итог, не суммируется с возрастными группами.'])
        ws.append([tr('Группа', lang), tr('Всего случаев', lang)] + [tr(x, lang) for x in AGE_LABELS] + ['0–17'])
        header_row = ws.max_row
        for row in rows:
            # Explicit text cell type prevents spreadsheet formula injection.
            ws.append([row['name'], row['total']] + row['ages'] + [row['children']])
            ws.cell(ws.max_row, 1).data_type = 's'
        ws.append([tr('Все', lang), totals['total']] + totals['ages'] + [totals['children']])
        for row in ws:
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = 's'
        for cell in ws[header_row]:
            cell.font = Font(color='FFFFFF', bold=True)
            cell.fill = PatternFill('solid', fgColor='12685E')
        ws.column_dimensions['A'].width = 48
        for letter in 'BCDEFGHIJKLMN':
            ws.column_dimensions[letter].width = 15
        ws.freeze_panes = f'C{header_row + 1}'
        output = io.BytesIO()
        wb.save(output)
        log(request.user, 'Выгрузка отчёта', detail='XLSX', after={'filters': request.GET.dict(), 'count': len(cases)})
        response = HttpResponse(output.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = 'attachment; filename="epiguard-report.xlsx"'
        return response
    log(request.user, 'Построение отчёта', after={'filters': request.GET.dict(), 'count': len(cases)})
    return render(request, 'surveillance/reports.html', {'title': 'Отчёты', 'active': 'reports', 'form': form, 'rows': rows,
        'ages': AGE_LABELS, 'totals': totals, 'missing_date': missing, 'selected': selected, 'generated_at': timezone.now(),
        'patients': len({c.patient_id for c in cases}), 'school_cases': sum(c.education_type == 'school' for c in cases),
        'kindergarten_cases': sum(c.education_type == 'kindergarten' for c in cases),
        'schools': len({c.education_name for c in cases if c.education_type == 'school' and c.education_name}),
        'kindergartens': len({c.education_name for c in cases if c.education_type == 'kindergarten' and c.education_name}),
        'templates': [{'name': t.name, 'query': urlencode(t.filters)} for t in ReportTemplate.objects.filter(owner=request.user).order_by('-created_at')[:20]]})


@login_required
def map_view(request):
    form, qs, missing = filtered_report(request.user, request.GET)
    groups = qs.values('territory__id', 'territory__name', 'territory__latitude', 'territory__longitude').annotate(total=Count('pk', distinct=True))
    points, unmapped = [], 0
    for group in groups:
        if group['territory__latitude'] is None or group['territory__longitude'] is None:
            unmapped += group['total']
        else:
            points.append({'id': group['territory__id'], 'name': group['territory__name'], 'lat': group['territory__latitude'], 'lon': group['territory__longitude'], 'count': group['total']})
    log(request.user, 'Просмотр карты', after={'filters': request.GET.dict(), 'count': qs.count()})
    return render(request, 'surveillance/map.html', {'title': 'Карта', 'active': 'map', 'form': form, 'points': points, 'unmapped': unmapped, 'total': qs.count()})


@login_required
def disease_catalog(request):
    qs = Disease.objects.prefetch_related('groups', 'questionnaires').all()
    term = request.GET.get('q', '').strip()[:100]
    if term:
        qs = qs.filter(Q(name_ru__icontains=term) | Q(name_tg__icontains=term) | Q(code__icontains=term))
    return render(request, 'surveillance/diseases.html', {'title': 'Справочник заболеваний', 'active': 'diseases', 'records': qs, 'groups': DiseaseGroup.objects.all()})


@login_required
def questionnaire_api(request, pk):
    clinical_create_required(request.user)
    if request.GET.get('case'):
        case = get_object_or_404(cases_for(request.user), pk=request.GET['case'])
        if case.disease_id == pk and case.questionnaire_id:
            frozen = case.questionnaire_snapshot
            return JsonResponse({'version': frozen.get('version'), 'approved': frozen.get('approved', False), 'questions': frozen.get('questions', []), 'language': language_for(request)})
    q = Questionnaire.objects.filter(disease_id=pk, effective_from__lte=timezone.localdate()).order_by('-version').first()
    return JsonResponse({'version': q.version if q else None, 'approved': q.approved if q else False, 'questions': q.questions if q else [], 'language': language_for(request)})


@login_required
def audit_view(request):
    scoped = cases_for(request.user)
    qs = Audit.objects.select_related('actor', 'case')
    if not request.user.is_superuser:
        qs = qs.filter(Q(case__in=scoped) | Q(actor=request.user, case__isnull=True))
    term = request.GET.get('q', '').strip()[:100]
    if term:
        qs = qs.filter(Q(action__icontains=term) | Q(case__number__icontains=term))
    return render(request, 'surveillance/audit.html', {'title': 'История действий', 'active': 'audit', 'records': page(request, qs, 30)})


@login_required
@require_POST
def upload_attachment(request, pk):
    case = get_object_or_404(cases_for(request.user), pk=pk)
    if not can_edit(request.user, case):
        raise PermissionDenied
    form = AttachmentForm(request.POST, request.FILES)
    if form.is_valid():
        with transaction.atomic():
            obj = form.save(commit=False)
            obj.case, obj.author = case, request.user
            obj.original_name = Path(request.FILES['file'].name).name[:240]
            obj.save()
            log(request.user, 'Добавление документа', case, after={'attachment': obj.pk, 'name': obj.original_name})
        messages.success(request, 'Документ сохранён в закрытом хранилище.')
    else:
        messages.error(request, form.errors.as_text())
    return redirect('case_detail', pk=pk)


@login_required
def download_attachment(request, pk):
    obj = get_object_or_404(Attachment, pk=pk, case__in=cases_for(request.user))
    log(request.user, 'Скачивание документа', obj.case, after={'attachment': obj.pk})
    response = FileResponse(obj.file.open('rb'), as_attachment=True, filename=obj.original_name, content_type='application/octet-stream')
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@login_required
@require_POST
def set_language(request):
    lang = request.POST.get('language', 'ru')
    if lang in ['ru', 'tg', 'en']:
        p = profile(request.user)
        if p:
            p.language = lang
            p.save(update_fields=['language'])
        request.session['language'] = lang
    url = request.POST.get('next', '/')
    return redirect(url if url.startswith('/') and not url.startswith('//') else '/')


def totp(secret, step):
    key = base64.b32decode(secret, casefold=True)
    digest = hmac.new(key, struct.pack('>Q', step), hashlib.sha1).digest()
    offset = digest[-1] & 15
    return str((struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7fffffff) % 1000000).zfill(6)


@login_required
def security(request):
    p, _ = Profile.objects.get_or_create(user=request.user, defaults={'approved': request.user.is_superuser})
    if not p.totp_enabled and not request.session.get('enroll_secret'):
        request.session['enroll_secret'] = base64.b32encode(secrets.token_bytes(20)).decode()
    secret = p.totp_secret if p.totp_enabled else request.session['enroll_secret']
    error = None
    if request.method == 'POST':
        key = f'otp:{request.user.pk}'
        if cache.get(key, 0) >= 5:
            error = 'Слишком много попыток. Подождите 15 минут.'
        elif not p.totp_enabled and not request.user.check_password(request.POST.get('password', '')):
            error = 'Для подключения введите текущий пароль.'
            cache.set(key, cache.get(key, 0) + 1, 900)
        else:
            code = request.POST.get('code', '').strip()
            step = int(time.time() // 30)
            valid_step = next((s for s in [step - 1, step, step + 1] if s > p.totp_last_step and hmac.compare_digest(totp(secret, s), code)), None)
            if valid_step is None:
                cache.set(key, cache.get(key, 0) + 1, 900)
                error = 'Код неверный или уже использован.'
            else:
                # Atomic update prevents replay across simultaneous login sessions.
                with transaction.atomic():
                    changed = Profile.objects.filter(pk=p.pk, totp_last_step__lt=valid_step).update(totp_secret=secret, totp_enabled=True, totp_last_step=valid_step)
                    if not changed:
                        error = 'Код уже использован. Дождитесь следующего.'
                    else:
                        request.session['mfa_verified'] = True
                        request.session.pop('enroll_secret', None)
                        log(request.user, 'Подтверждение двухфакторного входа')
                if not error:
                    cache.delete(key)
                    return redirect('dashboard')
    return render(request, 'surveillance/security.html', {'title': 'Двухфакторный вход', 'active': 'settings', 'enrolling': not p.totp_enabled, 'secret': secret if not p.totp_enabled else '', 'error': error, 'mfa_required': settings.MFA_REQUIRED})


@login_required
def settings_view(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            user = form.save()
            update_session_auth_hash(request, user)
            log(request.user, 'Изменение пароля')
        messages.success(request, 'Пароль изменён.')
        return redirect('settings')
    return render(request, 'surveillance/settings.html', {'title': 'Настройки', 'active': 'settings', 'form': form, 'mfa_required': settings.MFA_REQUIRED})
