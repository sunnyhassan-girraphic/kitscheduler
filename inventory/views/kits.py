import datetime

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ..models import Asset, Kit, KitAssetTag, KitHistory, StaffMember, Tag
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
        qty_by_asset_id = {kat.asset_id: kat.quantity for kat in kit.kit_asset_tags.all()}
        members = list(kit.assets.all().order_by("asset_type", "asset_id"))
        for m in members:
            m.kit_tag = tags_by_asset_id.get(m.id)
            m.kit_qty = qty_by_asset_id.get(m.id, 1)
        nested_count = sum(
            m.nested_assets.count()
            + sum(c.nested_assets.count() for c in m.nested_assets.all() if c.asset_type in Asset.NESTABLE_CONTAINER_TYPES)
            for m in members if m.asset_type in Asset.CONTAINER_TYPES
        )
        current_booking = next(
            (b for b in kit.bookings.all() if b.start_date <= today <= b.end_date), None
        )
        rows.append({
            "kit": kit,
            "members": members,
            "nested_count": nested_count,
            "current_booking": current_booking,
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
    """Assets eligible to be direct kit members: not archived, not a COMPONENT,
    not an I/O Device already nested inside an Engine (it travels with that
    Engine automatically). Individually-tracked assets (qty == 1) are
    exclusive to one kit at a time, same as before. Bulk/stock assets
    (qty > 1, e.g. a 'Wired Mouse' row representing several identical
    units) can appear in multiple kits at once as long as some quantity
    remains uncommitted - see _bulk_asset_availability(). Assets already in
    the current kit are always included so they remain visible/removable
    even in edge cases."""
    other_kit_asset_ids = set(
        Kit.objects.exclude(pk=current_kit.pk if current_kit else None)
        .values_list("assets__id", flat=True)
    )
    other_kit_asset_ids.discard(None)
    other_kit_container_ids = set(
        Asset.objects.filter(
            id__in=other_kit_asset_ids, asset_type__in=Asset.CONTAINER_TYPES
        ).values_list("id", flat=True)
    )
    # I/O Devices nested inside one of those Engines are containers too -
    # their own nested components must be excluded from the picker as well.
    other_kit_container_ids |= set(
        Asset.objects.filter(
            parent_engine_id__in=other_kit_container_ids, asset_type__in=Asset.NESTABLE_CONTAINER_TYPES
        ).values_list("id", flat=True)
    )
    current_kit_asset_ids = set(current_kit.assets.values_list("id", flat=True)) if current_kit else set()
    # Bulk assets (qty > 1) with any quantity still uncommitted elsewhere -
    # these stay selectable even though they're technically "in" another kit.
    bulk_available_ids = set(
        Asset.objects.filter(qty__gt=1).exclude(id__in=current_kit_asset_ids).values_list("id", flat=True)
    )

    return Asset.objects.filter(
        archived=False
    ).exclude(
        asset_type=Asset.AssetType.COMPONENT
    ).exclude(
        Q(asset_type__in=Asset.NESTABLE_CONTAINER_TYPES) & Q(parent_engine__isnull=False)
    ).filter(
        Q(id__in=current_kit_asset_ids)
        | Q(id__in=bulk_available_ids)
        | (~Q(id__in=other_kit_asset_ids) & ~Q(parent_engine_id__in=other_kit_container_ids))
    )


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


def _kit_picker_assets(current_kit=None):
    """Assets eligible to be direct kit members, with nested-component info for engines/I-O devices."""
    assets = _kit_eligible_assets_qs(current_kit).order_by(
        "asset_type", "asset_id"
    ).prefetch_related("nested_assets")
    committed_elsewhere = _bulk_committed_elsewhere(current_kit)

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
        data.append({
            "id": a.id, "assetId": a.asset_id, "makeModel": a.make_model,
            "type": a.get_asset_type_display(), "status": a.status.lower(),
            "statusDisplay": a.get_status_display(), "nested": nested,
            "isBulk": is_bulk, "totalQty": a.qty, "available": available,
        })
    return list(assets), data


def _tags_json():
    return [{"id": t.id, "name": t.name, "color": t.color} for t in Tag.objects.all()]


def _apply_kit_tag_selection(kit, selected_ids, tag_by_asset_id, qty_by_asset_id=None):
    """Sync KitAssetTag rows for a kit: keep tags/quantity for assets that
    stay, create rows (with tag/quantity) for newly-added assets, remove
    rows for assets no longer in the kit. Returns (before_ids, after_ids,
    before_qty, after_qty) so the caller can log history."""
    qty_by_asset_id = qty_by_asset_id or {}
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
        tag_id = tag_by_asset_id.get(asset_id) if asset and asset.asset_type == Asset.AssetType.ENGINE else None
        tag_id = tag_id if tag_id else None

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
                changed.append("tag")
            if row.quantity != quantity:
                row.quantity = quantity
                changed.append("quantity")
            if changed:
                row.save(update_fields=changed)
        else:
            KitAssetTag.objects.create(kit=kit, asset_id=asset_id, tag_id=tag_id, quantity=quantity)

    return before_ids, selected_ids, before_qty, after_qty


@login_required
def kit_create_view(request):
    assets, assets_json = _kit_picker_assets()
    tags_json = _tags_json()

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        notes = request.POST.get("notes", "").strip()
        asset_ids = request.POST.getlist("assets")
        selected_ids = [int(i) for i in asset_ids if i.isdigit()]
        tag_by_asset_id = {}
        qty_by_asset_id = {}
        for aid in selected_ids:
            raw = request.POST.get(f"tag_{aid}", "").strip()
            if raw.isdigit():
                tag_by_asset_id[aid] = int(raw)
            raw_qty = request.POST.get(f"qty_{aid}", "").strip()
            if raw_qty.isdigit() and int(raw_qty) > 0:
                qty_by_asset_id[aid] = int(raw_qty)

        if not name:
            return render(request, "inventory/kit_form.html", {
                "assets": assets, "assets_json": assets_json, "tags_json": tags_json,
                "error": "Kit name is required.",
                "selected_ids": selected_ids, "notes": notes, "active_nav": "kits",
            })

        if Kit.objects.filter(name=name).exists():
            return render(request, "inventory/kit_form.html", {
                "assets": assets, "assets_json": assets_json, "tags_json": tags_json,
                "error": f'A kit named "{name}" already exists.',
                "selected_ids": selected_ids, "name": name, "notes": notes, "active_nav": "kits",
            })

        kit = Kit.objects.create(name=name, notes=notes)
        changed_by = StaffMember.for_user(request.user)
        KitHistory.objects.create(kit=kit, changed_by=changed_by, field_changed="created")
        KitHistory.record_note(kit, changed_by, "", notes)
        if selected_ids:
            valid_ids = list(_kit_eligible_assets_qs(kit).filter(id__in=selected_ids).values_list("id", flat=True))
            _, after_ids, _, after_qty = _apply_kit_tag_selection(kit, valid_ids, tag_by_asset_id, qty_by_asset_id)
            KitHistory.record_asset_changes(kit, [], after_ids, changed_by)

        return redirect("/kits/")

    return render(request, "inventory/kit_form.html", {
        "assets": assets, "assets_json": assets_json, "tags_json": tags_json,
        "selected_ids": [], "active_nav": "kits",
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
    assets, assets_json = _kit_picker_assets(current_kit=kit)
    tags_json = _tags_json()

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        notes = request.POST.get("notes", "").strip()
        asset_ids = request.POST.getlist("assets")
        selected_ids = [int(i) for i in asset_ids if i.isdigit()]
        tag_by_asset_id = {}
        qty_by_asset_id = {}
        for aid in selected_ids:
            raw = request.POST.get(f"tag_{aid}", "").strip()
            if raw.isdigit():
                tag_by_asset_id[aid] = int(raw)
            raw_qty = request.POST.get(f"qty_{aid}", "").strip()
            if raw_qty.isdigit() and int(raw_qty) > 0:
                qty_by_asset_id[aid] = int(raw_qty)

        if not name:
            return render(request, "inventory/kit_form.html", {
                "kit": kit, "assets": assets, "assets_json": assets_json, "tags_json": tags_json,
                "error": "Kit name is required.",
                "selected_ids": selected_ids, "notes": notes, "active_nav": "kits",
            })

        if Kit.objects.filter(name=name).exclude(pk=kit_id).exists():
            return render(request, "inventory/kit_form.html", {
                "kit": kit, "assets": assets, "assets_json": assets_json, "tags_json": tags_json,
                "error": f'A kit named "{name}" already exists.',
                "selected_ids": selected_ids, "name": name, "notes": notes, "active_nav": "kits",
            })

        before_name = kit.name
        before_note = kit.notes
        kit.name = name
        kit.notes = notes
        kit.save()

        changed_by = StaffMember.for_user(request.user)
        if before_name != kit.name:
            KitHistory.objects.create(
                kit=kit, changed_by=changed_by, field_changed="name",
                old_value=before_name, new_value=kit.name,
            )
        KitHistory.record_note(kit, changed_by, before_note, notes)

        valid_ids = list(_kit_eligible_assets_qs(kit).filter(id__in=selected_ids).values_list("id", flat=True))
        before_ids, after_ids, before_qty, after_qty = _apply_kit_tag_selection(
            kit, valid_ids, tag_by_asset_id, qty_by_asset_id
        )
        KitHistory.record_asset_changes(kit, before_ids, after_ids, changed_by)
        KitHistory.record_quantity_changes(kit, before_qty, after_qty, changed_by)

        return redirect("/kits/")

    kit_asset_tags = list(kit.kit_asset_tags.order_by("created_at"))
    selected_tags_json = {kat.asset_id: kat.tag_id for kat in kit_asset_tags if kat.tag_id}
    selected_qty_json = {kat.asset_id: kat.quantity for kat in kit_asset_tags}
    history_mode = request.GET.get("history", "month")
    if history_mode not in ("week", "month", "all"):
        history_mode = "month"
    history_page_num = request.GET.get("history_page", 1)
    return render(request, "inventory/kit_form.html", {
        "kit": kit,
        "assets": assets,
        "assets_json": assets_json,
        "tags_json": tags_json,
        "selected_ids": [kat.asset_id for kat in kit_asset_tags],
        "selected_tags_json": selected_tags_json,
        "selected_qty_json": selected_qty_json,
        "name": kit.name,
        "notes": kit.notes,
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


