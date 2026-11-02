from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from surveillance.services import audit


class Command(BaseCommand):
    help = 'Operator-only MFA recovery after verifying user identity. Revokes all sessions for the user.'

    def add_arguments(self, parser):
        parser.add_argument('username')
        parser.add_argument('--reason', required=True)
        parser.add_argument('--operator', required=True)

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            actor = User.objects.get(username=options['operator'], is_superuser=True)
            user = User.objects.get(username=options['username'])
            p = user.profile
        except (User.DoesNotExist, AttributeError):
            raise CommandError('User/profile or superuser operator not found.')
        if len(options['reason'].strip()) < 10:
            raise CommandError('Provide a detailed recovery reason.')
        p.totp_secret, p.totp_enabled, p.totp_last_step = '', False, -1
        p.save(update_fields=['totp_secret', 'totp_enabled', 'totp_last_step'])
        for session in Session.objects.filter(expire_date__gt=timezone.now()):
            if session.get_decoded().get('_auth_user_id') == str(user.pk):
                session.delete()
        audit(actor, 'Восстановление MFA', detail=options['reason'], after={'user_id': user.pk})
        self.stdout.write(self.style.SUCCESS('MFA reset; sessions revoked. User must enroll again in production.'))
