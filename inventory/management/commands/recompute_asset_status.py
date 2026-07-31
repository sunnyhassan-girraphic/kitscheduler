import datetime

from django.core.management.base import BaseCommand

from ...status_sync import recompute_all


def _recompute_all_kit_statuses():
    """Nightly sweep: set Kit.status=BOOKED for kits with an active booking
    today, Kit.status=READY for those that don't — only touching kits in
    auto-managed states (BOOKED/READY). Catches date-boundary crossings that
    no signal event would otherwise trigger (e.g. a job ending overnight)."""
    from inventory.models import Kit, KitBooking

    today = datetime.date.today()
    booked_kit_ids = set(
        KitBooking.objects.filter(
            start_date__lte=today, end_date__gte=today
        ).values_list("kit_id", flat=True)
    )
    auto_states = (Kit.Status.BOOKED, Kit.Status.READY)
    Kit.objects.filter(status__in=auto_states, id__in=booked_kit_ids).exclude(
        status=Kit.Status.BOOKED
    ).update(status=Kit.Status.BOOKED)
    Kit.objects.filter(status__in=auto_states).exclude(id__in=booked_kit_ids).exclude(
        status=Kit.Status.READY
    ).update(status=Kit.Status.READY)


class Command(BaseCommand):
    help = (
        "Recomputes Asset.status (Available/In use/Booked tier) for every "
        "asset based on current kit membership, nesting, and job bookings, "
        "and Kit.status (Booked/Ready) for all auto-managed kits. "
        "Most changes are picked up immediately via signals when something "
        "is edited, but a booking's start/end date can arrive with no edit "
        "happening that day — run this nightly via cron (e.g. just after "
        "midnight) to catch that."
    )

    def handle(self, *args, **options):
        recompute_all()
        _recompute_all_kit_statuses()
        self.stdout.write(self.style.SUCCESS("Asset and kit status recompute complete."))
