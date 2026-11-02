from django.conf import settings
from .access import profile, is_epi


def workspace(request):
    p = profile(request.user) if request.user.is_authenticated else None
    return {'lang': p.language if p else request.session.get('language', 'ru'), 'current_profile': p,
            'is_epi': is_epi(request.user) if request.user.is_authenticated else False, 'demo_mode': settings.DEMO_MODE,
            'navigation': [('dashboard', 'Обзор', 'grid'), ('cases', 'Реестр случаев', 'folder'),
                ('inbox', 'Входящие извещения', 'inbox'), ('outbreaks', 'Очаги', 'target'),
                ('contacts', 'Контактные лица', 'users'), ('measures', 'Мероприятия', 'check'),
                ('laboratory', 'Лаборатория', 'lab'), ('reports', 'Отчёты', 'chart'),
                ('map', 'Карта', 'map'), ('signals', 'Сигналы', 'signal'),
                ('diseases', 'Справочник заболеваний', 'book'), ('audit', 'История действий', 'clock')]}
