import hashlib
import uuid
from datetime import date
from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.utils import timezone
from .models import Case, Patient, Disease, DiseaseGroup, Territory, Service, Contact, Measure, LabResult, Outbreak, Signal, Questionnaire, Attachment
from .access import cases_for, service_ids


class SecureAuthenticationForm(AuthenticationForm):
    def clean(self):
        key_data = f'{self.request.META.get("REMOTE_ADDR", "")}|{self.cleaned_data.get("username", "").lower()}'
        self.attempt_key = 'login:' + hashlib.sha256(key_data.encode()).hexdigest()
        ip_key = 'login-ip:' + hashlib.sha256(self.request.META.get('REMOTE_ADDR', '').encode()).hexdigest()
        if cache.get(self.attempt_key, 0) >= 5 or cache.get(ip_key, 0) >= 30:
            raise ValidationError('Слишком много попыток. Попробуйте через 15 минут.')
        try:
            data = super().clean()
            user = self.get_user()
            p = getattr(user, 'profile', None)
            if not user.is_superuser and (not p or not p.approved):
                raise ValidationError('Доступ ещё не подтверждён администратором.')
        except ValidationError:
            cache.set(self.attempt_key, cache.get(self.attempt_key, 0) + 1, 900)
            cache.set(ip_key, cache.get(ip_key, 0) + 1, 900)
            raise
        cache.delete(self.attempt_key)
        return data


class DateInput(forms.DateInput):
    input_type = 'date'

    def __init__(self, attrs=None, format=None):
        super().__init__(attrs=attrs, format=format or '%Y-%m-%d')


class CaseForm(forms.ModelForm):
    existing_patient = forms.ModelChoiceField(label='Ранее зарегистрированный пациент', queryset=Patient.objects.none(), required=False, empty_label='Новый пациент')
    full_name = forms.CharField(label='ФИО пациента', max_length=200, required=False)
    medical_id = forms.CharField(label='Внешний медицинский ID (если известен)', max_length=80, required=False)
    birth_date = forms.DateField(label='Дата рождения', required=False, widget=DateInput())
    approximate_age = forms.IntegerField(label='Возраст, если дата неизвестна', required=False, min_value=0, max_value=120)
    sex = forms.ChoiceField(label='Пол', choices=Patient._meta.get_field('sex').choices, initial='u')
    phone = forms.CharField(label='Телефон', required=False, max_length=60)
    comment = forms.CharField(label='Основание изменения', required=False, widget=forms.Textarea(attrs={'rows': 2}))
    version = forms.IntegerField(widget=forms.HiddenInput, initial=1)
    submission_key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)

    class Meta:
        model = Case
        fields = ['disease', 'diagnosis', 'onset_date', 'detected_date', 'confirmed_date', 'territory', 'residence', 'registered_address', 'stay_address', 'infection_address', 'address_quality', 'clinic', 'family_doctor', 'education_type', 'education_name', 'workplace', 'hospitalized', 'severity', 'outcome', 'vaccination', 'vaccination_source', 'vaccination_reason', 'notes']
        labels = {'disease': 'Заболевание'}
        widgets = {**{key: DateInput() for key in ['onset_date', 'detected_date', 'confirmed_date']}, 'notes': forms.Textarea(attrs={'rows': 4})}

    def __init__(self, *args, user, language='ru', **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields['existing_patient'].queryset = Patient.objects.filter(cases__in=cases_for(user)).distinct().order_by('full_name')
        self.fields['disease'].queryset = Disease.objects.filter(active=True)
        self.questionnaire = None
        self.questions = []
        if self.instance.pk and not self.instance._state.adding:
            p = self.instance.patient
            for key, value in {'version': self.instance.version, 'full_name': p.full_name, 'medical_id': p.medical_id, 'birth_date': p.birth_date, 'sex': p.sex, 'phone': p.phone, 'approximate_age': p.approximate_age}.items():
                self.initial[key] = value
            for key in ['existing_patient', 'full_name', 'medical_id', 'birth_date', 'sex', 'phone', 'approximate_age']:
                self.fields[key].disabled = True
            self.fields['existing_patient'].required = False
        disease_id = self.data.get('disease') if self.is_bound else self.instance.disease_id or self.initial.get('disease')
        if str(disease_id).isdigit():
            if self.instance.questionnaire_id and str(self.instance.disease_id) == str(disease_id):
                self.questionnaire = self.instance.questionnaire
                self.questions = self.instance.questionnaire_snapshot.get('questions', [])
            else:
                self.questionnaire = Questionnaire.objects.filter(disease_id=disease_id, effective_from__lte=timezone.localdate()).order_by('-version').first()
                self.questions = self.questionnaire.questions if self.questionnaire else []
        for question in self.questions:
            key = 'q_' + question['key']
            label = question.get('label_' + language, question.get('label_ru', question['key']))
            opts = {'label': label, 'required': False}
            if question.get('type') == 'select':
                self.fields[key] = forms.ChoiceField(**opts, choices=[('', '—')] + [(v, v) for v in question.get('options', [])])
            elif question.get('type') == 'date':
                self.fields[key] = forms.DateField(**opts, widget=DateInput())
            else:
                self.fields[key] = forms.CharField(**opts, max_length=3000, widget=forms.Textarea(attrs={'rows': 2}))
            self.initial[key] = self.instance.answers.get(question['key'], '')
            if question.get('show_if'):
                cond = question['show_if']
                self.fields[key].widget.attrs.update({'data-depends': 'q_' + cond['key'], 'data-value': cond['value']})
        self.order_fields(['existing_patient', 'full_name', 'medical_id', 'birth_date', 'approximate_age', 'sex', 'phone'] + list(self.Meta.fields) + ['comment', 'version', 'submission_key'])

    @property
    def question_fields(self):
        return [self['q_' + q['key']] for q in self.questions]

    def clean(self):
        data = super().clean()
        today = timezone.localdate()
        if self.instance._state.adding and not data.get('existing_patient'):
            if not data.get('full_name', '').strip():
                self.add_error('full_name', 'Укажите ФИО или выберите существующего пациента.')
            if data.get('medical_id') and Patient.objects.filter(medical_id=data['medical_id']).exists():
                self.add_error('medical_id', 'Этот ID уже существует. Выберите доступного пациента или обратитесь к уполномоченному сотруднику.')
        if not self.instance._state.adding and not data.get('comment', '').strip():
            self.add_error('comment', 'Укажите основание исправления.')
        birth = data.get('birth_date')
        if data.get('existing_patient'):
            birth = data['existing_patient'].birth_date
        for field in ['birth_date', 'onset_date', 'detected_date', 'confirmed_date']:
            if data.get(field) and data[field] > today:
                self.add_error(field, 'Дата не может быть в будущем.')
        reference = data.get('onset_date') or data.get('detected_date')
        if birth and reference and birth > reference:
            self.add_error('onset_date', 'Дата заболевания раньше даты рождения.')
        if data.get('onset_date') and data.get('detected_date') and data['onset_date'] > data['detected_date']:
            self.add_error('onset_date', 'Начало симптомов позже выявления. Проверьте даты.')
        if data.get('confirmed_date') and data.get('detected_date') and data['confirmed_date'] < data['detected_date']:
            self.add_error('confirmed_date', 'Подтверждение раньше выявления.')
        if data.get('diagnosis') == 'confirmed' and not data.get('confirmed_date'):
            self.add_error('confirmed_date', 'Укажите дату подтверждения.')
        if data.get('education_type') in ['school', 'kindergarten'] and not data.get('education_name'):
            self.add_error('education_name', 'Укажите учреждение.')
        self.answers = {}
        for q in self.questions:
            visible = not q.get('show_if') or str(data.get('q_' + q['show_if']['key'], '')) == str(q['show_if']['value'])
            answer = data.get('q_' + q['key']) if visible else None
            if visible and q.get('required') and not answer:
                self.add_error('q_' + q['key'], 'Обязательный вопрос.')
            if answer is not None:
                self.answers[q['key']] = answer.isoformat() if isinstance(answer, date) else answer
        return data


class ContactForm(forms.ModelForm):
    same_person = forms.ModelChoiceField(label='Это тот же человек, что ранее добавленный контакт', queryset=Contact.objects.none(), required=False)

    class Meta:
        model = Contact
        fields = ['patient', 'full_name', 'category', 'contact_date', 'observation_until', 'notes', 'status', 'linked_case']
        widgets = {'contact_date': DateInput(), 'observation_until': DateInput(), 'notes': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        scoped = cases_for(user)
        self.fields['same_person'].queryset = Contact.objects.filter(case__in=scoped).exclude(pk=self.instance.pk).order_by('full_name')
        self.fields['same_person'].label_from_instance = lambda obj: f'{obj.full_name} · {obj.case}'
        self.fields['patient'].queryset = Patient.objects.filter(cases__in=scoped).distinct()
        self.fields['linked_case'].queryset = scoped.exclude(status__in=['draft', 'cancelled'])

    def clean(self):
        data = super().clean()
        if data.get('status') == 'ill' and not data.get('linked_case'):
            self.add_error('linked_case', 'Свяжите контакт с зарегистрированным случаем заболевания.')
        if data.get('patient') and data.get('linked_case') and data['patient'].pk != data['linked_case'].patient_id:
            self.add_error('linked_case', 'В связанном случае другой пациент.')
        same = data.get('same_person')
        if same and same.patient_id and data.get('patient') and same.patient_id != data['patient'].pk:
            self.add_error('same_person', 'Выбран контакт другого пациента.')
        if data.get('contact_date') and data.get('observation_until') and data['observation_until'] < data['contact_date']:
            self.add_error('observation_until', 'Наблюдение заканчивается раньше контакта.')
        return data


class MeasureForm(forms.ModelForm):
    class Meta:
        model = Measure
        fields = ['kind', 'description', 'authority_document', 'assignee', 'due_date']
        widgets = {'due_date': DateInput(), 'description': forms.Textarea(attrs={'rows': 3})}

    def clean(self):
        data = super().clean()
        if data.get('kind') == 'quarantine' and not data.get('authority_document'):
            self.add_error('authority_document', 'Укажите уполномоченный орган и документ решения.')
        return data


class LabForm(forms.ModelForm):
    class Meta:
        model = LabResult
        fields = ['sample', 'collected_date', 'result_date', 'laboratory', 'method', 'pathogen', 'result', 'replaces', 'comment']
        widgets = {'collected_date': DateInput(), 'result_date': DateInput(), 'comment': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, case, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['replaces'].queryset = case.labs.all()
        self.fields['replaces'].label = 'Исправляет предыдущий результат'

    def clean(self):
        data = super().clean()
        if data.get('replaces') and not data.get('comment', '').strip():
            self.add_error('comment', 'Укажите основание исправления.')
        if data.get('replaces') and data['replaces'].corrections.exists():
            self.add_error('replaces', 'Результат уже исправлен. Выберите актуальную запись.')
        if data.get('result_date') and data.get('collected_date') and data['result_date'] < data['collected_date']:
            self.add_error('result_date', 'Результат раньше взятия пробы.')
        for key in ['collected_date', 'result_date']:
            if data.get(key) and data[key] > timezone.localdate():
                self.add_error(key, 'Дата не может быть в будущем.')
        if data.get('result') != 'pending' and not data.get('result_date'):
            self.add_error('result_date', 'Укажите дату результата.')
        return data


class OutbreakForm(forms.ModelForm):
    class Meta:
        model = Outbreak
        fields = ['title', 'disease', 'territory', 'service', 'opened_date', 'description', 'source', 'cases']
        labels = {'disease': 'Заболевание', 'territory': 'Территория', 'service': 'Ответственная СЭС', 'cases': 'Связанные случаи'}
        widgets = {'opened_date': DateInput(), 'description': forms.Textarea(attrs={'rows': 3}), 'source': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['cases'].queryset = cases_for(user).exclude(status__in=['draft', 'cancelled']).filter(duplicate_of__isnull=True)
        self.fields['service'].queryset = Service.objects.exclude(level='hospital') if user.is_superuser else Service.objects.filter(pk__in=service_ids(user)).exclude(level='hospital')

    def clean(self):
        data = super().clean()
        if data.get('opened_date') and data['opened_date'] > timezone.localdate():
            self.add_error('opened_date', 'Дата не может быть в будущем.')
        if data.get('disease') and any(c.disease_id != data['disease'].pk for c in data.get('cases', [])):
            self.add_error('cases', 'В очаге должны быть случаи выбранной инфекции.')
        return data


class SignalForm(forms.ModelForm):
    class Meta:
        model = Signal
        fields = ['title', 'territory', 'service', 'source', 'description', 'status', 'outbreak']
        labels = {'territory': 'Территория', 'service': 'Ответственная СЭС', 'outbreak': 'Связанный очаг'}
        widgets = {'description': forms.Textarea(attrs={'rows': 4})}

    def __init__(self, *args, user, **kwargs):
        from .access import outbreaks_for
        super().__init__(*args, **kwargs)
        self.fields['service'].queryset = Service.objects.exclude(level='hospital') if user.is_superuser else Service.objects.filter(pk__in=service_ids(user)).exclude(level='hospital')
        self.fields['outbreak'].queryset = outbreaks_for(user)

    def clean(self):
        data = super().clean()
        if data.get('status') == 'linked' and not data.get('outbreak'):
            self.add_error('outbreak', 'Выберите расследование / очаг.')
        return data


class AttachmentForm(forms.ModelForm):
    class Meta:
        model = Attachment
        fields = ['file']
        labels = {'file': 'Документ PDF / PNG / JPEG (до 10 МБ)'}

    def clean_file(self):
        from pathlib import Path
        f = self.cleaned_data['file']
        if f.size > 10 * 1024 * 1024:
            raise ValidationError('Максимум 10 МБ.')
        signatures = {'.pdf': b'%PDF-', '.png': b'\x89PNG\r\n\x1a\n', '.jpg': b'\xff\xd8\xff', '.jpeg': b'\xff\xd8\xff'}
        expected = signatures.get(Path(f.name).suffix.lower())
        header = f.read(8)
        f.seek(0)
        if not expected or not header.startswith(expected):
            raise ValidationError('Допустимы настоящие PDF, PNG и JPEG.')
        return f


class ReportFilterForm(forms.Form):
    start = forms.DateField(label='С', required=False, widget=DateInput())
    end = forms.DateField(label='По', required=False, widget=DateInput())
    date_field = forms.ChoiceField(label='Дата учёта', choices=[('detected_date', 'Выявление'), ('onset_date', 'Начало симптомов'), ('registered_at', 'Регистрация'), ('confirmed_date', 'Подтверждение')], required=False)
    disease = forms.ModelChoiceField(label='Заболевание', queryset=Disease.objects.filter(active=True), required=False, empty_label='Все заболевания')
    group = forms.ModelChoiceField(label='Группа', queryset=DiseaseGroup.objects.all(), required=False, empty_label='Все группы')
    territory = forms.ModelChoiceField(label='Территория', queryset=Territory.objects.all(), required=False, empty_label='Все территории')
    diagnosis = forms.ChoiceField(label='Диагноз', choices=[('', 'Все статусы')] + list(Case.Diagnosis.choices), required=False)
    sex = forms.ChoiceField(label='Пол', choices=[('', 'Все')] + list(Patient._meta.get_field('sex').choices), required=False)
    age_group = forms.ChoiceField(label='Возраст', choices=[('', 'Все'), ('children', '0–17'), ('unknown', 'Неизвестен')] + [(x, x) for x in ['<1', '1–4', '5–14', '15–17', '18–19', '20–29', '30–39', '40–49', '50–59', '60+']], required=False)
    group_by = forms.ChoiceField(label='Строки отчёта', choices=[('disease', 'Заболевания'), ('territory', 'Территории'), ('clinic', 'Поликлиники'), ('education_name', 'Школы и детсады'), ('residence', 'Адреса проживания')], required=False)
    include_cancelled = forms.BooleanField(label='Включить отменённые', required=False)
    include_excluded = forms.BooleanField(label='Включить исключённые диагнозы', required=False)
    include_duplicates = forms.BooleanField(label='Включить дубли', required=False)

    def clean(self):
        data = super().clean()
        if data.get('start') and data.get('end') and data['start'] > data['end']:
            raise ValidationError('Начало периода позже окончания.')
        return data
