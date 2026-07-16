import datetime

from django.contrib.auth.decorators import login_required
from django.db import models
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ..models import Asset, StaffMember, Tag

CONTAINER_KIND_META = {
    Asset.AssetType.ENGINE: {
        "label": "Engine", "label_plural": "Engines", "active_nav": "engines",
        "list_url": "/engines/", "new_url": "/engines/new/",
        "edit_url": lambda pk: f"/engines/{pk}/edit/",
        "delete_url": lambda pk: f"/engines/{pk}/delete/",
    },
    Asset.AssetType.IO_DEVICE: {
        "label": "I/O Device", "label_plural": "I/O Devices", "active_nav": "engines",
        "list_url": "/engines/?io=1", "new_url": "/admin/inventory/asset/add/",
        "edit_url": lambda pk: f"/io-devices/{pk}/edit/",
        "delete_url": lambda pk: f"/io-devices/{pk}/delete/",
    },
}


@login_required
def asset_list_view(request):
    asset_type = request.GET.get("type", "")
    status = request.GET.get("status", "")
    show_archived = request.GET.get("archived") == "1"
    query = request.GET.get("q", "").strip()

    assets = Asset.objects.select_related("parent_engine", "last_updated_by").prefetch_related("kits")

    if not show_archived:
        assets = assets.filter(archived=False)
    if asset_type:
        assets = assets.filter(asset_type=asset_type)
    if status:
        assets = assets.filter(status=status)
    if query:
        assets = assets.filter(
            Q(asset_id__icontains=query)
            | Q(make_model__icontains=query)
            | Q(serial__icontains=query)
            | Q(notes__icontains=query)
        )

    type_order = {value: i for i, (value, _label) in enumerate(Asset.AssetType.choices)}
    type_rank = models.Case(
        *[models.When(asset_type=value, then=rank) for value, rank in type_order.items()],
        output_field=models.IntegerField(),
    )
    assets = list(assets.annotate(_type_rank=type_rank).order_by("_type_rank", "asset_id"))

    type_counts = dict(
        Asset.objects.filter(archived=False)
        .values_list("asset_type")
        .annotate(c=Count("id"))
        .values_list("asset_type", "c")
    )
    total_active = Asset.objects.filter(archived=False).count()

    context = {
        "assets": assets,
        "asset_types": [c for c in Asset.AssetType.choices if c[0] != Asset.AssetType.SONNET],
        "statuses": Asset.Status.choices,
        "type_counts": type_counts,
        "total_active": total_active,
        "selected_type": asset_type,
        "selected_status": status,
        "show_archived": show_archived,
        "query": query,
        "active_nav": "assets",
    }
    return render(request, "inventory/asset_list.html", context)


def _container_form_context(kind, container=None):
    """Shared context builder for the Engine / Sonnet Box create/edit form."""
    # An Engine can contain anything except another Engine (including Sonnet
    # Boxes). A Sonnet Box can't contain another container (Engine or
    # Sonnet Box) - only components/standalone gear.
    excluded_types = [Asset.AssetType.ENGINE] if kind == Asset.AssetType.ENGINE else list(Asset.CONTAINER_TYPES)

    components_qs = Asset.objects.filter(
        archived=False
    ).exclude(
        asset_type__in=excluded_types
    ).filter(
        Q(parent_engine__isnull=True) | Q(parent_engine=container)
    ).order_by("asset_type", "asset_id")

    components = list(components_qs)
    components_json = [
        {
            "id": a.id,
            "assetId": a.asset_id,
            "makeModel": a.make_model,
            "type": a.get_asset_type_display(),
            "status": a.status.lower(),
            "statusDisplay": a.get_status_display(),
            "isContainer": a.asset_type in Asset.NESTABLE_CONTAINER_TYPES,
        }
        for a in components
    ]

    # Sonnet Boxes and I/O Devices are themselves containers: a GPU (etc.)
    # gets nested INTO one, and it then nests into the Engine. Build a
    # second, separate pool of "nestable into a sub-container" assets
    # (plain components only - never another Engine/Sonnet/I-O device)
    # covering anything unassigned or already sitting inside one of the
    # sub-containers available above, so picking one reveals what's
    # already inside it.
    nestable_json = []
    if kind == Asset.AssetType.ENGINE:
        sonnet_pool_ids = [a.id for a in components if a.asset_type in Asset.NESTABLE_CONTAINER_TYPES]
        nestable_qs = Asset.objects.filter(
            archived=False
        ).exclude(
            asset_type__in=Asset.CONTAINER_TYPES
        ).filter(
            Q(parent_engine__isnull=True) | Q(parent_engine_id__in=sonnet_pool_ids)
        ).order_by("asset_type", "asset_id")
        nestable_json = [
            {
                "id": a.id,
                "assetId": a.asset_id,
                "makeModel": a.make_model,
                "type": a.get_asset_type_display(),
                "status": a.status.lower(),
                "statusDisplay": a.get_status_display(),
                "parentSonnetId": a.parent_engine_id if a.parent_engine_id in sonnet_pool_ids else None,
            }
            for a in nestable_qs
        ]

    meta = CONTAINER_KIND_META[kind]
    return {
        "components": components,
        "components_json": components_json,
        "nestable_json": nestable_json,
        "staff_members": list(StaffMember.objects.filter(active=True).order_by("name")),
        "statuses": Asset.Status.choices,
        "active_nav": meta["active_nav"],
        "kind": kind,
        "kind_label": meta["label"],
        "kind_label_plural": meta["label_plural"],
        "list_url": meta["list_url"],
        "delete_url": meta["delete_url"](container.id) if container else None,
    }


def _apply_component_selection(container, selected_ids, kind=None):
    """Nest/un-nest components against an Engine or Sonnet Box, mirroring the admin form's save()."""
    kind = kind or container.asset_type
    excluded_types = [Asset.AssetType.ENGINE] if kind == Asset.AssetType.ENGINE else list(Asset.CONTAINER_TYPES)
    valid_components = Asset.objects.filter(
        id__in=selected_ids, archived=False
    ).exclude(asset_type__in=excluded_types)
    selected = set(valid_components)
    currently_nested = set(container.nested_assets.all())
    for asset in selected - currently_nested:
        asset.parent_engine = container
        asset.save(update_fields=["parent_engine"])
    for asset in currently_nested - selected:
        asset.parent_engine = None
        asset.save(update_fields=["parent_engine"])


def _apply_sonnet_children(selected_top_level_ids, raw_pairs):
    """raw_pairs is a list of 'containerId:childId' strings from the form.
    For every Sonnet Box / I-O Device among the top-level selection, nest/
    un-nest its children to match what was submitted for it."""
    by_sonnet = {}
    for pair in raw_pairs:
        if ":" not in pair:
            continue
        sid, cid = pair.split(":", 1)
        if sid.isdigit() and cid.isdigit():
            by_sonnet.setdefault(int(sid), set()).add(int(cid))

    sonnets = Asset.objects.filter(
        id__in=selected_top_level_ids, asset_type__in=Asset.NESTABLE_CONTAINER_TYPES
    )
    for sonnet in sonnets:
        wanted_ids = by_sonnet.get(sonnet.id, set())
        valid_children = set(
            Asset.objects.filter(
                id__in=wanted_ids, archived=False
            ).exclude(asset_type__in=Asset.CONTAINER_TYPES)
        )
        currently_nested = set(sonnet.nested_assets.all())
        for asset in valid_children - currently_nested:
            asset.parent_engine = sonnet
            asset.save(update_fields=["parent_engine"])
        for asset in currently_nested - valid_children:
            asset.parent_engine = None
            asset.save(update_fields=["parent_engine"])


def _container_create_view(request, kind):
    meta = CONTAINER_KIND_META[kind]
    if request.method == "POST":
        asset_id = request.POST.get("asset_id", "").strip()
        make_model = request.POST.get("make_model", "").strip()
        serial = request.POST.get("serial", "").strip()
        qty = request.POST.get("qty", "1").strip()
        status = request.POST.get("status", Asset.Status.AVAILABLE)
        archived = request.POST.get("archived") == "on"
        notes = request.POST.get("notes", "").strip()
        last_updated_by_id = request.POST.get("last_updated_by", "").strip()
        last_updated_date = request.POST.get("last_updated_date", "").strip()
        last_updated_notes = request.POST.get("last_updated_notes", "").strip()
        component_ids = request.POST.getlist("components")
        selected_ids = [int(i) for i in component_ids if i.isdigit()]

        form_ctx = _container_form_context(kind)
        form_ctx.update({
            "asset_id": asset_id, "make_model": make_model, "serial": serial,
            "qty": qty, "status": status, "archived": archived, "notes": notes,
            "last_updated_by_id": last_updated_by_id, "last_updated_date": last_updated_date, "last_updated_notes": last_updated_notes,
            "selected_ids": selected_ids,
            "sonnet_child_pairs": request.POST.getlist("sonnet_child"),
        })

        if not asset_id:
            form_ctx["error"] = f"{meta['label']} ID is required."
            return render(request, "inventory/engine_form.html", form_ctx)

        if not last_updated_date:
            form_ctx["error"] = "Last updated date is required before you can save."
            return render(request, "inventory/engine_form.html", form_ctx)

        if Asset.objects.filter(asset_id=asset_id).exists():
            form_ctx["error"] = f'An asset with ID "{asset_id}" already exists.'
            return render(request, "inventory/engine_form.html", form_ctx)

        if status not in Asset.Status.values:
            status = Asset.Status.AVAILABLE

        try:
            qty_val = max(1, int(qty))
        except ValueError:
            qty_val = 1

        last_updated_by = None
        if last_updated_by_id.isdigit():
            last_updated_by = StaffMember.objects.filter(pk=last_updated_by_id).first()

        try:
            parsed_date = datetime.date.fromisoformat(last_updated_date)
        except ValueError:
            form_ctx["error"] = "Last updated date is invalid."
            return render(request, "inventory/engine_form.html", form_ctx)

        container = Asset.objects.create(
            asset_id=asset_id, asset_type=kind,
            make_model=make_model, serial=serial, qty=qty_val, status=status,
            archived=archived, notes=notes,
            last_updated_by=last_updated_by, last_updated_date=parsed_date, last_updated_notes=last_updated_notes,
        )
        _apply_component_selection(container, selected_ids)
        if kind == Asset.AssetType.ENGINE:
            _apply_sonnet_children(selected_ids, request.POST.getlist("sonnet_child"))

        return redirect(meta["list_url"])

    form_ctx = _container_form_context(kind)
    form_ctx.update({
        "selected_ids": [], "qty": "1", "status": Asset.Status.AVAILABLE,
        "sonnet_child_pairs": [],
        "last_updated_date": datetime.date.today().isoformat(),
    })
    return render(request, "inventory/engine_form.html", form_ctx)


def _container_edit_view(request, kind, container_id):
    meta = CONTAINER_KIND_META[kind]
    container = get_object_or_404(Asset, pk=container_id, asset_type=kind)

    if request.method == "POST":
        asset_id = request.POST.get("asset_id", "").strip()
        make_model = request.POST.get("make_model", "").strip()
        serial = request.POST.get("serial", "").strip()
        qty = request.POST.get("qty", "1").strip()
        status = request.POST.get("status", Asset.Status.AVAILABLE)
        archived = request.POST.get("archived") == "on"
        notes = request.POST.get("notes", "").strip()
        last_updated_by_id = request.POST.get("last_updated_by", "").strip()
        last_updated_date = request.POST.get("last_updated_date", "").strip()
        last_updated_notes = request.POST.get("last_updated_notes", "").strip()
        component_ids = request.POST.getlist("components")
        selected_ids = [int(i) for i in component_ids if i.isdigit()]
        kit_tag_id = request.POST.get("kit_tag", "").strip()

        form_ctx = _container_form_context(kind, container=container)
        form_ctx.update({
            "engine": container,
            "asset_id": asset_id, "make_model": make_model, "serial": serial,
            "qty": qty, "status": status, "archived": archived, "notes": notes,
            "last_updated_by_id": last_updated_by_id, "last_updated_date": last_updated_date, "last_updated_notes": last_updated_notes,
            "selected_ids": selected_ids,
            "sonnet_child_pairs": request.POST.getlist("sonnet_child"),
        })

        if not asset_id:
            form_ctx["error"] = f"{meta['label']} ID is required."
            return render(request, "inventory/engine_form.html", form_ctx)

        if not last_updated_date:
            form_ctx["error"] = "Last updated date is required before you can save."
            return render(request, "inventory/engine_form.html", form_ctx)

        if Asset.objects.filter(asset_id=asset_id).exclude(pk=container.pk).exists():
            form_ctx["error"] = f'An asset with ID "{asset_id}" already exists.'
            return render(request, "inventory/engine_form.html", form_ctx)

        if status not in Asset.Status.values:
            status = Asset.Status.AVAILABLE

        try:
            qty_val = max(1, int(qty))
        except ValueError:
            qty_val = 1

        last_updated_by = None
        if last_updated_by_id.isdigit():
            last_updated_by = StaffMember.objects.filter(pk=last_updated_by_id).first()

        try:
            parsed_date = datetime.date.fromisoformat(last_updated_date)
        except ValueError:
            form_ctx["error"] = "Last updated date is invalid."
            return render(request, "inventory/engine_form.html", form_ctx)

        container.asset_id = asset_id
        container.make_model = make_model
        container.serial = serial
        container.qty = qty_val
        container.status = status
        container.archived = archived
        container.notes = notes
        container.last_updated_by = last_updated_by
        container.last_updated_date = parsed_date
        container.last_updated_notes = last_updated_notes
        container.save()

        _apply_component_selection(container, selected_ids)
        if kind == Asset.AssetType.ENGINE:
            _apply_sonnet_children(selected_ids, request.POST.getlist("sonnet_child"))

        kit_membership = container.kit_asset_tags.first()
        if kit_membership:
            new_tag_id = int(kit_tag_id) if kit_tag_id.isdigit() else None
            if new_tag_id != kit_membership.tag_id:
                kit_membership.tag_id = new_tag_id
                kit_membership.save(update_fields=["tag"])

        return redirect(meta["list_url"])

    form_ctx = _container_form_context(kind, container=container)
    form_ctx.update({
        "engine": container,
        "asset_id": container.asset_id,
        "make_model": container.make_model,
        "serial": container.serial,
        "qty": str(container.qty),
        "status": container.status,
        "archived": container.archived,
        "notes": container.notes,
        "last_updated_by_id": str(container.last_updated_by_id) if container.last_updated_by_id else "",
        "last_updated_date": container.last_updated_date.isoformat() if container.last_updated_date else "",
        "last_updated_notes": container.last_updated_notes,
        "selected_ids": list(container.nested_assets.values_list("id", flat=True)),
        "sonnet_child_pairs": [],
        "tags": list(Tag.objects.all()),
        "kit_membership": container.kit_asset_tags.select_related("kit", "tag").first(),
    })
    return render(request, "inventory/engine_form.html", form_ctx)


def _container_delete_view(request, kind, container_id):
    meta = CONTAINER_KIND_META[kind]
    container = get_object_or_404(Asset, pk=container_id, asset_type=kind)
    container.delete()
    return redirect(meta["list_url"])


def _container_list_view(request, kind):
    meta = CONTAINER_KIND_META[kind]
    make_model = request.GET.get("make_model", "")
    show_archived = request.GET.get("archived") == "1"
    query = request.GET.get("q", "").strip()
    show_io = kind == Asset.AssetType.ENGINE and request.GET.get("io") == "1"
    effective_type = Asset.AssetType.IO_DEVICE if show_io else kind

    items = Asset.objects.filter(
        asset_type=effective_type
    ).select_related("last_updated_by").prefetch_related("nested_assets", "kits")

    if not show_archived:
        items = items.filter(archived=False)
    if make_model and not show_io:
        items = items.filter(make_model=make_model)
    if query:
        items = items.filter(
            Q(asset_id__icontains=query) | Q(make_model__icontains=query) | Q(serial__icontains=query)
        )

    items = list(items.order_by("make_model", "asset_id"))

    models_qs = (
        Asset.objects.filter(asset_type=kind, archived=False)
        .exclude(make_model="")
        .order_by("make_model")
        .values_list("make_model", flat=True)
        .distinct()
    )

    context = {
        "engines": items,
        "engine_models": list(models_qs),
        "selected_make_model": make_model,
        "show_archived": show_archived,
        "show_io": show_io,
        "query": query,
        "active_nav": meta["active_nav"],
        "kind": kind,
        "kind_label": "I/O Device" if show_io else meta["label"],
        "kind_label_plural": "I/O Devices" if show_io else meta["label_plural"],
        "all_tab_label": meta["label_plural"],
        "new_url": "/admin/inventory/asset/add/" if show_io else meta["new_url"],
    }
    return render(request, "inventory/engine_list.html", context)


@login_required
def engine_create_view(request):
    return _container_create_view(request, Asset.AssetType.ENGINE)


@login_required
def engine_edit_view(request, engine_id):
    return _container_edit_view(request, Asset.AssetType.ENGINE, engine_id)


@login_required
@require_POST
def engine_delete_view(request, engine_id):
    return _container_delete_view(request, Asset.AssetType.ENGINE, engine_id)


@login_required
def engine_list_view(request):
    return _container_list_view(request, Asset.AssetType.ENGINE)


@login_required
def io_device_edit_view(request, io_id):
    return _container_edit_view(request, Asset.AssetType.IO_DEVICE, io_id)


@login_required
@require_POST
def io_device_delete_view(request, io_id):
    return _container_delete_view(request, Asset.AssetType.IO_DEVICE, io_id)


