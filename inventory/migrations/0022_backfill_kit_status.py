"""Backfill Kit.status to BOOKED for any kit that already has an active
booking today. Kits without an active booking stay at READY (the default
set by migration 0021). This is a one-time catch-up; the signal in
inventory/signals.py handles all future KitBooking saves/deletes."""
import datetime

from django.db import migrations


def backfill_kit_status(apps, schema_editor):
    Kit = apps.get_model("inventory", "Kit")
    KitBooking = apps.get_model("inventory", "KitBooking")

    today = datetime.date.today()
    booked_kit_ids = set(
        KitBooking.objects.filter(
            start_date__lte=today,
            end_date__gte=today,
        ).values_list("kit_id", flat=True)
    )
    if booked_kit_ids:
        Kit.objects.filter(pk__in=booked_kit_ids, status="READY").update(status="BOOKED")


def reverse_backfill(apps, schema_editor):
    Kit = apps.get_model("inventory", "Kit")
    Kit.objects.filter(status="BOOKED").update(status="READY")


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0021_kit_status"),
    ]

    operations = [
        migrations.RunPython(backfill_kit_status, reverse_code=reverse_backfill),
    ]
