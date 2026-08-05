import datetime
import json

from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ..models import Asset, AssetHistory, KitAssetTag, StaffMember, StockTakeEntry, StockTakeSession
from ..pdf_utils import build_stocktake_pdf


# Asset types that can appear in a stock take (excludes ENGINE and IO_DEVICE
# which have their own dedicated edit pages and aren't physically "loose" items)
STOCKTAKE_ASSET_TYPES = [
    Asset.AssetType.ENGINE,
    Asset.AssetType.IO_DEVICE,
    Asset.AssetType.PERIPHERAL,
    Asset.AssetType.CABLE,
    Asset.AssetType.STANDALONE,
    Asset.AssetType.COMPONENT,
    Asset.AssetType.LAPTOP,
]

STALENESS_PRESETS = [
    (30, "30 days"),
    (60, "60 days"),
    (90, "90 days"),
    (None, "All assets"),
]


def models_q_stale(cutoff):
    from django.db.models import Q
    return Q(last_updated_date__isnull=True) | Q(last_updated_date__lt=cutoff)


def stocktake_index_view(request):
    sessions = StockTakeSession.objects.select_related("created_by").prefetch_related("entries")

    # Staleness dashboard - count assets by band
    today = datetime.date.today()
    qs = Asset.objects.filter(archived=False).exclude(
        asset_type=Asset.AssetType.LICENSE
    )
    cutoff_90 = today - datetime.timedelta(days=90)
    cutoff_60 = today - datetime.timedelta(days=60)
    cutoff_30 = today - datetime.timedelta(days=30)

    band_90_plus = qs.filter(
        last_updated_date__lt=cutoff_90
    ).count() + qs.filter(last_updated_date__isnull=True).count()
    band_60_90 = qs.filter(last_updated_date__gte=cutoff_90, last_updated_date__lt=cutoff_60).count()
    band_30_60 = qs.filter(last_updated_date__gte=cutoff_60, last_updated_date__lt=cutoff_30).count()
    band_ok = qs.filter(last_updated_date__gte=cutoff_30).count()

    staleness = [
        {"label": "90+ days / never", "count": band_90_plus, "days": 90, "cls": "stale-red"},
        {"label": "60-90 days", "count": band_60_90, "days": 60, "cls": "stale-amber"},
        {"label": "30-60 days", "count": band_30_60, "days": 30, "cls": "stale-yellow"},
        {"label": "Under 30 days", "count": band_ok, "days": None, "cls": "stale-ok"},
    ]

    return render(request, "inventory/stocktake_index.html", {
        "sessions": sessions,
        "staleness": staleness,
        "active_nav": "stocktake",
    })


def stocktake_new_view(request):
    asset_type_choices = [
        (t.value, t.label) for t in Asset.AssetType
        if t.value in [x.value for x in STOCKTAKE_ASSET_TYPES]
    ]
    staff_members = StaffMember.objects.filter(active=True).order_by("name")
    current_staff = StaffMember.for_user(request.user)

    # Pre-select from staleness dashboard link
    preselect_days = request.GET.get("days")

    if request.method == "POST":
        label = request.POST.get("label", "").strip()
        selected_types = request.POST.getlist("asset_types")
        staleness_raw = request.POST.get("staleness_days", "").strip()
        staleness_days = int(staleness_raw) if staleness_raw.isdigit() else None
        created_by_id = request.POST.get("created_by", "").strip()

        if not selected_types:
            return render(request, "inventory/stocktake_new.html", {
                "asset_type_choices": asset_type_choices,
                "staleness_presets": STALENESS_PRESETS,
                "staff_members": staff_members,
                "current_staff": current_staff,
                "error": "Select at least one asset type.",
                "active_nav": "stocktake",
            })

        created_by = None
        if created_by_id.isdigit():
            created_by = StaffMember.objects.filter(pk=created_by_id).first()

        session = StockTakeSession.objects.create(
            label=label,
            asset_types=selected_types,
            staleness_days=staleness_days,
            created_by=created_by,
        )

        # Build the entry list - assets matching type filter, sorted stalest first
        qs = Asset.objects.filter(
            archived=False,
            asset_type__in=selected_types,
        ).select_related("last_updated_by")

        if staleness_days is not None:
            cutoff = datetime.date.today() - datetime.timedelta(days=staleness_days)
            qs = qs.filter(models_q_stale(cutoff))

        # Sort: null last_updated_date first (never updated), then oldest first
        assets = sorted(qs, key=lambda a: (
            a.last_updated_date is not None,
            a.last_updated_date or datetime.date.min,
        ))

        entries = [
            StockTakeEntry(
                session=session,
                asset=a,
                last_updated_snapshot=a.last_updated_date,
            )
            for a in assets
        ]
        StockTakeEntry.objects.bulk_create(entries)

        return redirect("stocktake_detail", session_id=session.pk)

    return render(request, "inventory/stocktake_new.html", {
        "asset_type_choices": asset_type_choices,
        "staleness_presets": STALENESS_PRESETS,
        "staff_members": staff_members,
        "current_staff": current_staff,
        "preselect_days": preselect_days,
        "active_nav": "stocktake",
    })


def stocktake_detail_view(request, session_id):
    session = get_object_or_404(
        StockTakeSession.objects.select_related("created_by").prefetch_related(
            "entries__asset__last_updated_by",
            "entries__reviewed_by",
        ),
        pk=session_id,
    )

    today = datetime.date.today()
    current_staff = StaffMember.for_user(request.user)

    def staleness_cls(d):
        if d is None:
            return "stale-red"
        days = (today - d).days
        if days >= 90:
            return "stale-red"
        if days >= 60:
            return "stale-amber"
        if days >= 30:
            return "stale-yellow"
        return "stale-ok"

    entries = []

    # Pre-fetch how many units of each bulk asset are committed to kits
    bulk_asset_ids = [e.asset_id for e in session.entries.all() if e.asset.qty > 1]
    committed_map = {}
    if bulk_asset_ids:
        for kat in KitAssetTag.objects.filter(asset_id__in=bulk_asset_ids).values("asset_id", "quantity"):
            committed_map[kat["asset_id"]] = committed_map.get(kat["asset_id"], 0) + kat["quantity"]

    for e in session.entries.all():
        a = e.asset
        d = a.last_updated_date
        entries.append({
            "entry": e,
            "asset": a,
            "staleness_cls": staleness_cls(d),
            "days_ago": (today - d).days if d else None,
            "is_bulk": a.qty > 1,
            "committed": committed_map.get(a.id, 0),
        })

    staff_members = StaffMember.objects.filter(active=True).order_by("name")
    status_choices = [
        (Asset.Status.AVAILABLE, "Available"),
        (Asset.Status.NEEDS_REPAIR, "Needs repair"),
        (Asset.Status.MAINTENANCE, "Maintenance"),
        (Asset.Status.MISSING, "Missing"),
    ]

    is_open = session.status == StockTakeSession.Status.IN_PROGRESS

    return render(request, "inventory/stocktake_detail.html", {
        "session": session,
        "entries": entries,
        "staff_members": staff_members,
        "current_staff": current_staff,
        "status_choices": status_choices,
        "active_nav": "stocktake",
        "Outcome": StockTakeEntry.Outcome,
        "is_open": is_open,
    })


@require_POST
def stocktake_review_entry(request, session_id, entry_id):
    """AJAX: mark a single entry as confirmed/flagged/skipped.
    Optionally update the asset status, qty, and notes, and bump last_updated."""
    session = get_object_or_404(StockTakeSession, pk=session_id)
    if session.status == StockTakeSession.Status.COMPLETE:
        return JsonResponse({"ok": False, "error": "Session is already closed."}, status=400)

    entry = get_object_or_404(StockTakeEntry, pk=entry_id, session=session)

    try:
        data = json.loads(request.body)
    except (ValueError, KeyError):
        return JsonResponse({"ok": False, "error": "Invalid request."}, status=400)

    outcome = data.get("outcome", "")
    notes = data.get("notes", "").strip()
    new_status = data.get("status", "").strip()
    reviewed_by_id = data.get("reviewed_by_id")
    new_qty_raw = data.get("new_qty", "")

    if outcome not in StockTakeEntry.Outcome.values:
        return JsonResponse({"ok": False, "error": "Invalid outcome."}, status=400)

    reviewed_by = None
    if reviewed_by_id:
        reviewed_by = StaffMember.objects.filter(pk=reviewed_by_id).first()

    entry.outcome = outcome
    entry.notes = notes
    entry.reviewed_at = datetime.datetime.now()
    entry.reviewed_by = reviewed_by
    entry.save()

    # If confirmed or flagged, bump the asset's last_updated fields
    if outcome in (StockTakeEntry.Outcome.CONFIRMED, StockTakeEntry.Outcome.FLAGGED):
        asset = entry.asset
        update_fields = ["last_updated_date", "last_updated_by", "last_updated_notes", "status"]

        asset.last_updated_date = datetime.date.today()
        asset.last_updated_by = reviewed_by
        asset.last_updated_notes = notes or f"Stock take - {outcome.lower()}"

        # Status change
        if new_status and new_status in Asset.Status.values:
            old_status = asset.status
            asset.status = new_status
            if old_status != new_status:
                AssetHistory.objects.create(
                    asset=asset,
                    changed_by=reviewed_by,
                    field_changed="status",
                    old_value=old_status,
                    new_value=new_status,
                    note=f"Stock take: {notes}" if notes else "Stock take",
                )

        # Quantity update - only for bulk assets, only if a valid number was sent
        qty_changed = False
        new_qty = None
        if asset.qty > 1 and new_qty_raw != "":
            try:
                new_qty = int(new_qty_raw)
                if new_qty >= 0 and new_qty != asset.qty:
                    old_qty = asset.qty
                    asset.qty = new_qty
                    update_fields.append("qty")
                    qty_changed = True
                    AssetHistory.objects.create(
                        asset=asset,
                        changed_by=reviewed_by,
                        field_changed="qty",
                        old_value=str(old_qty),
                        new_value=str(new_qty),
                        note=f"Stock take: physical count {new_qty}" + (f" - {notes}" if notes else ""),
                    )
            except (ValueError, TypeError):
                pass

        asset.save(update_fields=update_fields)

        note_text = f"Stock take - {outcome.lower()}: {notes}" if notes else f"Stock take - {outcome.lower()}"
        AssetHistory.record_note(asset, reviewed_by, before_note="", after_note=note_text)

    # Return fresh counts for the progress bar
    total = session.entries.count()
    reviewed = session.entries.exclude(outcome=StockTakeEntry.Outcome.PENDING).count()
    confirmed = session.entries.filter(outcome=StockTakeEntry.Outcome.CONFIRMED).count()
    flagged = session.entries.filter(outcome=StockTakeEntry.Outcome.FLAGGED).count()

    return JsonResponse({
        "ok": True,
        "total": total,
        "reviewed": reviewed,
        "confirmed": confirmed,
        "flagged": flagged,
        "pending": total - reviewed,
    })


@require_POST
def stocktake_close_view(request, session_id):
    session = get_object_or_404(StockTakeSession, pk=session_id, status=StockTakeSession.Status.IN_PROGRESS)
    session.status = StockTakeSession.Status.COMPLETE
    session.closed_at = datetime.datetime.now()
    session.save()
    return redirect("stocktake_detail", session_id=session.pk)


@require_POST
def stocktake_delete_view(request, session_id):
    session = get_object_or_404(StockTakeSession, pk=session_id)
    session.delete()
    return redirect("stocktake_index")


def stocktake_pdf_view(request, session_id):
    session = get_object_or_404(
        StockTakeSession.objects.select_related("created_by").prefetch_related(
            "entries__asset", "entries__reviewed_by"
        ),
        pk=session_id,
    )
    pdf_bytes = build_stocktake_pdf(session)
    filename = f"{session.display_label} - Stock Take.pdf".replace("/", "-")
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def stocktake_stale_view(request):
    """Browse stale assets filtered by staleness band, without starting a session."""
    today = datetime.date.today()
    days_param = request.GET.get("days", "")

    # Parse the days filter
    try:
        days = int(days_param) if days_param else None
    except ValueError:
        days = None

    qs = Asset.objects.filter(archived=False).exclude(
        asset_type=Asset.AssetType.LICENSE
    ).select_related("last_updated_by").order_by(
        "last_updated_date", "asset_id"
    )

    # Apply staleness filter
    if days is not None:
        cutoff = today - datetime.timedelta(days=days)
        # For "90 days" band we want exactly 90+ (and never updated)
        # For "60 days" we want 60-89 days
        # For "30 days" we want 30-59 days
        # For None (all) we want everything
        next_band = {90: None, 60: 90, 30: 60}.get(days)
        if next_band is not None:
            next_cutoff = today - datetime.timedelta(days=next_band)
            qs = qs.filter(
                models_q_stale(cutoff)
            ).exclude(
                last_updated_date__isnull=False,
                last_updated_date__lt=next_cutoff,
            )
        else:
            # 90+ days or never - include never-updated
            qs = qs.filter(models_q_stale(cutoff))
    # else: no filter, show all (link from "Under 30 days" card)

    # For "under 30 days" card (days=None from the URL but band is ok)
    under_30_param = request.GET.get("under30", "")
    if under_30_param == "1":
        cutoff_30 = today - datetime.timedelta(days=30)
        qs = Asset.objects.filter(
            archived=False,
            last_updated_date__gte=cutoff_30,
        ).exclude(asset_type=Asset.AssetType.LICENSE).select_related(
            "last_updated_by"
        ).order_by("last_updated_date", "asset_id")

    def staleness_cls(d):
        if d is None:
            return "stale-red"
        age = (today - d).days
        if age >= 90:
            return "stale-red"
        if age >= 60:
            return "stale-amber"
        if age >= 30:
            return "stale-yellow"
        return "stale-ok"

    assets = []
    for a in qs:
        d = a.last_updated_date
        assets.append({
            "asset": a,
            "days_ago": (today - d).days if d else None,
            "staleness_cls": staleness_cls(d),
        })

    # Band label for the page heading
    band_labels = {
        "90": "Not seen in 90+ days or never updated",
        "60": "Not seen in 60-90 days",
        "30": "Not seen in 30-60 days",
    }
    if under_30_param == "1":
        band_label = "Seen within the last 30 days"
    else:
        band_label = band_labels.get(days_param, "All assets")

    return render(request, "inventory/stocktake_stale.html", {
        "assets": assets,
        "band_label": band_label,
        "days_param": days_param,
        "under30": under_30_param,
        "active_nav": "stocktake",
    })
