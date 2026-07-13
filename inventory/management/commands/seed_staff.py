from django.core.management.base import BaseCommand

from inventory.models import StaffMember

STAFF_SEED = ["Dom", "Fabio", "Sunny", "Charlie", "Josh"]


class Command(BaseCommand):
    help = "Creates the team's StaffMember records. Safe to re-run - existing names are left untouched."

    def handle(self, *args, **options):
        created = 0
        skipped = 0
        for name in STAFF_SEED:
            obj, was_created = StaffMember.objects.get_or_create(name=name)
            if was_created:
                created += 1
                self.stdout.write(f"  Created {name}")
            else:
                skipped += 1
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"{created} created, {skipped} already existed"))
