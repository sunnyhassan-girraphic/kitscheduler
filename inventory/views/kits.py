import datetime

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ..models import Asset, Job, Kit, KitAssetTag, KitBooking, KitHistory, StaffMember, Tag
from ..pdf_utils import build_kit_checklist_pdf, get_kit_checklist_rows


@login_required
def kit_list_view(request):
    query = request.GET.get("q", "").strip()
    tab = request.GET.get("tab", "all")
    today = datetime.date.today()

    kits = Kit.objects.prefetch_related(
        "assets__nested_assets", "bookings__job", "kit_asset_tags__tag"
    )
    if query:
        kits = kits.filter(name__icontains=query)
    kits = list(kits.order_by("name"))

    if tab == "engines":
        kits = [k for k in kits if k.assets.filter(asset_type=Asset.AssetType.ENGINE).exists()]
    elif tab == "licenses":
        kits = [k for k in kits if k.assets.filter(asset_type=Asset.AssetType.LICENSE).exists()]
    elif tab == "booked":
        kits = [k for k in kits if any(b.start_date <= today <= b.end_date for b in k.bookings.all())]
    elif tab == "empty":
        kits = [k for k in kits if not k.assets.exists()]

    rows = []
    for kit in kits:
        tags_by_asset_id = {kat.asset_id: kat.tag for kat in kit.kit_asset_tags.all() if kat.tag_id}
        tags2_by_asset_id = {kat.asset_id: kat.tag_2 for kat in kit.kit_asset_tags.all() if kat.tag_2_id}
        qty_by_asset_id = {kat.asset_id: kat.quantity for kat in kit.kit_asset_tags.all()}
        members = list(kit.assets.all().order_by("asset_type", "asset_id"))
        member_ids = [m.id for m in members]
        # For each member, find other kits it also belongs to (excluding this kit)
        other_kits_by_asset = {}
        for kat in KitAssetTag.objects.filter(
            asset_id__in=member_ids
        ).exclude(kit=kit).select_related("kit"):
            other_kits_by_asset.setdefault(kat.asset_id, []).append(kat.kit.name)
        for m in members:
            m.kit_tag = tags_by_asset_id.get(m.id)
            m.kit_tag_2 = tags2_by_asset_id.get(m.id)
            m.kit_qty = qty_by_asset_id.get(m.id, 1)
            m.also_in_kits = other_kits_by_asset.get(m.id, [])
        nested_count = sum(
            m.nested_assets.count()
            + sum(c.nested_assets.count() for c in m.nested_assets.all() if c.asset_type in Asset.NESTABLE_CONTAINER_TYPES)
            for m in members if m.asset_type in Asset.CONTAINER_TYPES
        )
        current_booking = next(
            (b for b in kit.bookings.all() if b.start_date <= today <= b.end_date), None
        )
        job_color = current_booking.job.resolve_color() if current_booking else None
        # Keep DB status in sync with booking reality for display.
        # The signal does this on every booking save/delete, but existing kits
        # may have stale status — override in memory so the card always shows
        # the correct state without requiring a migration-based backfill.
        if current_booking and kit.status == Kit.Status.READY:
            kit.status = Kit.Status.BOOKED
        elif not current_booking and kit.status == Kit.Status.BOOKED:
            kit.status = Kit.Status.READY
        rows.append({
            "kit": kit,
            "members": members,
            "nested_count": nested_count,
            "current_booking": current_booking,
            "job_color": job_color,
        })

    context = {
        "rows": rows,
        "query": query,
        "tab": tab,
        "total_kits": len(kits),
        "active_nav": "kits",
    }
    return render(request, "inventory/kit_list.html", context)


def _kit_eligible_assets_qs(current_kit=None):
    """All non-archived, non-component, non-nested assets — shown in the
    picker regardless of kit membership or status. Callers use
    _kit_picker_assets() which annotates each asset with whether it is
    already in another kit (shown greyed/disabled in the UI) and handles
    bulk-quantity availability. The server-side save still validates that
    only genuinely addable assets are committed."""
    return Asset.objects.filter(
        archived=False
    ).exclude(
        asset_type=Asset.AssetType.COMPONENT
    ).exclude(
        Q(asset_type__in=Asset.NESTABLE_CONTAINER_TYPES) & Q(parent_engine__isnull=False)
    )


def _other_kit_asset_info(current_kit=None, date_from=None, date_to=None):
    """Returns dicts keyed by asset id for non-bulk (qty=1) assets in other kits.

    When date_from/date_to are provided, an asset is a hard conflict only if
    the other kit has a booking overlapping that window.

    When no dates are provided, we still do a date check against today so that
    assets in another kit that isn't currently booked can still be added
    (they get an informational 'also in Kit X' hint instead of a hard block).
    Only kits with an ACTIVE booking today are treated as hard conflicts when
    no explicit date range is given.
    """
    import datetime as _dt
    from ..models import KitBooking

    kat_qs = KitAssetTag.objects.select_related("kit", "asset").filter(asset__qty=1)
    if current_kit is not None:
        kat_qs = kat_qs.exclude(kit=current_kit)

    asset_to_kit = {}
    for kat in kat_qs:
        asset_to_kit[kat.asset_id] = kat.kit

    if not asset_to_kit:
        return {}, {}, {}

    other_kit_ids_set = {k.id for k in asset_to_kit.values()}

    # Use the provided date range, or fall back to today (so the picker is
    # still useful without explicit dates — only kits actively booked today
    # are hard conflicts, not kits sitting idle on the shelf).
    check_from = date_from or _dt.date.today()
    check_to = date_to or _dt.date.today()

    conflicting_kit_ids = set(
        KitBooking.objects.filter(
            kit_id__in=other_kit_ids_set,
            start_date__lte=check_to,
            end_date__gte=check_from,
        ).values_list("kit_id", flat=True)
    )

    other_kit_ids = set(asset_to_kit.keys())
    asset_kit_name = {aid: kit.name for aid, kit in asset_to_kit.items()}
    asset_booking_conflict = {
        aid: (kit.id in conflicting_kit_ids)
        for aid, kit in asset_to_kit.items()
    }
    return other_kit_ids, asset_kit_name, asset_booking_conflict


def _bulk_committed_elsewhere(current_kit=None):
    """For bulk/stock assets (qty > 1), how many units are already
    committed to OTHER kits. Keyed by asset id."""
    qs = KitAssetTag.objects.filter(asset__qty__gt=1)
    if current_kit is not None:
        qs = qs.exclude(kit=current_kit)
    committed = {}
    for asset_id, qty in qs.values_list("asset_id", "quantity"):
        committed[asset_id] = committed.get(asset_id, 0) + qty
    return committed


def _kit_picker_assets(current_kit=None, date_from=None, date_to=None):
    """All picker-eligible assets, annotated with kit-membership and availability info.

    date_from / date_to are optional datetime.date objects. When provided, the
    'inAnotherKit' hint is date-aware: assets in a different kit are only a
    hard conflict if that kit has a booking overlapping the given window. If the
    dates don't overlap, the Add button is shown with a softer 'also in Kit X'
    informational hint, and bookingConflict=False in the JSON.
    """
    assets = _kit_eligible_assets_qs(current_kit).order_by(
        "asset_type", "asset_id"
    ).prefetch_related("nested_assets")
    committed_elsewhere = _bulk_committed_elsewhere(current_kit)
    other_kit_ids, asset_kit_name, asset_booking_conflict = _other_kit_asset_info(
        current_kit, date_from=date_from, date_to=date_to
    )

    data = []
    for a in assets:
        nested = []
        if a.asset_type in Asset.CONTAINER_TYPES:
            nested = [
                {"assetId": n.asset_id, "makeModel": n.make_model}
                for n in a.nested_assets.all()
            ]
        is_bulk = a.qty > 1
        available = max(a.qty - committed_elsewhere.get(a.id, 0), 0) if is_bulk else 1
        in_another_kit = not is_bulk and a.id in other_kit_ids
        booking_conflict = in_another_kit and asset_booking_conflict.get(a.id, True)
        data.append({
            "id": a.id, "assetId": a.asset_id, "makeModel": a.make_model,
            "type": a.get_asset_type_display(), "status": a.status.lower(),
            "statusDisplay": a.get_status_display(), "nested": nested,
            "isBulk": is_bulk, "totalQty": a.qty, "available": available,
            "inAnotherKit": in_another_kit,
            "bookingConflict": booking_conflict,
            "otherKitName": asset_kit_name.get(a.id, ""),
        })
    return list(assets), data


def _tags_json():
    return [{"id": t.id, "name": t.name, "color": t.color} for t in Tag.objects.all()]


def _jobs_json():
    """All jobs as JSON for the kit form's job picker.
    Ordered by start_date desc so most recent/upcoming are at the top."""
    today = datetime.date.today()
    jobs = Job.objects.order_by("-start_date").values(
        "id", "name", "category", "start_date", "end_date"
    )
    result = []
    for j in jobs:
        if j["end_date"] < today:
            status_label = "Done"
            status_class = "job-status-done"
        elif j["start_date"] > today:
            status_label = "Upcoming"
            status_class = "job-status-upcoming"
        else:
            status_label = "Active"
            status_class = "job-status-active"
        result.append({
            "id": j["id"],
            "name": j["name"],
            "category": j["category"],
            "startDate": j["start_date"].isoformat(),
            "endDate": j["end_date"].isoformat(),
            "displayRange": f"{j['start_date'].strftime('%d %b')} – {j['end_date'].strftime('%d %b %Y')}",
            "label": f"{j['name']} ({j['start_date'].strftime('%d %b')} – {j['end_date'].strftime('%d %b %Y')})",
            "statusLabel": status_label,
            "statusClass": status_class,
        })
    return result


def _apply_kit_tag_selection(kit, selected_ids, tag_by_asset_id, qty_by_asset_id=None, tag2_by_asset_id=None):
    """Sync KitAssetTag rows for a kit: keep tags/quantity for assets that
    stay, create rows (with tag/quantity) for newly-added assets, remove
    rows for assets no longer in the kit. Returns (before_ids, after_ids,
    before_qty, after_qty) so the caller can log history."""
    qty_by_asset_id = qty_by_asset_id or {}
    tag2_by_asset_id = tag2_by_asset_id or {}
    selected_ids = set(selected_ids)
    existing = {kat.asset_id: kat for kat in kit.kit_asset_tags.all()}
    before_ids = set(existing)
    before_qty = {aid: kat.quantity for aid, kat in existing.items()}
    committed_elsewhere = _bulk_committed_elsewhere(current_kit=kit)
    assets_by_id = {a.id: a for a in Asset.objects.filter(id__in=selected_ids)}

    for asset_id in set(existing) - selected_ids:
        existing[asset_id].delete()

    after_qty = {}
    for asset_id in selected_ids:
        asset = assets_by_id.get(asset_id)
        is_engine = asset and asset.asset_type == Asset.AssetType.ENGINE
        tag_id = tag_by_asset_id.get(asset_id) if is_engine else None
        tag_id = tag_id if tag_id else None
        # tag_2 only applies to G3 engines (make_model contains "G3")
        is_g3 = is_engine and asset and "G3" in (asset.make_model or "").upper()
        tag_2_id = tag2_by_asset_id.get(asset_id) if is_g3 else None
        tag_2_id = tag_2_id if tag_2_id else None

        if asset and asset.qty > 1:
            available = max(asset.qty - committed_elsewhere.get(asset_id, 0), 0)
            requested = qty_by_asset_id.get(asset_id, 1)
            quantity = max(1, min(requested, available)) if available > 0 else 1
        else:
            quantity = 1
        after_qty[asset_id] = quantity

        if asset_id in existing:
            row = existing[asset_id]
            changed = []
            if row.tag_id != tag_id:
                row.tag_id = tag_id
                changed.append("tag_id")
            if row.tag_2_id != tag_2_id:
                row.tag_2_id = tag_2_id
                changed.append("tag_2_id")
            if row.quantity != quantity:
                row.quantity = quantity
                changed.append("quantity")
            if changed:
                row.save(update_fields=changed)
        else:
            KitAssetTag.objects.create(
                kit=kit, asset_id=asset_id,
                tag_id=tag_id, tag_2_id=tag_2_id, quantity=quantity,
            )

    return before_ids, selected_ids, before_qty, after_qty


@login_required
def _parse_picker_dates(request):
    """Parse optional ?from=YYYY-MM-DD&to=YYYY-MM-DD query params for date-aware
    conflict checking in the asset picker. Returns (date_from, date_to) or (None, None)."""
    try:
        date_from = datetime.date.fromisoformat(request.GET.get("from", ""))
        date_to = datetime.date.fromisoformat(request.GET.get("to", ""))
        if date_from <= date_to:
            return date_from, date_to
    except (ValueError, TypeError):
        pass
    return None, None


@login_required
def kit_create_view(request):
    date_from, date_to = _parse_picker_dates(request)
    assets, assets_json = _kit_picker_assets(date_from=date_from, date_to=date_to)
    tags_json = _tags_json()
    jobs_json = _jobs_json()

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        notes = request.POST.get("notes", "").strip()
        asset_ids = request.POST.getlist("assets")
        selected_ids = [int(i) for i in asset_ids if i.isdigit()]
        tag_by_asset_id = {}
        tag2_by_asset_id = {}
        qty_by_asset_id = {}
        for aid in selected_ids:
            raw = request.POST.get(f"tag_{aid}", "").strip()
            if raw.isdigit():
                tag_by_asset_id[aid] = int(raw)
            raw2 = request.POST.get(f"tag2_{aid}", "").strip()
            if raw2.isdigit():
                tag2_by_asset_id[aid] = int(raw2)
            raw_qty = request.POST.get(f"qty_{aid}", "").strip()
            if raw_qty.isdigit() and int(raw_qty) > 0:
                qty_by_asset_id[aid] = int(raw_qty)

        # Job assignment fields
        linked_job_id = request.POST.get("linked_job_id", "").strip()
        booking_start = request.POST.get("booking_start", "").strip()
        booking_end = request.POST.get("booking_end", "").strip()

        if not name:
            return render(request, "inventory/kit_form.html", {
                "assets": assets, "assets_json": assets_json, "tags_json": tags_json,
                "jobs_json": jobs_json,
                "error": "Kit name is required.",
                "selected_ids": selected_ids, "notes": notes, "active_nav": "kits",
            })

        if Kit.objects.filter(name=name).exists():
            return render(request, "inventory/kit_form.html", {
                "assets": assets, "assets_json": assets_json, "tags_json": tags_json,
                "jobs_json": jobs_json,
                "error": f'A kit named "{name}" already exists.',
                "selected_ids": selected_ids, "name": name, "notes": notes, "active_nav": "kits",
            })

        raw_status = request.POST.get("status", Kit.Status.READY).strip()
        status = raw_status if raw_status in [s for s in Kit.Status.values if s != Kit.Status.BOOKED] else Kit.Status.READY
        kit = Kit.objects.create(name=name, notes=notes, status=status)
        changed_by = StaffMember.for_user(request.user)
        KitHistory.objects.create(kit=kit, changed_by=changed_by, field_changed="created")
        KitHistory.record_note(kit, changed_by, "", notes)
        if selected_ids:
            valid_ids = list(_kit_eligible_assets_qs(kit).filter(id__in=selected_ids).values_list("id", flat=True))
            _, after_ids, _, after_qty = _apply_kit_tag_selection(kit, valid_ids, tag_by_asset_id, qty_by_asset_id, tag2_by_asset_id)
            KitHistory.record_asset_changes(kit, [], after_ids, changed_by)

        # Create KitBooking if a job was linked with valid dates
        if linked_job_id.isdigit() and booking_start and booking_end:
            try:
                job = Job.objects.get(pk=int(linked_job_id))
                b_start = datetime.date.fromisoformat(booking_start)
                b_end = datetime.date.fromisoformat(booking_end)
                if b_start <= b_end:
                    KitBooking.objects.create(kit=kit, job=job, start_date=b_start, end_date=b_end)
                    KitHistory.objects.create(
                        kit=kit, changed_by=changed_by, field_changed="job_assigned",
                        new_value=f"{job.name} ({b_start} to {b_end})",
                    )
            except (Job.DoesNotExist, ValueError):
                pass

        return redirect("/kits/")

    return render(request, "inventory/kit_form.html", {
        "assets": assets, "assets_json": assets_json, "tags_json": tags_json,
        "jobs_json": jobs_json,
        "selected_ids": [], "active_nav": "kits",
        "kit_status_choices": Kit.Status.choices,
        "status": Kit.Status.READY,
        "picker_date_from": date_from,
        "picker_date_to": date_to,
    })


def _packed_by_default(user):
    """First and last name of the logged-in Django user, parsed together
    (e.g. 'Sunny Hassan'). Falls back to the linked StaffMember's name if
    the Django login has no first/last name set, then to the username."""
    first = (getattr(user, "first_name", "") or "").strip()
    last = (getattr(user, "last_name", "") or "").strip()
    full = f"{first} {last}".strip()
    if full:
        return full
    staff = StaffMember.for_user(user)
    if staff:
        return staff.name
    return getattr(user, "username", "") or ""


@login_required
def kit_edit_view(request, kit_id):
    kit = get_object_or_404(Kit, pk=kit_id)
    date_from, date_to = _parse_picker_dates(request)
    # If no explicit date range was passed, use this kit's own future/active
    # booking window so the conflict check is meaningful.
    if not (date_from and date_to):
        kit_booking = kit.bookings.order_by("start_date").first()
        if kit_booking:
            date_from = kit_booking.start_date
            date_to = kit_booking.end_date
    assets, assets_json = _kit_picker_assets(current_kit=kit, date_from=date_from, date_to=date_to)
    tags_json = _tags_json()
    jobs_json = _jobs_json()

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        notes = request.POST.get("notes", "").strip()
        asset_ids = request.POST.getlist("assets")
        selected_ids = [int(i) for i in asset_ids if i.isdigit()]
        tag_by_asset_id = {}
        tag2_by_asset_id = {}
        qty_by_asset_id = {}
        for aid in selected_ids:
            raw = request.POST.get(f"tag_{aid}", "").strip()
            if raw.isdigit():
                tag_by_asset_id[aid] = int(raw)
            raw2 = request.POST.get(f"tag2_{aid}", "").strip()
            if raw2.isdigit():
                tag2_by_asset_id[aid] = int(raw2)
            raw_qty = request.POST.get(f"qty_{aid}", "").strip()
            if raw_qty.isdigit() and int(raw_qty) > 0:
                qty_by_asset_id[aid] = int(raw_qty)

        # Job assignment fields
        linked_job_id = request.POST.get("linked_job_id", "").strip()
        booking_start = request.POST.get("booking_start", "").strip()
        booking_end = request.POST.get("booking_end", "").strip()
        remove_booking = request.POST.get("remove_booking") == "1"

        if not name:
            return render(request, "inventory/kit_form.html", {
                "kit": kit, "assets": assets, "assets_json": assets_json,
                "tags_json": tags_json, "jobs_json": jobs_json,
                "error": "Kit name is required.",
                "selected_ids": selected_ids, "notes": notes, "active_nav": "kits",
            })

        if Kit.objects.filter(name=name).exclude(pk=kit_id).exists():
            return render(request, "inventory/kit_form.html", {
                "kit": kit, "assets": assets, "assets_json": assets_json,
                "tags_json": tags_json, "jobs_json": jobs_json,
                "error": f'A kit named "{name}" already exists.',
                "selected_ids": selected_ids, "name": name, "notes": notes, "active_nav": "kits",
            })

        raw_status = request.POST.get("status", kit.status).strip()
        new_status = raw_status if raw_status in [s for s in Kit.Status.values if s != Kit.Status.BOOKED] else kit.status

        before_name = kit.name
        before_note = kit.notes
        before_status = kit.status
        kit.name = name
        kit.notes = notes
        kit.status = new_status
        kit.save()

        changed_by = StaffMember.for_user(request.user)
        if before_name != kit.name:
            KitHistory.objects.create(
                kit=kit, changed_by=changed_by, field_changed="name",
                old_value=before_name, new_value=kit.name,
            )
        if before_status != kit.status:
            KitHistory.objects.create(
                kit=kit, changed_by=changed_by, field_changed="status",
                old_value=before_status, new_value=kit.status,
            )
        KitHistory.record_note(kit, changed_by, before_note, notes)

        valid_ids = list(_kit_eligible_assets_qs(kit).filter(id__in=selected_ids).values_list("id", flat=True))
        before_ids, after_ids, before_qty, after_qty = _apply_kit_tag_selection(
            kit, valid_ids, tag_by_asset_id, qty_by_asset_id, tag2_by_asset_id
        )
        KitHistory.record_asset_changes(kit, before_ids, after_ids, changed_by)
        KitHistory.record_quantity_changes(kit, before_qty, after_qty, changed_by)

        # Handle job booking changes
        existing_booking = kit.bookings.order_by("start_date").first()
        if remove_booking and existing_booking:
            KitHistory.objects.create(
                kit=kit, changed_by=changed_by, field_changed="job_removed",
                old_value=f"{existing_booking.job.name} ({existing_booking.start_date} to {existing_booking.end_date})",
            )
            existing_booking.delete()
        elif linked_job_id.isdigit() and booking_start and booking_end:
            try:
                job = Job.objects.get(pk=int(linked_job_id))
                b_start = datetime.date.fromisoformat(booking_start)
                b_end = datetime.date.fromisoformat(booking_end)
                if b_start <= b_end:
                    if existing_booking:
                        # Update existing booking
                        old_desc = f"{existing_booking.job.name} ({existing_booking.start_date} to {existing_booking.end_date})"
                        existing_booking.job = job
                        existing_booking.start_date = b_start
                        existing_booking.end_date = b_end
                        existing_booking.save()
                        new_desc = f"{job.name} ({b_start} to {b_end})"
                        if old_desc != new_desc:
                            KitHistory.objects.create(
                                kit=kit, changed_by=changed_by, field_changed="job_changed",
                                old_value=old_desc, new_value=new_desc,
                            )
                    else:
                        KitBooking.objects.create(kit=kit, job=job, start_date=b_start, end_date=b_end)
                        KitHistory.objects.create(
                            kit=kit, changed_by=changed_by, field_changed="job_assigned",
                            new_value=f"{job.name} ({b_start} to {b_end})",
                        )
            except (Job.DoesNotExist, ValueError):
                pass

        return redirect("/kits/")

    # Build current booking context for the form
    current_booking = kit.bookings.select_related("job").order_by("start_date").first()

    kit_asset_tags = list(kit.kit_asset_tags.order_by("created_at"))
    selected_tags_json = {kat.asset_id: kat.tag_id for kat in kit_asset_tags if kat.tag_id}
    selected_tags2_json = {kat.asset_id: kat.tag_2_id for kat in kit_asset_tags if kat.tag_2_id}
    selected_qty_json = {kat.asset_id: kat.quantity for kat in kit_asset_tags}
    history_mode = request.GET.get("history", "week")
    if history_mode not in ("week", "month", "all"):
        history_mode = "week"
    history_page_num = request.GET.get("history_page", 1)
    return render(request, "inventory/kit_form.html", {
        "kit": kit,
        "assets": assets,
        "assets_json": assets_json,
        "tags_json": tags_json,
        "jobs_json": jobs_json,
        "current_booking": current_booking,
        "selected_ids": [kat.asset_id for kat in kit_asset_tags],
        "selected_tags_json": selected_tags_json,
        "selected_tags2_json": selected_tags2_json,
        "selected_qty_json": selected_qty_json,
        "name": kit.name,
        "notes": kit.notes,
        "status": kit.status,
        "kit_status_choices": Kit.Status.choices,
        "picker_date_from": date_from,
        "picker_date_to": date_to,
        "active_nav": "kits",
        "pdf_items": get_kit_checklist_rows(kit),
        "history_mode": history_mode,
        "history_page": KitHistory.filtered_for(kit, mode=history_mode, page=history_page_num),
        "pdf_packed_by_default": _packed_by_default(request.user),
        "pdf_event_date_default": datetime.date.today().strftime("%d/%m/%Y"),
        "pdf_event_date_iso": datetime.date.today().isoformat(),
    })

@login_required
@require_POST
def kit_delete_view(request, kit_id):
    kit = get_object_or_404(Kit, pk=kit_id)
    kit.delete()
    return redirect("/kits/")


@login_required
def kit_set_status_view(request, kit_id):
    """POST-only API: set Kit.status from the traffic-light dropdown on the
    kit card. Only accepts valid non-BOOKED values (BOOKED is auto-managed).
    Optional: pass remove_booking=1 to also delete the kit's current booking.
    Returns JSON {status, statusDisplay, bookingRemoved} on success."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required."}, status=405)
    kit = get_object_or_404(Kit, pk=kit_id)
    new_status = request.POST.get("status", "").strip()
    allowed = [s for s in Kit.Status.values if s != Kit.Status.BOOKED]
    if new_status not in allowed:
        return JsonResponse({"error": f"Invalid status '{new_status}'."}, status=400)
    old_status = kit.status
    kit.status = new_status
    kit.save(update_fields=["status"])
    changed_by = StaffMember.for_user(request.user)
    KitHistory.objects.create(
        kit=kit, changed_by=changed_by, field_changed="status",
        old_value=old_status, new_value=new_status,
    )

    booking_removed = False
    if request.POST.get("remove_booking") == "1":
        existing = kit.bookings.order_by("start_date").first()
        if existing:
            KitHistory.objects.create(
                kit=kit, changed_by=changed_by, field_changed="job_removed",
                old_value=f"{existing.job.name} ({existing.start_date} to {existing.end_date})",
            )
            existing.delete()
            booking_removed = True

    # If status was set to READY but an active booking exists today,
    # immediately recompute to BOOKED so the response reflects truth.
    if new_status == Kit.Status.READY and not booking_removed:
        from inventory.signals import _recompute_kit_status
        _recompute_kit_status(kit.pk)
        kit.refresh_from_db(fields=["status"])

    return JsonResponse({
        "status": kit.status,
        "statusDisplay": kit.get_status_display(),
        "bookingRemoved": booking_removed,
        "isBooked": kit.status == Kit.Status.BOOKED,
    })


@login_required
def kit_booking_info_view(request, kit_id):
    """GET: returns the kit's current active/upcoming booking as JSON.
    Used by the status-change modal to show which job will be affected."""
    kit = get_object_or_404(Kit, pk=kit_id)
    booking = kit.bookings.select_related("job").order_by("start_date").first()
    if booking:
        return JsonResponse({
            "hasBooking": True,
            "jobName": booking.job.name,
            "startDate": booking.start_date.isoformat(),
            "endDate": booking.end_date.isoformat(),
            "bookingId": booking.id,
        })
    return JsonResponse({"hasBooking": False})


@login_required
def kit_pdf_view(request, kit_id):
    kit = get_object_or_404(Kit, pk=kit_id)
    meta = {
        "packed_by": request.GET.get("packed_by", ""),
        "event_date": request.GET.get("event_date", ""),
        "gps_tag": request.GET.get("gps_tag", ""),
        "carnet": request.GET.get("carnet", ""),
        "cases": request.GET.get("cases", ""),
    }

    item_overrides = {}
    for row in get_kit_checklist_rows(kit):
        asset_id = row["id"]
        case_val = request.GET.get(f"case_{asset_id}", "").strip()
        checked_val = request.GET.get(f"checked_{asset_id}", "") == "1"
        qty_val = request.GET.get(f"qty_{asset_id}", "").strip()
        override = {}
        if case_val:
            override["case"] = case_val
        if checked_val:
            override["checked"] = checked_val
        if qty_val.isdigit() and int(qty_val) > 0:
            override["qty"] = int(qty_val)
        if override:
            item_overrides[asset_id] = override

    pdf_bytes = build_kit_checklist_pdf(kit, meta, item_overrides)
    filename = f"{kit.name} - Kit Checklist.pdf".replace("/", "-")
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


