"""Wires up automatic Asset.status recompute (see status_sync.py) to fire
on every edit that could change it, from ANY entry point - this app's own
views, Django Admin, the shell, and background scripts alike. Connected in
InventoryConfig.ready().
"""
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Asset, AssetBooking, KitAssetTag, KitBooking
from .status_sync import recompute_for_asset_ids


def _recompute_kit_status(kit_id):
    """Auto-set Kit.status to BOOKED when it has an active booking today,
    clear back to READY when it doesn't — but only when the kit is currently
    in an auto-managed state (BOOKED or READY).  Manual statuses (PREP,
    FAULTY, ARCHIVED) are sticky and never overwritten here."""
    import datetime
    from .models import Kit, KitBooking as _KB

    try:
        kit = Kit.objects.get(pk=kit_id)
    except Kit.DoesNotExist:
        return

    # Only touch auto-managed states
    if kit.status not in (Kit.Status.BOOKED, Kit.Status.READY):
        return

    today = datetime.date.today()
    is_booked_today = _KB.objects.filter(
        kit=kit, start_date__lte=today, end_date__gte=today
    ).exists()
    new_status = Kit.Status.BOOKED if is_booked_today else Kit.Status.READY
    if kit.status != new_status:
        Kit.objects.filter(pk=kit_id).update(status=new_status)


@receiver(post_save, sender=KitAssetTag)
def _kit_asset_tag_saved(sender, instance, **kwargs):
    recompute_for_asset_ids([instance.asset_id])


@receiver(post_delete, sender=KitAssetTag)
def _kit_asset_tag_deleted(sender, instance, **kwargs):
    recompute_for_asset_ids([instance.asset_id])


@receiver(post_save, sender=KitBooking)
def _kit_booking_saved(sender, instance, **kwargs):
    member_ids = list(KitAssetTag.objects.filter(kit_id=instance.kit_id).values_list("asset_id", flat=True))
    recompute_for_asset_ids(member_ids)
    _recompute_kit_status(instance.kit_id)


@receiver(post_delete, sender=KitBooking)
def _kit_booking_deleted(sender, instance, **kwargs):
    member_ids = list(KitAssetTag.objects.filter(kit_id=instance.kit_id).values_list("asset_id", flat=True))
    recompute_for_asset_ids(member_ids)
    _recompute_kit_status(instance.kit_id)


@receiver(post_save, sender=AssetBooking)
def _asset_booking_saved(sender, instance, **kwargs):
    recompute_for_asset_ids([instance.asset_id])


@receiver(post_delete, sender=AssetBooking)
def _asset_booking_deleted(sender, instance, **kwargs):
    recompute_for_asset_ids([instance.asset_id])


@receiver(post_save, sender=Asset)
def _asset_saved(sender, instance, **kwargs):
    """Covers nesting changes (parent_engine set/cleared) and simply keeps
    a newly-created or edited asset's own status honest. Safe against
    recursion: recompute writes via a plain QuerySet.update(), which never
    re-fires this signal."""
    recompute_for_asset_ids([instance.id])
