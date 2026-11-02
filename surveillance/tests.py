import base64
import io
import tempfile
import time
import uuid
from datetime import date, timedelta
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook
from .models import *
from .services import transition, capture_age, snapshot, ConflictError
from .access import cases_for
from .reporting import aggregate_report, filtered_report, AGE_LABELS
from .forms import CaseForm, AttachmentForm, LabForm, OutbreakForm
from .views import totp


class WorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.area = Territory.objects.create(name='Test district', latitude=38.5, longitude=68.8)
        cls.other_area = Territory.objects.create(name='Other district')
        cls.district = Service.objects.create(name='District', level='district', territory=cls.area)
        cls.city = Service.objects.create(name='City', level='city', territory=cls.area)
        cls.republic = Service.objects.create(name='Republic', level='republic', territory=cls.area)
        cls.receiving = Service.objects.create(name='Receiving', level='district', territory=cls.other_area)
        for service in [cls.district, cls.city, cls.republic]:
            Route.objects.create(territory=cls.area, service=service, is_lead=service == cls.district)
        cls.doctor = User.objects.create_user('doctor', password='Test-password!2381')
        cls.other = User.objects.create_user('other', password='Test-password!2381')
        cls.epi = User.objects.create_user('epi', password='Test-password!2381')
        cls.receiver = User.objects.create_user('receiver', password='Test-password!2381')
        cls.admin_user = User.objects.create_user('useradmin', password='Test-password!2381', is_staff=True)
        for user, role in [(cls.doctor, 'clinician'), (cls.other, 'clinician'), (cls.epi, 'epidemiologist'), (cls.receiver, 'epidemiologist'), (cls.admin_user, 'administrator')]:
            p = Profile.objects.create(user=user, approved=True, role=role)
            if user == cls.epi:
                p.services.add(cls.district)
            elif user == cls.receiver:
                p.services.add(cls.receiving)
        cls.disease = Disease.objects.create(code='measles', name_ru='Корь', name_tg='Сурхча', name_en='Measles')
        cls.q = Questionnaire.objects.create(disease=cls.disease, version=1, title='Draft questionnaire', questions=[{'key': 'exposure', 'label_ru': 'Контакт', 'type': 'text'}])
        cls.patient = Patient.objects.create(full_name='Test Patient', birth_date=date(2010, 3, 15), sex='f')

    def setUp(self):
        cache.clear()
        self.client.force_login(self.doctor)

    def new_case(self, **kwargs):
        fields = dict(patient=self.patient, disease=self.disease, creator=self.doctor, patient_name=self.patient.full_name,
                      patient_sex='f', birth_date_snapshot=self.patient.birth_date, territory=self.area,
                      residence='Test address', onset_date=date(2026, 3, 15), detected_date=date(2026, 3, 16),
                      questionnaire=self.q, questionnaire_snapshot={'version': 1, 'questions': self.q.questions})
        fields.update(kwargs)
        case = Case.objects.create(**fields)
        capture_age(case)
        case.save()
        return case

    def active_case(self):
        case = self.new_case()
        case = transition(case.pk, self.doctor, 'submit', case.version)
        return transition(case.pk, self.epi, 'accept', case.version)

    def form_data(self, **kwargs):
        fields = {'full_name': 'Fictional New Patient', 'birth_date': '2000-01-01', 'sex': 'm', 'phone': '',
            'disease': self.disease.pk, 'diagnosis': 'suspect', 'detected_date': '2026-03-16', 'onset_date': '2026-03-15',
            'territory': self.area.pk, 'residence': 'Example', 'address_quality': 'reported', 'education_type': 'none',
            'hospitalized': 'unknown', 'severity': 'unknown', 'outcome': 'unknown', 'vaccination': 'unknown',
            'version': 1, 'submission_key': str(uuid.uuid4()), 'q_exposure': 'Example exposure'}
        fields.update(kwargs)
        return fields

    def test_one_case_three_notifications_and_number_on_accept(self):
        case = self.new_case()
        case = transition(case.pk, self.doctor, 'submit', 1)
        self.assertIsNone(case.number)
        self.assertEqual(case.notifications.count(), 3)
        self.assertEqual(Case.objects.count(), 1)
        case = transition(case.pk, self.epi, 'accept', 2)
        self.assertTrue(case.number.startswith('EP-'))
        self.assertEqual(case.responsible_service, self.district)
        self.assertIsNotNone(case.registered_at)

    def test_cancel_requires_reason_keeps_number_excluded_from_report(self):
        case = self.active_case()
        number = case.number
        with self.assertRaises(ValidationError):
            transition(case.pk, self.epi, 'cancel', case.version)
        case = transition(case.pk, self.epi, 'cancel', case.version, 'Incorrect initial report')
        self.assertEqual(case.number, number)
        self.assertEqual(Case.objects.count(), 1)
        self.assertEqual(filtered_report(self.epi, {})[1].count(), 0)
        self.assertEqual(filtered_report(self.epi, {'include_cancelled': 'on'})[1].count(), 1)
        another = self.active_case()
        self.assertNotEqual(another.number, number)
        self.assertTrue(case.audit_entries.filter(action='cancel').exists())

    def test_foreign_case_hidden_everywhere(self):
        own = self.active_case()
        foreign = self.new_case(creator=self.other, patient_name='PRIVATE Foreign Patient')
        foreign = transition(foreign.pk, self.other, 'submit', 1)
        foreign = transition(foreign.pk, self.epi, 'accept', 2)
        for path in ['/', '/cases/', '/reports/', '/map/', '/audit/', '/inbox/']:
            response = self.client.get(path)
            self.assertNotContains(response, 'PRIVATE Foreign Patient')
        for suffix in ['', 'edit/']:
            self.assertEqual(self.client.get(f'/cases/{foreign.pk}/{suffix}').status_code, 404)
        self.assertEqual(self.client.post(f'/cases/{foreign.pk}/action/', {'action': 'cancel', 'version': foreign.version, 'reason': 'hack'}).status_code, 404)
        export = load_workbook(io.BytesIO(self.client.get('/reports/?export=xlsx').content))
        self.assertNotIn('PRIVATE Foreign Patient', str(list(export.active.values)))

    def test_clinician_cannot_accept_or_transfer(self):
        case = self.new_case()
        case = transition(case.pk, self.doctor, 'submit', 1)
        with self.assertRaises(PermissionDenied):
            transition(case.pk, self.doctor, 'accept', case.version)

    def test_no_route_is_not_randomly_assigned(self):
        case = self.new_case(territory=self.other_area)
        case = transition(case.pk, self.doctor, 'submit', 1)
        self.assertIsNone(case.responsible_service_id)
        self.assertEqual(case.notifications.count(), 0)

    def test_transfer_holds_responsibility_number_then_accepts(self):
        case = self.active_case()
        number = case.number
        case = transition(case.pk, self.epi, 'transfer', case.version, 'Actual residence changed', target=self.receiving)
        self.assertEqual(case.responsible_service, self.district)
        self.assertEqual(case.number, number)
        self.assertIn(case, cases_for(self.receiver))
        with self.assertRaises(PermissionDenied):
            transition(case.pk, self.epi, 'transfer_accept', case.version, 'Wrong recipient')
        case = transition(case.pk, self.receiver, 'transfer_accept', case.version, 'Accepted responsibility')
        self.assertEqual(case.responsible_service, self.receiving)
        self.assertEqual(case.number, number)
        self.assertEqual(Case.objects.count(), 1)

    def test_transfer_return_retains_source(self):
        case = self.active_case()
        case = transition(case.pk, self.epi, 'transfer', case.version, 'Check address', target=self.receiving)
        case = transition(case.pk, self.receiver, 'transfer_return', case.version, 'Address not in jurisdiction')
        self.assertEqual(case.responsible_service, self.district)
        self.assertEqual(case.status, 'active')

    def test_conflicting_transition_rolls_back(self):
        case = self.active_case()
        version = case.version
        case = transition(case.pk, self.epi, 'complete', version, 'Done')
        with self.assertRaises(ConflictError):
            transition(case.pk, self.epi, 'reopen', version, 'Stale browser')
        case.refresh_from_db()
        self.assertEqual(case.status, 'completed')

    def test_duplicate_preserves_original_and_counts_once(self):
        main = self.active_case()
        duplicate = self.active_case()
        duplicate = transition(duplicate.pk, self.epi, 'duplicate', duplicate.version, 'Same episode, reviewed manually', duplicate=main)
        self.assertEqual(Case.objects.count(), 2)
        self.assertEqual(duplicate.duplicate_of, main)
        self.assertEqual(filtered_report(self.epi, {})[1].count(), 1)
        self.assertEqual(filtered_report(self.epi, {'include_duplicates': 'on'})[1].count(), 2)

    def test_age_boundaries_and_children_do_not_double(self):
        ages = [0, 1, 4, 5, 14, 15, 17, 18, 19, 20, 29, 30, 39, 40, 49, 50, 59, 60, None]
        cases = [Case(disease=self.disease, age_at_onset=age) for age in ages]
        row = aggregate_report(cases)[0]
        self.assertEqual(sum(row['ages']), len(ages))
        self.assertEqual(row['children'], 7)
        self.assertEqual(cases[6].age_group, '15–17')
        self.assertEqual(cases[7].age_group, '18–19')
        self.assertEqual(cases[-1].age_group, 'Неизвестен')

    def test_age_uses_historical_birth_and_illness_dates(self):
        case = self.new_case()
        self.assertEqual(case.age_at_onset, 16)
        self.patient.birth_date = date(2000, 1, 1)
        self.patient.full_name = 'Changed later'
        self.patient.save()
        case.refresh_from_db()
        self.assertEqual(case.age_at_onset, 16)
        self.assertEqual(case.patient_name, 'Test Patient')

    def test_create_and_retry_are_idempotent(self):
        data = self.form_data()
        response = self.client.post('/cases/new/', data)
        self.assertEqual(response.status_code, 302, response.content.decode())
        self.assertEqual(Case.objects.count(), 1)
        response2 = self.client.post('/cases/new/', data)
        self.assertEqual(response2.status_code, 302)
        self.assertEqual(Case.objects.count(), 1)
        case = Case.objects.first()
        self.assertEqual(case.answers['exposure'], 'Example exposure')
        self.assertEqual(case.questionnaire_snapshot['version'], 1)

    def test_form_validation_preserves_input(self):
        response = self.client.post('/cases/new/', self.form_data(onset_date='2026-04-20', full_name='Retained Input'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Retained Input')
        self.assertEqual(Case.objects.count(), 0)

    def test_initial_date_uses_html_iso_and_sex_unknown(self):
        response = self.client.get('/cases/new/')
        form = response.context['form']
        self.assertEqual(form['sex'].value(), 'u')
        self.assertIn(f'value="{timezone.localdate().isoformat()}"', str(form['detected_date']))

    def test_questionnaire_api_keeps_frozen_version(self):
        case = self.new_case()
        newer = Questionnaire.objects.create(disease=self.disease, title='New version', version=2, effective_from=date(2026, 1, 1), questions=[])
        response = self.client.get(f'/api/diseases/{self.disease.pk}/questionnaire/?case={case.pk}')
        self.assertEqual(response.json()['version'], 1)
        response = self.client.get(f'/api/diseases/{self.disease.pk}/questionnaire/')
        self.assertEqual(response.json()['version'], 2)

    def test_existing_patient_no_identity_overwrite(self):
        case = self.new_case()
        data = self.form_data(existing_patient=str(self.patient.pk), full_name='Attempted overwrite')
        response = self.client.post('/cases/new/', data)
        self.assertEqual(response.status_code, 302)
        self.patient.refresh_from_db()
        self.assertEqual(self.patient.full_name, 'Test Patient')
        self.assertEqual(Case.objects.count(), 2)

    def test_stale_edit_preserves_saved_version(self):
        case = self.new_case()
        data = self.form_data(version=case.version, comment='First correction', residence='New address')
        response = self.client.post(f'/cases/{case.pk}/edit/', data)
        self.assertEqual(response.status_code, 302, response.content.decode())
        data.update(comment='Stale correction', residence='Should not win')
        response = self.client.post(f'/cases/{case.pk}/edit/', data)
        self.assertEqual(response.status_code, 200)
        case.refresh_from_db()
        self.assertEqual(case.residence, 'New address')
        self.assertContains(response, 'Should not win')

    def test_frozen_questionnaire(self):
        case = self.new_case()
        self.q.questions = []
        with self.assertRaises(ValidationError):
            self.q.save()

    def test_conditional_question_ignores_hidden_answer(self):
        disease = Disease.objects.create(code='anthrax', name_ru='Сибирская язва', name_tg='Сӯхтанӣ', name_en='Anthrax')
        Questionnaire.objects.create(disease=disease, title='Conditional', questions=[
            {'key': 'animal', 'label_ru': 'Животные', 'type': 'select', 'options': ['Да', 'Нет']},
            {'key': 'farm', 'label_ru': 'Хозяйство', 'type': 'text', 'required': True, 'show_if': {'key': 'animal', 'value': 'Да'}},
        ])
        form = CaseForm(self.form_data(disease=disease.pk, q_animal='Нет', q_farm='Must ignore'), user=self.doctor)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertNotIn('farm', form.answers)

    def test_report_dates_status_and_territory(self):
        case = self.active_case()
        self.assertEqual(filtered_report(self.epi, {'start': '2026-03-01', 'end': '2026-03-31'})[1].count(), 1)
        self.assertEqual(filtered_report(self.epi, {'start': '2026-04-01'})[1].count(), 0)
        self.assertEqual(filtered_report(self.epi, {'territory': self.other_area.pk})[1].count(), 0)
        self.assertFalse(filtered_report(self.epi, {'start': '2026-04-01', 'end': '2026-03-01'})[0].is_valid())

    def test_school_cases_are_not_school_count(self):
        first = self.active_case()
        second = self.active_case()
        Case.objects.filter(pk__in=[first.pk, second.pk]).update(education_type='school', education_name='Example School')
        response = self.client.get('/reports/')
        self.assertEqual(response.context['school_cases'], 2)
        self.assertEqual(response.context['schools'], 1)

    def test_shared_contact_patient_is_unique(self):
        case = self.active_case()
        for category in ['family', 'school']:
            Contact.objects.create(case=case, patient=self.patient, full_name=self.patient.full_name, category=category)
        response = self.client.get('/contacts/')
        self.assertEqual(response.context['unique_contacts'], 1)

    def test_contact_observation_edit_is_scoped_and_audited(self):
        case = self.active_case()
        contact = Contact.objects.create(case=case, full_name='Test Contact', category='family')
        self.assertEqual(self.client.get(f'/contacts/{contact.pk}/edit/').status_code, 403)
        self.client.force_login(self.epi)
        data = {'full_name': 'Test Contact', 'category': 'family', 'status': 'completed', 'notes': 'No symptoms at end of observation', 'reason': 'Observation ended', 'version': case.version}
        response = self.client.post(f'/contacts/{contact.pk}/edit/', data)
        self.assertEqual(response.status_code, 302, response.content.decode())
        contact.refresh_from_db()
        self.assertEqual(contact.status, 'completed')
        self.assertTrue(Audit.objects.filter(case=case, action='Наблюдение контактного лица').exists())
        data['notes'] = 'Stale write'
        response = self.client.post(f'/contacts/{contact.pk}/edit/', data)
        self.assertEqual(response.status_code, 200)
        contact.refresh_from_db()
        self.assertNotEqual(contact.notes, 'Stale write')

    def test_same_contact_key_across_locations_counts_once(self):
        case = self.active_case()
        first = Contact.objects.create(case=case, full_name='Contact without medical ID', category='family')
        self.client.force_login(self.epi)
        response = self.client.post(f'/cases/{case.pk}/contacts/add/', {'full_name': first.full_name, 'category': 'school', 'status': 'monitoring', 'same_person': first.pk})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Contact.objects.count(), 2)
        self.assertEqual(self.client.get('/contacts/').context['unique_contacts'], 1)

    def test_signal_not_in_case_statistics(self):
        Signal.objects.create(title='Unverified', territory=self.area, service=self.district, source='Test', description='Not a case', creator=self.epi)
        self.assertEqual(self.client.get('/').context['total'], 0)

    def test_lab_correction_preserves_old_result(self):
        case = self.active_case()
        lab = LabResult.objects.create(case=case, sample='A', collected_date=date(2026, 3, 15), result_date=date(2026, 3, 16), laboratory='Lab', method='Method', result='negative', creator=self.doctor)
        form = LabForm({'sample': 'A', 'collected_date': '2026-03-15', 'result_date': '2026-03-17', 'laboratory': 'Lab', 'method': 'Method', 'result': 'positive', 'replaces': lab.pk, 'comment': 'Corrected document'}, case=case)
        self.assertTrue(form.is_valid(), form.errors)
        correction = form.save(commit=False)
        correction.case, correction.creator = case, self.doctor
        correction.save()
        lab.refresh_from_db()
        self.assertEqual(lab.result, 'negative')
        self.assertEqual(lab.corrections.count(), 1)
        case.refresh_from_db()
        self.assertEqual(case.diagnosis, 'suspect')

    def test_file_type_size_and_scoped_download(self):
        case = self.active_case()
        bad = AttachmentForm(files={'file': SimpleUploadedFile('bad.pdf', b'<script>bad</script>')})
        self.assertFalse(bad.is_valid())
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            self.client.force_login(self.epi)
            response = self.client.post(f'/cases/{case.pk}/documents/', {'file': SimpleUploadedFile('report.pdf', b'%PDF-1.4\ntest')})
            self.assertEqual(response.status_code, 302)
            attachment = Attachment.objects.get()
            download = self.client.get(f'/documents/{attachment.pk}/')
            self.assertEqual(download.status_code, 200)
            self.assertTrue(b''.join(download.streaming_content).startswith(b'%PDF'))
            download.close()
            self.client.force_login(self.other)
            self.assertEqual(self.client.get(f'/documents/{attachment.pk}/').status_code, 404)

    def test_user_administrator_does_not_gain_clinical_access(self):
        self.active_case()
        self.client.force_login(self.admin_user)
        self.assertEqual(cases_for(self.admin_user).count(), 0)
        self.assertEqual(self.client.get('/cases/new/').status_code, 403)
        self.assertEqual(self.client.get('/outbreaks/').status_code, 403)

    def test_audit_cannot_be_changed_or_deleted(self):
        case = self.active_case()
        entry = case.audit_entries.first()
        with self.assertRaises(ValidationError):
            entry.delete()
        with self.assertRaises(ValidationError):
            Audit.objects.filter(pk=entry.pk).update(detail='tampered')
        with self.assertRaises(ValidationError):
            Audit.objects.all().delete()

    def test_csrf_required(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.doctor)
        self.assertEqual(client.post('/cases/new/', self.form_data()).status_code, 403)

    def test_login_lockout_and_approval(self):
        self.client.logout()
        for i in range(5):
            self.client.post('/login/', {'username': 'doctor', 'password': 'wrong'})
        response = self.client.post('/login/', {'username': 'doctor', 'password': 'Test-password!2381'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Слишком много попыток')
        cache.clear()
        self.doctor.profile.approved = False
        self.doctor.profile.save()
        response = self.client.post('/login/', {'username': 'doctor', 'password': 'Test-password!2381'})
        self.assertContains(response, 'Доступ ещё не подтверждён')

    def test_totp_rfc_vector(self):
        secret = base64.b32encode(b'12345678901234567890').decode()
        self.assertEqual(totp(secret, 1), '287082')

    def test_mfa_verification_and_replay(self):
        p = self.doctor.profile
        p.totp_secret = base64.b32encode(b'12345678901234567890').decode()
        p.totp_enabled = True
        p.save()
        self.assertRedirects(self.client.get('/'), '/security/', fetch_redirect_response=False)
        code = totp(p.totp_secret, int(time.time() // 30))
        self.assertEqual(self.client.post('/security/', {'code': code}).status_code, 302)
        self.assertEqual(self.client.get('/').status_code, 200)
        self.client.logout()
        self.client.force_login(self.doctor)
        response = self.client.post('/security/', {'code': code})
        self.assertContains(response, 'уже использован')

    def test_language_changes_labels_not_report_totals(self):
        self.active_case()
        before = self.client.get('/reports/').context['totals']
        self.client.post('/language/', {'language': 'en', 'next': '/reports/'})
        response = self.client.get('/reports/')
        self.assertEqual(before, response.context['totals'])
        self.assertContains(response, 'Reports')
        self.assertContains(response, 'Measles')

    def test_spreadsheet_formula_injection_is_text(self):
        case = self.active_case()
        Case.objects.filter(pk=case.pk).update(clinic='=HYPERLINK("https://bad.example")')
        response = self.client.get('/reports/?group_by=clinic&export=xlsx')
        workbook = load_workbook(io.BytesIO(response.content))
        cells = [c for row in workbook.active for c in row if isinstance(c.value, str) and c.value.startswith('=HYPERLINK')]
        self.assertEqual(len(cells), 1)
        self.assertEqual(cells[0].data_type, 's')

    def test_all_pages_render_in_scoped_sessions(self):
        case = self.active_case()
        self.client.force_login(self.epi)
        for path in ['/', '/cases/', '/cases/new/', f'/cases/{case.pk}/', f'/cases/{case.pk}/edit/', '/inbox/', '/contacts/', '/measures/', '/laboratory/', '/outbreaks/', '/outbreaks/new/', '/signals/', '/signals/new/', '/reports/', '/map/', '/diseases/', '/audit/', '/settings/', '/security/', f'/cases/{case.pk}/contacts/add/', f'/cases/{case.pk}/measures/add/', f'/cases/{case.pk}/laboratory/add/']:
            self.assertEqual(self.client.get(path).status_code, 200, path)
