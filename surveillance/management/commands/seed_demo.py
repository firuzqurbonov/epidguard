import secrets
from datetime import date, timedelta
from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from surveillance.models import *
from surveillance.catalog import GROUPS, DISEASES, COMMON_QUESTIONS, SPECIFIC_QUESTIONS
from surveillance.services import transition, capture_age, audit


class Command(BaseCommand):
    help = 'Install catalog and optional fictional demo. Never use demo patients in production.'

    def add_arguments(self, parser):
        parser.add_argument('--catalog-only', action='store_true')

    @transaction.atomic
    def handle(self, *args, **options):
        groups = {}
        for code, ru, tg, en in GROUPS:
            groups[code], _ = DiseaseGroup.objects.get_or_create(code=code, defaults={'name_ru': ru, 'name_tg': tg, 'name_en': en, 'is_total': code != 'priority'})
        for code, ru, tg, en, group, review in DISEASES:
            disease, created = Disease.objects.get_or_create(code=code, defaults={'name_ru': ru, 'name_tg': tg, 'name_en': en, 'source_label': tg, 'terminology_review': review, 'high_consequence': group == 'priority'})
            if created:
                disease.groups.add(groups['all'])
                if group:
                    disease.groups.add(groups[group])
            Questionnaire.objects.get_or_create(disease=disease, version=1, defaults={'title': 'Проект анкеты · требует утверждения', 'effective_from': date(2026, 1, 1), 'questions': COMMON_QUESTIONS + SPECIFIC_QUESTIONS.get(code, [])})
        self.stdout.write(f'Catalog: {Disease.objects.count()} diseases, {DiseaseGroup.objects.count()} groups.')
        if options['catalog_only']:
            return
        if not settings.DEBUG or not settings.DEMO_MODE:
            raise CommandError('Demo seed requires DEBUG=1 and DEMO_MODE=1. Use --catalog-only for a clean environment.')
        if User.objects.filter(username='epi.demo').exists():
            self.stdout.write('Demo already exists; no passwords or records changed.')
            return
        tajikistan, _ = Territory.objects.get_or_create(name='Таджикистан', defaults={'latitude': 38.86, 'longitude': 71.27})
        dushanbe, _ = Territory.objects.get_or_create(name='Душанбе', defaults={'parent': tajikistan, 'latitude': 38.56, 'longitude': 68.79})
        districts = []
        for name, lat, lon in [('Исмоили Сомони', 38.60, 68.79), ('Сино', 38.57, 68.74), ('Фирдавси', 38.52, 68.76), ('Шохмансур', 38.54, 68.82), ('Худжанд', 40.28, 69.62), ('Бохтар', 37.84, 68.78), ('Хорог', 37.49, 71.55)]:
            territory, _ = Territory.objects.get_or_create(name=name, defaults={'parent': dushanbe if len(districts) < 4 else tajikistan, 'latitude': lat, 'longitude': lon})
            districts.append(territory)
        republic = Service.objects.create(name='ДЕМО · Республиканская СЭС', level='republic', territory=tajikistan)
        city = Service.objects.create(name='ДЕМО · Городская СЭС Душанбе', level='city', territory=dushanbe, parent=republic)
        services = []
        for territory in districts:
            district = Service.objects.create(name=f'ДЕМО · СЭС {territory.name}', level='district', territory=territory, parent=city if territory.parent_id == dushanbe.pk else republic)
            services.append(district)
            Route.objects.create(territory=territory, service=district, is_lead=True)
            Route.objects.create(territory=territory, service=republic)
            if territory.parent_id == dushanbe.pk:
                Route.objects.create(territory=territory, service=city)
        hospital = Service.objects.create(name='ДЕМО · Поликлиника №1', level='hospital', territory=districts[0])
        credentials = []
        users = {}
        for username, first, last, role, superuser in [
            ('admin.demo', 'Администратор', 'Демо', 'administrator', True),
            ('epi.demo', 'Мадина', 'Демонстрационная', 'epidemiologist', False),
            ('doctor.demo', 'Фарид', 'Демонстрационный', 'clinician', False),
            ('lab.demo', 'Саида', 'Демонстрационная', 'laboratory', False),
            ('district.demo', 'Навруз', 'Демонстрационный', 'epidemiologist', False),
        ]:
            password = secrets.token_urlsafe(15)
            user = User.objects.create_user(username=username, password=password, first_name=first, last_name=last, is_staff=superuser, is_superuser=superuser)
            p = Profile.objects.create(user=user, approved=True, role=role, institution=hospital if role in ['clinician', 'laboratory'] else city)
            if username == 'epi.demo':
                p.services.add(city, republic, *services)
            elif username == 'district.demo':
                p.services.add(services[1])
            users[username] = user
            credentials.append(f'{username}    {password}')
        today = timezone.localdate()
        codes = ['measles', 'hepatitis-a', 'chickenpox', 'intestinal-unknown', 'acute-brucellosis', 'ari', 'malaria', 'pertussis', 'ascariasis', 'hepatitis-b', 'pneumonia', 'anthrax']
        created_cases = []
        for i in range(54):
            age = [0, 3, 7, 12, 16, 19, 24, 35, 42, 54, 67][i % 11]
            detected = today - timedelta(days=(i * 3) % 28)
            patient = Patient.objects.create(full_name=f'Демо Пациент {i + 1:03d}', birth_date=date(detected.year - age, 1, 1), sex='f' if i % 2 else 'm')
            disease = Disease.objects.get(code=codes[i % len(codes)])
            q = disease.questionnaires.first()
            case = Case.objects.create(patient=patient, disease=disease, creator=users['doctor.demo'], institution=hospital,
                territory=districts[i % len(districts)], residence=f'ДЕМО адрес, улица Примерная, дом {i + 1}',
                onset_date=detected - timedelta(days=2), detected_date=detected,
                diagnosis='confirmed' if i % 3 == 0 else 'suspect', confirmed_date=detected if i % 3 == 0 else None,
                patient_name=patient.full_name, patient_sex=patient.sex, birth_date_snapshot=patient.birth_date,
                education_type='school' if 6 <= age < 18 else 'kindergarten' if 1 <= age < 6 else 'none',
                education_name=f'ДЕМО · Школа {i % 4 + 1}' if 6 <= age < 18 else 'ДЕМО · Детсад 1' if 1 <= age < 6 else '',
                clinic=f'ДЕМО · Поликлиника №{i % 3 + 1}', hospitalized='yes' if i % 5 == 0 else 'no',
                questionnaire=q, questionnaire_snapshot={'title': q.title, 'version': q.version, 'approved': False, 'questions': q.questions},
                notes='Вымышленная запись для проверки интерфейса. Не относится к реальному человеку.')
            capture_age(case)
            case.save()
            if i < 51:
                case = transition(case.pk, users['doctor.demo'], 'submit', case.version)
            if i < 43:
                case = transition(case.pk, users['epi.demo'], 'accept', case.version)
                if i < 10:
                    case = transition(case.pk, users['epi.demo'], 'complete', case.version, 'Демонстрационное расследование завершено.')
                elif i == 11:
                    case = transition(case.pk, users['epi.demo'], 'cancel', case.version, 'Демонстрационная отмена с сохранением номера.')
            created_cases.append(case)
        for i, case in enumerate(created_cases[12:18]):
            Contact.objects.create(case=case, full_name=f'Демо Контакт {i + 1}', category='family', contact_date=today - timedelta(days=2), observation_until=today + timedelta(days=5))
            Measure.objects.create(case=case, kind='investigation', description='Демонстрационное уточнение эпиданамнеза', assignee='Демо Эпидемиолог', due_date=today + timedelta(days=i - 2), creator=users['epi.demo'])
            LabResult.objects.create(case=case, sample=f'ДЕМО-{i + 1}', collected_date=today, laboratory='ДЕМО · Лаборатория', method='Демонстрационный метод', result='pending', creator=users['lab.demo'])
        outbreak = Outbreak.objects.create(title='ДЕМО · Групповое расследование', disease=created_cases[12].disease, territory=created_cases[12].territory, service=created_cases[12].responsible_service, description='Вымышленный учебный очаг. Не является сообщением о реальной эпидемиологической ситуации.', creator=users['epi.demo'])
        outbreak.cases.add(created_cases[12], created_cases[24])
        Signal.objects.create(title='ДЕМО · Сообщение о группе симптомов', territory=districts[0], service=services[0], source='Учебное сообщение', description='Сигнал ещё не проверен; не включён в число случаев.', creator=users['epi.demo'])
        credentials_path = settings.BASE_DIR / '.demo-credentials.txt'
        credentials_path.write_text('EpiGuard — LOCAL DEMO ONLY\nGenerated passwords, no real patients.\n\n' + '\n'.join(credentials) + '\n', encoding='utf-8')
        self.stdout.write(self.style.SUCCESS(f'Demo ready. Credentials: {credentials_path}'))
