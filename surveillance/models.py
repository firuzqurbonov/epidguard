import uuid
from datetime import date
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils import timezone


class Territory(models.Model):
    name = models.CharField('Название', max_length=160)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT)
    # Coordinates are reference centres, never patient addresses or official boundaries.
    latitude = models.FloatField(null=True, blank=True, validators=[MinValueValidator(-90), MaxValueValidator(90)])
    longitude = models.FloatField(null=True, blank=True, validators=[MinValueValidator(-180), MaxValueValidator(180)])

    class Meta:
        ordering = ['name']
        verbose_name = 'Территория'
        verbose_name_plural = 'Территории'

    def __str__(self):
        return self.name


class Service(models.Model):
    name = models.CharField('СЭС / учреждение', max_length=200)
    level = models.CharField('Уровень', max_length=20, choices=[('district', 'Район'), ('city', 'Город'), ('republic', 'Республика'), ('hospital', 'Медучреждение')])
    territory = models.ForeignKey(Territory, on_delete=models.PROTECT)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT)

    def __str__(self):
        return self.name


class Route(models.Model):
    territory = models.ForeignKey(Territory, on_delete=models.PROTECT)
    service = models.ForeignKey(Service, on_delete=models.PROTECT)
    is_lead = models.BooleanField('Ведущая СЭС', default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['territory', 'service'], name='unique_route'),
                       models.UniqueConstraint(fields=['territory'], condition=models.Q(is_lead=True), name='one_lead_route')]

    def __str__(self):
        return f'{self.territory} → {self.service}'


class Profile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    role = models.CharField('Роль', max_length=20, choices=[('clinician', 'Медработник'), ('epidemiologist', 'Эпидемиолог'), ('laboratory', 'Лаборатория'), ('administrator', 'Администратор пользователей')], default='clinician')
    approved = models.BooleanField('Допуск подтверждён', default=False)
    institution = models.ForeignKey(Service, null=True, blank=True, on_delete=models.PROTECT)
    services = models.ManyToManyField(Service, blank=True, related_name='assigned_profiles')
    language = models.CharField(max_length=2, default='ru', choices=[('ru', 'RU'), ('tg', 'TJ'), ('en', 'EN')])
    totp_secret = models.CharField(max_length=64, blank=True)
    totp_enabled = models.BooleanField(default=False)
    totp_last_step = models.BigIntegerField(default=-1)

    def __str__(self):
        return f'{self.user} · {self.get_role_display()}'


class DiseaseGroup(models.Model):
    code = models.SlugField(unique=True)
    name_ru = models.CharField(max_length=200)
    name_tg = models.CharField(max_length=200)
    name_en = models.CharField(max_length=200)
    is_total = models.BooleanField(default=False)

    def __str__(self):
        return self.name_ru


class Disease(models.Model):
    code = models.SlugField(unique=True)
    name_ru = models.CharField('Русский', max_length=200)
    name_tg = models.CharField('Таджикский', max_length=200)
    name_en = models.CharField('English', max_length=200)
    source_label = models.CharField('Исходное написание', max_length=220, blank=True)
    groups = models.ManyToManyField(DiseaseGroup, blank=True)
    high_consequence = models.BooleanField('Особое внимание (проектная пометка)', default=False)
    terminology_review = models.BooleanField('Требует проверки терминологии', default=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name_ru']

    def __str__(self):
        return self.name_ru


class Questionnaire(models.Model):
    disease = models.ForeignKey(Disease, on_delete=models.PROTECT, related_name='questionnaires')
    version = models.PositiveIntegerField(default=1)
    title = models.CharField(max_length=200)
    effective_from = models.DateField(default=timezone.localdate)
    approved = models.BooleanField(default=False)
    questions = models.JSONField(default=list, help_text='List: key, label_ru/tg/en, type, required, options, show_if {key, value}.')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['disease', 'version'], name='unique_questionnaire_version')]

    def clean(self):
        import re
        if not isinstance(self.questions, list):
            raise ValidationError({'questions': 'Ожидается список вопросов.'})
        keys = set()
        for q in self.questions:
            if not isinstance(q, dict) or not re.fullmatch(r'[a-z][a-z0-9_]{0,49}', str(q.get('key', ''))):
                raise ValidationError({'questions': 'У каждого вопроса нужен уникальный key: латиница, цифры, подчёркивание.'})
            if q['key'] in keys or not all(q.get('label_' + lang) for lang in ['ru', 'tg', 'en']):
                raise ValidationError({'questions': 'Нужны уникальные ключи и названия на RU / TJ / EN.'})
            if q.get('type', 'text') not in ['text', 'date', 'select']:
                raise ValidationError({'questions': 'Тип: text, date или select.'})
            if q.get('type') == 'select' and (not isinstance(q.get('options'), list) or not q['options'] or not all(isinstance(x, str) for x in q['options'])):
                raise ValidationError({'questions': 'Для select нужен непустой список строк options.'})
            if q.get('show_if') and (not isinstance(q['show_if'], dict) or q['show_if'].get('key') not in keys or 'value' not in q['show_if']):
                raise ValidationError({'questions': 'show_if должен ссылаться на предыдущий вопрос и содержать value.'})
            keys.add(q['key'])
        if self.pk and Case.objects.filter(questionnaire=self).exists():
            old = type(self).objects.get(pk=self.pk)
            if any(getattr(old, field) != getattr(self, field) for field in ['questions', 'version', 'disease_id', 'title', 'effective_from', 'approved']):
                raise ValidationError('Анкета уже использована. Создайте новую версию.')

    def save(self, *args, **kwargs):
        if self.pk and Case.objects.filter(questionnaire=self).exists():
            old = type(self).objects.get(pk=self.pk)
            if any(getattr(old, field) != getattr(self, field) for field in ['questions', 'version', 'disease_id', 'title', 'effective_from', 'approved']):
                raise ValidationError('Анкета уже использована. Создайте новую версию.')
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.disease} · v{self.version}'


def medical_id():
    return f'MED-{uuid.uuid4().hex[:16].upper()}'


class Patient(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    medical_id = models.CharField('Медицинский ID', max_length=80, unique=True, default=medical_id)
    full_name = models.CharField('ФИО', max_length=200)
    birth_date = models.DateField('Дата рождения', null=True, blank=True)
    approximate_age = models.PositiveSmallIntegerField('Приблизительный возраст', null=True, blank=True, validators=[MaxValueValidator(120)])
    sex = models.CharField('Пол', max_length=1, choices=[('f', 'Женский'), ('m', 'Мужской'), ('u', 'Неизвестно')], default='u')
    phone = models.CharField('Телефон', max_length=60, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.full_name} · {self.medical_id}'


class Case(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Черновик'
        SUBMITTED = 'submitted', 'Ожидает принятия'
        ACTIVE = 'active', 'В работе'
        TRANSFER = 'transfer', 'Передача ожидает принятия'
        COMPLETED = 'completed', 'Завершён'
        CANCELLED = 'cancelled', 'Отменён'

    class Diagnosis(models.TextChoices):
        SUSPECT = 'suspect', 'Подозрение'
        PROBABLE = 'probable', 'Вероятный'
        CONFIRMED = 'confirmed', 'Подтверждённый'
        EXCLUDED = 'excluded', 'Исключённый'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    number = models.CharField(max_length=40, unique=True, null=True, blank=True, editable=False)
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='cases')
    disease = models.ForeignKey(Disease, on_delete=models.PROTECT)
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='created_cases')
    institution = models.ForeignKey(Service, on_delete=models.PROTECT, null=True, blank=True, related_name='reported_cases')
    responsible_service = models.ForeignKey(Service, on_delete=models.PROTECT, null=True, blank=True, related_name='responsible_cases')
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='assigned_cases')
    status = models.CharField(max_length=20, choices=Status, default=Status.DRAFT, db_index=True)
    diagnosis = models.CharField('Диагностический статус', max_length=20, choices=Diagnosis, default=Diagnosis.SUSPECT)
    territory = models.ForeignKey(Territory, verbose_name='Территория проживания', on_delete=models.PROTECT, null=True, blank=True)
    residence = models.CharField('Фактический адрес', max_length=300, blank=True)
    registered_address = models.CharField('Адрес регистрации', max_length=300, blank=True)
    stay_address = models.CharField('Адрес пребывания', max_length=300, blank=True)
    infection_address = models.CharField('Предполагаемое место заражения', max_length=300, blank=True)
    address_quality = models.CharField('Качество адреса', max_length=20, choices=[('verified', 'Проверен'), ('reported', 'Со слов'), ('unknown', 'Неизвестно')], default='reported')
    onset_date = models.DateField('Начало симптомов', null=True, blank=True)
    detected_date = models.DateField('Дата выявления', default=timezone.localdate)
    registered_at = models.DateTimeField(null=True, blank=True)
    confirmed_date = models.DateField('Дата подтверждения', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # Historical snapshots: reports never silently use today's patient/address data.
    patient_name = models.CharField(max_length=200)
    patient_sex = models.CharField(max_length=1, default='u')
    birth_date_snapshot = models.DateField(null=True, blank=True)
    age_at_onset = models.PositiveSmallIntegerField(null=True, blank=True)
    age_approximate = models.BooleanField(default=False)
    clinic = models.CharField('Обслуживающая поликлиника', max_length=200, blank=True)
    family_doctor = models.CharField('Семейный врач / участок', max_length=200, blank=True)
    education_type = models.CharField('Учёба', max_length=20, choices=[('none', 'Не посещает'), ('school', 'Школа'), ('kindergarten', 'Детсад'), ('unknown', 'Неизвестно')], default='unknown')
    education_name = models.CharField('Школа / детсад, класс / группа', max_length=200, blank=True)
    workplace = models.CharField('Место работы / профессия', max_length=200, blank=True)
    hospitalized = models.CharField('Госпитализация', max_length=10, choices=[('yes', 'Да'), ('no', 'Нет'), ('unknown', 'Неизвестно')], default='unknown')
    severity = models.CharField('Тяжесть', max_length=20, choices=[('unknown', 'Неизвестно'), ('mild', 'Лёгкая'), ('moderate', 'Средняя'), ('severe', 'Тяжёлая')], default='unknown')
    outcome = models.CharField('Исход', max_length=20, choices=[('unknown', 'Неизвестно'), ('recovering', 'На лечении'), ('recovered', 'Выздоровление'), ('death', 'Смерть')], default='unknown')
    vaccination = models.CharField('Прививочный статус на дату заболевания', max_length=20, choices=[('unknown', 'Сведений нет'), ('full', 'Документирован полный курс'), ('partial', 'Неполный курс'), ('none', 'Подтверждённо не привит'), ('na', 'Не применимо')], default='unknown')
    vaccination_source = models.CharField('Источник и дата сведений о прививках', max_length=240, blank=True)
    vaccination_reason = models.CharField('Причина отсутствия / задержки', max_length=240, blank=True)
    notes = models.TextField('Общий эпиданамнез', blank=True)
    questionnaire = models.ForeignKey(Questionnaire, null=True, blank=True, on_delete=models.PROTECT)
    questionnaire_snapshot = models.JSONField(default=dict, blank=True)
    answers = models.JSONField(default=dict, blank=True)
    cancellation_reason = models.TextField(blank=True)
    duplicate_of = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='duplicates')
    version = models.PositiveIntegerField(default=1)
    submission_key = models.UUIDField(unique=True, default=uuid.uuid4)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['disease', 'detected_date']), models.Index(fields=['territory', 'status'])]

    def __str__(self):
        return self.number or f'ID {str(self.pk)[:8]}'

    @property
    def age_group(self):
        age = self.age_at_onset
        if age is None:
            return 'Неизвестен'
        for limit, label in [(1, '<1'), (5, '1–4'), (15, '5–14'), (18, '15–17'), (20, '18–19'), (30, '20–29'), (40, '30–39'), (50, '40–49'), (60, '50–59')]:
            if age < limit:
                return label
        return '60+'


class EpidNumber(models.Model):
    case = models.OneToOneField(Case, on_delete=models.PROTECT)
    issued_at = models.DateTimeField(auto_now_add=True)


class Notification(models.Model):
    case = models.ForeignKey(Case, related_name='notifications', on_delete=models.PROTECT)
    service = models.ForeignKey(Service, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    viewed_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['case', 'service'], name='unique_recipient')]


class Transfer(models.Model):
    case = models.ForeignKey(Case, on_delete=models.PROTECT, related_name='transfers')
    source = models.ForeignKey(Service, on_delete=models.PROTECT, related_name='outgoing_transfers')
    target = models.ForeignKey(Service, on_delete=models.PROTECT, related_name='incoming_transfers')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=[('pending', 'Ожидает'), ('accepted', 'Принята'), ('returned', 'Возвращена')], default='pending')
    response = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['case'], condition=models.Q(status='pending'), name='one_pending_transfer')]


class Outbreak(models.Model):
    title = models.CharField('Название очага', max_length=200)
    disease = models.ForeignKey(Disease, on_delete=models.PROTECT)
    territory = models.ForeignKey(Territory, on_delete=models.PROTECT)
    service = models.ForeignKey(Service, on_delete=models.PROTECT)
    cases = models.ManyToManyField(Case, blank=True, related_name='outbreaks')
    opened_date = models.DateField('Дата установления', default=timezone.localdate)
    description = models.TextField('Основание и обстоятельства')
    source = models.TextField('Предполагаемый источник и факторы передачи', blank=True)
    status = models.CharField('Статус', max_length=20, choices=[('active', 'В работе'), ('closed', 'Завершён')], default='active')
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    closed_reason = models.TextField(blank=True)

    def __str__(self):
        return self.title


class Contact(models.Model):
    case = models.ForeignKey(Case, on_delete=models.PROTECT, related_name='contacts')
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, null=True, blank=True)
    full_name = models.CharField('ФИО контактного', max_length=200)
    identity_key = models.UUIDField(default=uuid.uuid4, help_text='Shared key or patient ID identifies the same person across contacts.')
    category = models.CharField('Место контакта', max_length=20, choices=[('family', 'Семья'), ('school', 'Школа'), ('kindergarten', 'Детсад'), ('work', 'Работа'), ('other', 'Другое')])
    contact_date = models.DateField('Дата контакта', null=True, blank=True)
    observation_until = models.DateField('Наблюдение до', null=True, blank=True)
    notes = models.TextField('Наблюдение, обследования, результат', blank=True)
    status = models.CharField('Статус', max_length=20, choices=[('monitoring', 'Наблюдение'), ('completed', 'Завершено'), ('ill', 'Заболел')], default='monitoring')
    linked_case = models.ForeignKey(Case, null=True, blank=True, on_delete=models.PROTECT, related_name='prior_contacts')

    def __str__(self):
        return self.full_name


class Measure(models.Model):
    case = models.ForeignKey(Case, on_delete=models.PROTECT, related_name='measures')
    kind = models.CharField('Вид', max_length=20, choices=[('investigation', 'Расследование'), ('laboratory', 'Обследование'), ('disinfection', 'Дезинфекция'), ('observation', 'Наблюдение'), ('quarantine', 'Ограничительные меры'), ('vaccination', 'Задача службе вакцинации'), ('other', 'Другое')])
    description = models.TextField('Содержание, объект и основание')
    authority_document = models.CharField('Орган / документ решения (для ограничений)', max_length=240, blank=True)
    assignee = models.CharField('Ответственный', max_length=200)
    due_date = models.DateField('Срок исполнения')
    completed_at = models.DateTimeField(null=True, blank=True)
    result = models.TextField(blank=True)
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


class LabResult(models.Model):
    case = models.ForeignKey(Case, on_delete=models.PROTECT, related_name='labs')
    sample = models.CharField('Материал / номер пробы', max_length=160)
    collected_date = models.DateField('Дата взятия')
    result_date = models.DateField('Дата результата', null=True, blank=True)
    laboratory = models.CharField('Лаборатория', max_length=200)
    method = models.CharField('Метод', max_length=160)
    pathogen = models.CharField('Возбудитель', max_length=160, blank=True)
    result = models.CharField('Результат', max_length=20, choices=[('pending', 'Ожидается'), ('positive', 'Положительный'), ('negative', 'Отрицательный'), ('doubtful', 'Сомнительный')])
    comment = models.TextField('Комментарий / основание исправления', blank=True)
    replaces = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='corrections')
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


class Signal(models.Model):
    title = models.CharField('Название', max_length=200)
    territory = models.ForeignKey(Territory, on_delete=models.PROTECT)
    service = models.ForeignKey(Service, on_delete=models.PROTECT)
    source = models.CharField('Источник сообщения', max_length=200)
    description = models.TextField('Описание и результат проверки')
    status = models.CharField('Статус', max_length=20, choices=[('new', 'Новый'), ('review', 'На проверке'), ('linked', 'Связан с расследованием'), ('dismissed', 'Отклонён')], default='new')
    outbreak = models.ForeignKey(Outbreak, null=True, blank=True, on_delete=models.PROTECT)
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


def private_upload(instance, filename):
    from pathlib import Path
    return f'cases/{instance.case_id}/{uuid.uuid4().hex}{Path(filename).suffix.lower()}'


class Attachment(models.Model):
    case = models.ForeignKey(Case, on_delete=models.PROTECT, related_name='attachments')
    file = models.FileField(upload_to=private_upload)
    original_name = models.CharField(max_length=240)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


class AuditQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('Журнал нельзя изменять.')

    def delete(self):
        raise ValidationError('Журнал нельзя удалять.')


class Audit(models.Model):
    case = models.ForeignKey(Case, on_delete=models.PROTECT, null=True, blank=True, related_name='audit_entries')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True)
    actor_role = models.CharField(max_length=80, blank=True)
    actor_institution = models.CharField(max_length=200, blank=True)
    action = models.CharField(max_length=100)
    detail = models.TextField(blank=True)
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    objects = AuditQuerySet.as_manager()

    class Meta:
        ordering = ['-created_at', '-pk']

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Журнал нельзя изменять.')
        if self.actor_id:
            p = getattr(self.actor, 'profile', None)
            self.actor_role = 'Суперпользователь' if self.actor.is_superuser else p.get_role_display() if p else ''
            self.actor_institution = str(p.institution or '') if p else ''
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Журнал нельзя удалять.')


class ReportTemplate(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=160)
    filters = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
