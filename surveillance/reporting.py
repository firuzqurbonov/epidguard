from collections import Counter, defaultdict
from django.db.models import Q
from .forms import ReportFilterForm
from .access import cases_for

AGE_LABELS = ['<1', '1–4', '5–14', '15–17', '18–19', '20–29', '30–39', '40–49', '50–59', '60+', 'Неизвестен']
AGE_BOUNDS = {'<1': (0, 1), '1–4': (1, 5), '5–14': (5, 15), '15–17': (15, 18), '18–19': (18, 20),
              '20–29': (20, 30), '30–39': (30, 40), '40–49': (40, 50), '50–59': (50, 60), '60+': (60, 1000), 'children': (0, 18)}


def filtered_report(user, params):
    form = ReportFilterForm(params)
    qs = cases_for(user).exclude(status='draft')
    missing = 0
    if not form.is_valid():
        return form, qs.none(), missing
    data = form.cleaned_data
    for key, field in [('include_cancelled', 'status'), ('include_excluded', 'diagnosis')]:
        if not data.get(key):
            qs = qs.exclude(**{field: 'cancelled' if key == 'include_cancelled' else 'excluded'})
    if not data.get('include_duplicates'):
        qs = qs.filter(duplicate_of__isnull=True)
    for key in ['disease', 'territory', 'diagnosis']:
        if data.get(key):
            qs = qs.filter(**{key: data[key]})
    if data.get('sex'):
        qs = qs.filter(patient_sex=data['sex'])
    if data.get('group'):
        qs = qs.filter(disease__groups=data['group']).distinct()
    age = data.get('age_group')
    if age == 'unknown':
        qs = qs.filter(age_at_onset__isnull=True)
    elif age in AGE_BOUNDS:
        low, high = AGE_BOUNDS[age]
        qs = qs.filter(age_at_onset__gte=low, age_at_onset__lt=high)
    field = data.get('date_field') or 'detected_date'
    missing = qs.filter(**{field + '__isnull': True}).count()
    lookup = field + '__date' if field == 'registered_at' else field
    if data.get('start'):
        qs = qs.filter(**{lookup + '__gte': data['start']})
    if data.get('end'):
        qs = qs.filter(**{lookup + '__lte': data['end']})
    # An unknown date cannot be assigned to a selected reporting period.
    return form, qs, missing


def aggregate_report(cases, group_by='disease', language='ru'):
    buckets = defaultdict(list)
    for case in cases:
        if group_by == 'disease':
            key = getattr(case.disease, 'name_' + language)
        elif group_by == 'territory':
            key = str(case.territory or 'Неизвестно')
        else:
            key = getattr(case, group_by, '') or 'Не указано'
        buckets[key].append(case)
    rows = []
    for key, values in sorted(buckets.items()):
        counts = Counter(c.age_group for c in values)
        rows.append({'name': key, 'total': len(values), 'ages': [counts[x] for x in AGE_LABELS],
                     'children': sum(c.age_at_onset is not None and c.age_at_onset < 18 for c in values)})
    return rows
