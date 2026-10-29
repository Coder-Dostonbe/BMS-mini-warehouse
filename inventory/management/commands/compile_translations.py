from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from inventory.i18n_catalog import extract_project, read_po, write_mo


class Command(BaseCommand):
    help = "Compile locale/*/LC_MESSAGES/django.po to .mo without GNU gettext; report missing msgids."

    def handle(self, *args, **options):
        base = Path(settings.BASE_DIR)
        msgids = extract_project(base)
        po_files = sorted((base / "locale").glob("*/LC_MESSAGES/django.po"))
        if not po_files:
            raise CommandError("No locale/*/LC_MESSAGES/django.po found")
        for po in po_files:
            entries = read_po(po)
            translated = {k: v for k, v in entries.items() if k and v}
            if "" in entries:
                translated[""] = entries[""]
            write_mo(translated, po.with_suffix(".mo"))
            missing = sorted(m for m in msgids if m not in translated)
            self.stdout.write(f"{po.relative_to(base)}: {len(translated) - 1} strings")
            for msgid in missing:
                # ascii() keeps a cp1252 Windows console from crashing on Cyrillic.
                self.stdout.write(self.style.WARNING(f"  missing: {ascii(msgid)}"))
