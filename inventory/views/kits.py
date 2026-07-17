import datetime

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ..models import Asset, Kit, KitAssetTag, Tag
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
        members = list(kit.assets.all().order_by("asset_type", "asset_id"))
        for m in members:
            m.kit_tag = tags_by_asset_id.get(m.id)
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
    Engine automatically), and not already claimed by a DIFFERENT kit
    (directly, or nested inside a container - Engine or I/O Device - that's
    a member of a different kit) - an asset can only live in one kit at a
    time. Assets already in the current kit are always included so they
    remain visible/removable even in edge cases."""
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

    return Asset.objects.filter(
        archived=False
    ).exclude(
        asset_type=Asset.AssetType.COMPONENT
    ).exclude(
        Q(asset_type__in=Asset.NESTABLE_CONTAINER_TYPES) & Q(parent_engine__isnull=False)
    ).filter(
        Q(id__in=current_kit_asset_ids)
        | (~Q(id__in=other_kit_asset_ids) & ~Q(parent_engine_id__in=other_kit_container_ids))
    )


def _kit_picker_assets(current_kit=None):
    """Assets eligible to be direct kit members, with nested-component info for engines/I-O devices."""
    assets = _kit_eligible_assets_qs(current_kit).order_by(
        "asset_type", "asset_id"
    ).prefetch_related("nested_assets")

    data = []
    for a in assets:
        nested = []
        if a.asset_type in Asset.CONTAINER_TYPES:
            nested = [
                {"assetId": n.asset_id, "makeModel": n.make_model}
                for n in a.nested_assets.all()
            ]
        data.append({
            "id": a.id, "assetId": a.asset_id, "makeModel": a.make_model,
            "type": a.get_asset_type_display(), "status": a.status.lower(),
            "statusDisplay": a.get_status_display(), "nested": nested,
        })
    return list(assets), data


def _tags_json():
    return [{"id": t.id, "name": t.name, "color": t.color} for t in Tag.objects.all()]


def _apply_kit_tag_selection(kit, selected_ids, tag_by_asset_id):
    """Sync KitAssetTag rows for a kit: keep tags for assets that stay,
    create rows (with tag) for newly-added assets, remove rows for assets
    no longer in the kit."""
    selected_ids = set(selected_ids)
    existing = {kat.asset_id: kat for kat in kit.kit_asset_tags.all()}

    for asset_id in set(existing) - selected_ids:
        existing[asset_id].delete()

    for asset_id in selected_ids:
        tag_id = tag_by_asset_id.get(asset_id)
        tag_id = tag_id if tag_id else None
        if asset_id in existing:
            row = existing[asset_id]
            if row.tag_id != tag_id:
                row.tag_id = tag_id
                row.save(update_fields=["tag"])
        else:
            KitAssetTag.objects.create(kit=kit, asset_id=asset_id, tag_id=tag_id)


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
        for aid in selected_ids:
            raw = request.POST.get(f"tag_{aid}", "").strip()
            if raw.isdigit():
                tag_by_asset_id[aid] = int(raw)

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
        if selected_ids:
            valid_ids = list(_kit_eligible_assets_qs(kit).filter(id__in=selected_ids).values_list("id", flat=True))
            _apply_kit_tag_selection(kit, valid_ids, tag_by_asset_id)

        return redirect("/kits/")

    return render(request, "inventory/kit_form.html", {
        "assets": assets, "assets_json": assets_json, "tags_json": tags_json,
        "selected_ids": [], "active_nav": "kits",
    })


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
        for aid in selected_ids:
            raw = request.POST.get(f"tag_{aid}", "").strip()
            if raw.isdigit():
                tag_by_asset_id[aid] = int(raw)

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

        kit.name = name
        kit.notes = notes
        kit.save()

        valid_ids = list(_kit_eligible_assets_qs(kit).filter(id__in=selected_ids).values_list("id", flat=True))
        _apply_kit_tag_selection(kit, valid_ids, tag_by_asset_id)

        return redirect("/kits/")

    selected_tags_json = {
        kat.asset_id: kat.tag_id for kat in kit.kit_asset_tags.all() if kat.tag_id
    }
    return render(request, "inventory/kit_form.html", {
        "kit": kit,
        "assets": assets,
        "assets_json": assets_json,
        "tags_json": tags_json,
        "selected_ids": list(kit.assets.values_list("id", flat=True)),
        "selected_tags_json": selected_tags_json,
        "name": kit.name,
        "notes": kit.notes,
        "active_nav": "kits",
        "pdf_items": get_kit_checklist_rows(kit),
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
        if case_val or checked_val:
            item_overrides[asset_id] = {"case": case_val, "checked": checked_val}

    pdf_bytes = build_kit_checklist_pdf(kit, meta, item_overrides)
    filename = f"{kit.name} - Kit Checklist.pdf".replace("/", "-")
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


