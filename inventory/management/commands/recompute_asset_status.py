from django.core.management.base import BaseCommand

from ...status_sync import recompute_all


class Command(BaseCommand):
    help = (
        "Recomputes Asset.status (Available/In use/Booked tier) for every "
        "asset based on current kit membership, nesting, and job bookings. "
        "Most changes are already picked up immediately via signals when "
        "something is edited, but a booking's start/end date can arrive "
        "with no edit happening that day - run this nightly via cron "
        "(e.g. just after midnight) to catch that."
    )

    def handle(self, *args, **options):
        recompute_all()
        self.stdout.write(self.style.SUCCESS("Asset status recompute complete."))
