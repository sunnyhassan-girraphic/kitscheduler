"""Keeps Asset.status truthful for every viewer - Django Admin included -
by writing the computed value directly to the field, instead of layering a
separate computed 'effective status' on top that only some pages knew
about (which was confusing: the raw status said Available while some pages
showed a different 'In use' tag next to it).

Two facts drive the AUTO-managed tier of status (Available / In use /
Booked):
1. STRUCTURAL: is this asset nested inside a container (a Component or I/O
   Device inside an Engine, or a Component inside an I/O Device that's
   itself inside an Engine), or a direct member of a Kit? If so, at least
   'In use' - this has nothing to do with bookings; a kit sitting on the
   shelf with nothing booked still makes its members 'In use', because
   they're physically claimed by that kit/container, not free-standing.
2. BOOKING: is the Kit (or the asset directly) currently committed to a
   Job for today's date? If so, 'Booked' - this takes priority over the
   structural state, and is genuinely date-dependent (free this week,
   booked next week).

The other three status values (Needs repair, Maintenance, Missing) are
MANUAL and sticky - recompute never overwrites an asset sitting in one of
those. Someone has to deliberately set it back to Available for automatic
management to resume; if a manually-chosen Available/In use/Booked value
doesn't match reality, the very next recompute corrects it - these three
are not meant to be hand-picked long-term, only the manual/condition ones
are.

Recompute is triggered two ways:
- Immediately, via signals (see inventory/signals.py), whenever a
  KitAssetTag, KitBooking, or AssetBooking is created/changed/deleted, or
  an Asset's parent_engine changes - covers Django Admin edits too, not
  just this app's own forms.
- Nightly, via `python manage.py recompute_asset_status` (add to cron), to
  catch pure date-boundary crossings - a job's booking window starting or
  ending with no other edit happening that day.
"""
import datetime


def _expand_through_nesting(ids):
    from .models import Asset
    ids = set(ids)
    while True:
        newly_nested = set(
            Asset.objects.filter(parent_engine_id__in=ids).values_list("id", flat=True)
        )
        if newly_nested <= ids:
            break
        ids |= newly_nested
    return ids


def _job_committed_ids(on_date=None):
    """All asset ids (expanded through nesting) currently committed to a
    JOB on the given date (defaults to today)."""
    from .models import AssetBooking, KitAssetTag, KitBooking

    on_date = on_date or datetime.date.today()
    committed_kit_ids = set(
        KitBooking.objects.filter(
            start_date__lte=on_date, end_date__gte=on_date
        ).values_list("kit_id", flat=True)
    )
    committed = set(
        KitAssetTag.objects.filter(kit_id__in=committed_kit_ids).values_list("asset_id", flat=True)
    )
    committed |= set(
        AssetBooking.objects.filter(
            start_date__lte=on_date, end_date__gte=on_date
        ).values_list("asset_id", flat=True)
    )
    return _expand_through_nesting(committed)


def _structural_ids():
    """Every asset id physically claimed by something bigger right now:
    nested inside a container, or a direct member of ANY kit."""
    from .models import Asset, KitAssetTag

    direct_kit_member_ids = set(KitAssetTag.objects.values_list("asset_id", flat=True))
    nested_ids = _expand_through_nesting(
        set(Asset.objects.filter(parent_engine__isnull=False).values_list("id", flat=True))
    )
    return direct_kit_member_ids | nested_ids


def _apply(candidate_ids, job_ids, structural_ids):
    from .models import Asset

    to_booked, to_in_use, to_available = [], [], []
    for aid in candidate_ids:
        if aid in job_ids:
            to_booked.append(aid)
        elif aid in structural_ids:
            to_in_use.append(aid)
        else:
            to_available.append(aid)

    if to_booked:
        Asset.objects.filter(id__in=to_booked).exclude(status=Asset.Status.BOOKED).update(status=Asset.Status.BOOKED)
    if to_in_use:
        Asset.objects.filter(id__in=to_in_use).exclude(status=Asset.Status.IN_USE).update(status=Asset.Status.IN_USE)
    if to_available:
        Asset.objects.filter(id__in=to_available).exclude(status=Asset.Status.AVAILABLE).update(status=Asset.Status.AVAILABLE)


def recompute_for_asset_ids(asset_ids, on_date=None):
    """Targeted recompute for a specific set of asset ids, expanded to
    include anything nested inside them (if they're containers) - used by
    signal handlers for immediate feedback after one edit, instead of
    scanning the whole table every time."""
    from .models import Asset

    asset_ids = [aid for aid in asset_ids if aid is not None]
    if not asset_ids:
        return
    ids = _expand_through_nesting(set(asset_ids))
    job_ids = _job_committed_ids(on_date)
    structural_ids = _structural_ids()

    auto = Asset.AUTO_MANAGED_STATUSES
    candidate_ids = set(
        Asset.objects.filter(id__in=ids, archived=False, status__in=auto).values_list("id", flat=True)
    )
    _apply(candidate_ids, job_ids, structural_ids)


def recompute_all(on_date=None):
    """Recomputes and writes .status for every non-archived asset
    currently in an auto-managed state - manual states (Needs repair /
    Maintenance / Missing) are left untouched. A handful of queries total
    regardless of inventory size. Intended for nightly cron, to catch pure
    date-boundary crossings that no edit event would otherwise trigger."""
    from .models import Asset

    job_ids = _job_committed_ids(on_date)
    structural_ids = _structural_ids()

    auto = Asset.AUTO_MANAGED_STATUSES
    candidate_ids = set(
        Asset.objects.filter(archived=False, status__in=auto).values_list("id", flat=True)
    )
    _apply(candidate_ids, job_ids, structural_ids)
