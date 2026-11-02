import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = 'Consistent SQLite backup + private documents + manifest. Stop writes while copying documents.'

    def add_arguments(self, parser):
        parser.add_argument('--directory', default='backups')

    def handle(self, *args, **options):
        database = settings.DATABASES['default']
        if database['ENGINE'] != 'django.db.backends.sqlite3':
            raise CommandError('PostgreSQL: use pg_dump and a coordinated private-media snapshot; see docs/OPERATIONS.md.')
        root = Path(options['directory']).resolve()
        destination = root / timezone.now().strftime('%Y%m%dT%H%M%S%fZ')
        destination.mkdir(parents=True, exist_ok=False)
        with sqlite3.connect(str(database['NAME'])) as source, sqlite3.connect(destination / 'db.sqlite3') as target:
            source.backup(target)
            result = target.execute('PRAGMA integrity_check').fetchone()[0]
        if result != 'ok':
            raise CommandError('Backup integrity check failed.')
        if Path(settings.MEDIA_ROOT).exists():
            shutil.copytree(settings.MEDIA_ROOT, destination / 'private-media')
        manifest = {}
        for file in destination.rglob('*'):
            if file.is_file():
                with file.open('rb') as stream:
                    manifest[str(file.relative_to(destination))] = hashlib.file_digest(stream, 'sha256').hexdigest()
        (destination / 'manifest.json').write_text(json.dumps({'created_at': timezone.now().isoformat(), 'files': manifest}, indent=2), encoding='utf-8')
        self.stdout.write(self.style.SUCCESS(f'Backup verified: {destination}'))
        self.stdout.write('Protect this backup as medical data. Secret keys and .env must be backed up separately and securely.')
