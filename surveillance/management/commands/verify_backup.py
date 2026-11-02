import hashlib
import json
import sqlite3
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Read-only validation of backup manifest and SQLite integrity. Never overwrites the live database.'

    def add_arguments(self, parser):
        parser.add_argument('directory')

    def handle(self, *args, **options):
        root = Path(options['directory']).resolve()
        try:
            manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
            for name, digest in manifest['files'].items():
                file = (root / name).resolve()
                if not file.is_relative_to(root) or not file.is_file():
                    raise CommandError('Missing or invalid file in manifest.')
                with file.open('rb') as stream:
                    if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                        raise CommandError(f'Checksum mismatch: {name}')
            with sqlite3.connect((root / 'db.sqlite3').as_uri() + '?mode=ro', uri=True) as connection:
                if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise CommandError('SQLite integrity check failed.')
                count = connection.execute('SELECT COUNT(*) FROM surveillance_case').fetchone()[0]
            self.stdout.write(self.style.SUCCESS(f'Backup OK: {len(manifest["files"])} files; {count} case records. Live database unchanged.'))
        except (OSError, ValueError, KeyError, sqlite3.DatabaseError) as exc:
            raise CommandError('Cannot validate backup: ' + str(exc)) from exc
