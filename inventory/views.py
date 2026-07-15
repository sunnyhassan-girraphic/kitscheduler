import csv
import datetime
import io
import re
import zipfile

from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Count, Q
from django.http import HttpResponseRedirect, JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import (
    Asset, AssetBooking, CategoryColor, Job, Kit, KitAssetTag, KitBooking,
    LicenseFunctionality, StaffBooking, StaffMember, Tag, Ticket, TicketHistory,
    Vehicle, VanUsageLog, VanMaintenanceLog, VanChecklist, VAN_CHECKLIST_ITEMS,
)
from .pdf_utils import build_kit_checklist_pdf, get_kit_checklist_rows

VIEW_DAYS = 14
STEP_DAYS = 7


def _is_weekend(d):
    return d.weekday() >= 5


def _date_range(anchor, days=VIEW_DAYS):
    return [anchor + datetime.timedelta(days=i) for i in range(days)]


def _month_range(anchor):
    first = anchor.replace(day=1)
    if first.month == 12:
        next_month = first.replace(year=first.year + 1, month=1)
    else:
        next_month = first.replace(month=first.month + 1)
    last = next_month - datetime.timedelta(days=1)
    return [first + datetime.timedelta(days=i) for i in range((last - first).days + 1)]


def _parse_anchor(request, range_mode="week"):
    raw = request.GET.get("start")
    try:
        anchor = datetime.date.fromisoformat(raw) if raw else datetime.date.today()
    except ValueError:
        anchor = datetime.date.today()
    if range_mode == "month":
        return anchor.replace(day=1)
    monday = anchor - datetime.timedelta(days=anchor.weekday())
    return monday


def _build_rows(items, bookings_by_item_id, days, overlap_ok=False):
    day_index = {d: i for i, d in enumerate(days)}
    rows = []
    for item in items:
        item_bookings = bookings_by_item_id.get(item.id, [])
        cells = []
        for d in days:
            hits = [b for b in item_bookings if b.start_date <= d <= b.end_date]
            cells.append({
                "date": d,
                "booking": hits[0] if hits else None,
                "is_start": bool(hits and hits[0].start_date == d),
                "is_overlap": overlap_ok and len(hits) > 1,
                "is_weekend": _is_weekend(d),
            })

        visible = [b for b in item_bookings if b.start_date <= days[-1] and b.end_date >= days[0]]
        visible.sort(key=lambda b: (b.start_date, b.end_date))

        lane_ends = []
        spans = []
        for b in visible:
            clipped_start = max(b.start_date, days[0])
            clipped_end = min(b.end_date, days[-1])
            col_start = day_index[clipped_start]
            col_end = day_index[clipped_end]

            lane = None
            for i, end_col in enumerate(lane_ends):
                if end_col < col_start:
                    lane = i
                    lane_ends[i] = col_end
                    break
            if lane is None:
                lane = len(lane_ends)
                lane_ends.append(col_end)

            spans.append({
                "booking": b,
                "color": b.job.resolve_color(),
                "grid_col_start": col_start + 1,
                "grid_col_end": col_end + 2,
                "grid_row": lane + 1,
                "continues_before": b.start_date < days[0],
                "continues_after": b.end_date > days[-1],
            })

        rows.append({
            "item": item,
            "cells": cells,
            "spans": spans,
            "lane_count": max(len(lane_ends), 1),
        })
    return rows


def _week_availability(items, bookings_by_item_id, week_days):
    fully_free = 0
    partially_free = 0
    for item in items:
        item_bookings = bookings_by_item_id.get(item.id, [])
        booked_days = sum(
            1 for d in week_days
            if any(b.start_date <= d <= b.end_date for b in item_bookings)
        )
        if booked_days == 0:
            fully_free += 1
        elif booked_days < len(week_days):
            partially_free += 1
    return fully_free, partially_free


@login_required
def dashboard_view(request):
    today = datetime.date.today()

    raw = request.GET.get("date")
    try:
        agenda_date = datetime.date.fromisoformat(raw) if raw else today
    except ValueError:
        agenda_date = today

    week_start = today - datetime.timedelta(days=today.weekday())
    week_days = _date_range(week_start, days=7)

    staff = list(StaffMember.objects.filter(active=True).order_by("name"))
    agenda_bookings = list(
        StaffBooking.objects.select_related("job", "staff_member").filter(
            start_date__lte=agenda_date, end_date__gte=agenda_date,
            staff_member__active=True,
        )
    )
    bookings_by_staff_id = {}
    for b in agenda_bookings:
        bookings_by_staff_id.setdefault(b.staff_member_id, []).append(b)
    agenda_rows = [
        {"person": p, "bookings": sorted(bookings_by_staff_id.get(p.id, []), key=lambda b: b.job.name)}
        for p in staff
    ]

    kits = list(Kit.objects.all())
    kit_bookings = list(KitBooking.objects.filter(start_date__lte=week_days[-1], end_date__gte=week_days[0]))
    bookings_by_kit = {}
    for b in kit_bookings:
        bookings_by_kit.setdefault(b.kit_id, []).append(b)

    staff_bookings_week = list(StaffBooking.objects.filter(start_date__lte=week_days[-1], end_date__gte=week_days[0]))
    bookings_by_staff = {}
    for b in staff_bookings_week:
        bookings_by_staff.setdefault(b.staff_member_id, []).append(b)

    kits_fully_free, kits_partially_free = _week_availability(kits, bookings_by_kit, week_days)
    staff_fully_free, staff_partially_free = _week_availability(staff, bookings_by_staff, week_days)

    licenses = list(Asset.objects.filter(asset_type=Asset.AssetType.LICENSE, archived=False))
    license_bookings_week = list(AssetBooking.objects.filter(
        asset__asset_type=Asset.AssetType.LICENSE,
        start_date__lte=week_days[-1], end_date__gte=week_days[0],
    ))
    bookings_by_license = {}
    for b in license_bookings_week:
        bookings_by_license.setdefault(b.asset_id, []).append(b)
    licenses_fully_free, licenses_partially_free = _week_availability(licenses, bookings_by_license, week_days)

    jobs_this_week = Job.objects.filter(start_date__lte=week_days[-1], end_date__gte=week_days[0]).count()

    open_tickets = Ticket.objects.filter(status__in=[Ticket.Status.OPEN, Ticket.Status.IN_PROGRESS])
    open_ticket_count = open_tickets.count()
    urgent_ticket_count = open_tickets.filter(priority__in=[Ticket.Priority.HIGH, Ticket.Priority.URGENT]).count()

    upcoming_jobs = list(Job.objects.filter(start_date__gte=today).order_by("start_date")[:8])

    attention_assets = list(
        Asset.objects.filter(
            archived=False, status__in=[Asset.Status.NEEDS_REPAIR, Asset.Status.MISSING]
        ).order_by("status", "asset_id")[:12]
    )
    attention_count = Asset.objects.filter(
        archived=False, status__in=[Asset.Status.NEEDS_REPAIR, Asset.Status.MISSING]
    ).count()

    context = {
        "today": today,
        "agenda_date": agenda_date,
        "is_today": agenda_date == today,
        "prev_date": agenda_date - datetime.timedelta(days=1),
        "next_date": agenda_date + datetime.timedelta(days=1),
        "agenda_rows": agenda_rows,
        "total_staff": len(staff),
        "total_kits": len(kits),
        "kits_fully_free": kits_fully_free,
        "kits_partially_free": kits_partially_free,
        "staff_fully_free": staff_fully_free,
        "staff_partially_free": staff_partially_free,
        "total_licenses": len(licenses),
        "licenses_fully_free": licenses_fully_free,
        "licenses_partially_free": licenses_partially_free,
        "jobs_this_week": jobs_this_week,
        "open_ticket_count": open_ticket_count,
        "urgent_ticket_count": urgent_ticket_count,
        "upcoming_jobs": upcoming_jobs,
        "job_categories": Job.Category.choices,
        "attention_assets": attention_assets,
        "attention_count": attention_count,
        "active_nav": "dashboard",
    }
    return render(request, "inventory/dashboard.html", context)


@login_required
def timeline_view(request):
    range_mode = request.GET.get("range", "week")
    if range_mode not in ("week", "month"):
        range_mode = "week"

    show_kits = request.GET.get("kits", "1") != "0"
    show_staff = request.GET.get("staff", "1") != "0"
    show_licenses = request.GET.get("licenses", "0") != "0"

    anchor = _parse_anchor(request, range_mode=range_mode)
    if range_mode == "month":
        days = _month_range(anchor)
    else:
        days = _date_range(anchor)
    week_days = days[:7]

    kits = list(Kit.objects.prefetch_related("assets", "bookings__job").order_by("name"))
    kit_bookings = list(
        KitBooking.objects.select_related("job", "kit").filter(
            start_date__lte=days[-1], end_date__gte=days[0]
        )
    )
    bookings_by_kit = {}
    for b in kit_bookings:
        bookings_by_kit.setdefault(b.kit_id, []).append(b)
    kit_rows = _build_rows(kits, bookings_by_kit, days)

    staff = list(StaffMember.objects.filter(active=True).order_by("name"))
    staff_bookings = list(
        StaffBooking.objects.select_related("job", "staff_member").filter(
            start_date__lte=days[-1], end_date__gte=days[0]
        )
    )
    bookings_by_staff = {}
    for b in staff_bookings:
        bookings_by_staff.setdefault(b.staff_member_id, []).append(b)
    staff_rows = _build_rows(staff, bookings_by_staff, days, overlap_ok=True)

    licenses = list(Asset.objects.filter(
        asset_type=Asset.AssetType.LICENSE, archived=False
    ).prefetch_related("functionalities").order_by("asset_id"))
    for lic in licenses:
        lic.func_tags = [f.name for f in lic.functionalities.all()]

    license_bookings = list(
        AssetBooking.objects.select_related("job", "asset").prefetch_related("asset__kits").filter(
            asset__asset_type=Asset.AssetType.LICENSE,
            start_date__lte=days[-1], end_date__gte=days[0]
        )
    )
    bookings_by_license = {}
    for b in license_bookings:
        bookings_by_license.setdefault(b.asset_id, []).append(b)
    license_rows = _build_rows(licenses, bookings_by_license, days)

    kits_fully_free, kits_partially_free = _week_availability(kits, bookings_by_kit, week_days)
    staff_fully_free, staff_partially_free = _week_availability(staff, bookings_by_staff, week_days)

    jobs = list(Job.objects.order_by("-start_date")[:200])

    if range_mode == "month":
        prev_anchor = (anchor.replace(day=1) - datetime.timedelta(days=1)).replace(day=1)
        next_month = anchor.replace(day=1)
        if next_month.month == 12:
            next_anchor = next_month.replace(year=next_month.year + 1, month=1)
        else:
            next_anchor = next_month.replace(month=next_month.month + 1)
    else:
        prev_anchor = anchor - datetime.timedelta(days=STEP_DAYS)
        next_anchor = anchor + datetime.timedelta(days=STEP_DAYS)

    context = {
        "range_mode": range_mode,
        "show_kits": show_kits,
        "show_staff": show_staff,
        "show_licenses": show_licenses,
        "days": days,
        "kit_rows": kit_rows,
        "staff_rows": staff_rows,
        "license_rows": license_rows,
        "jobs": jobs,
        "job_categories": Job.Category.choices,
        "today": datetime.date.today(),
        "anchor": anchor,
        "prev_anchor": prev_anchor,
        "next_anchor": next_anchor,
        "total_kits": len(kits),
        "total_staff": len(staff),
        "total_licenses": len(licenses),
        "kits_fully_free": kits_fully_free,
        "kits_partially_free": kits_partially_free,
        "staff_fully_free": staff_fully_free,
        "staff_partially_free": staff_partially_free,
        "active_nav": "timeline",
    }
    return render(request, "inventory/timeline.html", context)


@login_required
def calendar_view(request):
    qs = request.GET.urlencode()
    return HttpResponseRedirect(f"/timeline/{'?' + qs if qs else ''}")


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
        "asset_types": Asset.AssetType.choices,
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
            m.nested_assets.count() for m in members if m.asset_type in Asset.CONTAINER_TYPES
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
    not a Sonnet Box already nested inside an Engine (it travels with that
    Engine automatically), and not already claimed by a DIFFERENT kit
    (directly, or nested inside a container - Engine or Sonnet Box - that's
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
    # Sonnet Boxes nested inside one of those Engines are containers too -
    # their own nested components must be excluded from the picker as well.
    other_kit_container_ids |= set(
        Asset.objects.filter(
            parent_engine_id__in=other_kit_container_ids, asset_type=Asset.AssetType.SONNET
        ).values_list("id", flat=True)
    )
    current_kit_asset_ids = set(current_kit.assets.values_list("id", flat=True)) if current_kit else set()

    return Asset.objects.filter(
        archived=False
    ).exclude(
        asset_type=Asset.AssetType.COMPONENT
    ).exclude(
        Q(asset_type=Asset.AssetType.SONNET) & Q(parent_engine__isnull=False)
    ).filter(
        Q(id__in=current_kit_asset_ids)
        | (~Q(id__in=other_kit_asset_ids) & ~Q(parent_engine_id__in=other_kit_container_ids))
    )


def _kit_picker_assets(current_kit=None):
    """Assets eligible to be direct kit members, with nested-component info for engines/Sonnet Boxes."""
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


CONTAINER_KIND_META = {
    Asset.AssetType.ENGINE: {
        "label": "Engine", "label_plural": "Engines", "active_nav": "engines",
        "list_url": "/engines/", "new_url": "/engines/new/",
        "edit_url": lambda pk: f"/engines/{pk}/edit/",
        "delete_url": lambda pk: f"/engines/{pk}/delete/",
    },
    Asset.AssetType.SONNET: {
        "label": "Sonnet Box", "label_plural": "Sonnet Boxes", "active_nav": "sonnet_boxes",
        "list_url": "/sonnet-boxes/", "new_url": "/sonnet-boxes/new/",
        "edit_url": lambda pk: f"/sonnet-boxes/{pk}/edit/",
        "delete_url": lambda pk: f"/sonnet-boxes/{pk}/delete/",
    },
}


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
        }
        for a in components
    ]

    meta = CONTAINER_KIND_META[kind]
    return {
        "components": components,
        "components_json": components_json,
        "staff_members": list(StaffMember.objects.filter(active=True).order_by("name")),
        "statuses": Asset.Status.choices,
        "active_nav": meta["active_nav"],
        "kind": kind,
        "kind_label": meta["label"],
        "kind_label_plural": meta["label_plural"],
        "list_url": meta["list_url"],
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

        return redirect(meta["list_url"])

    form_ctx = _container_form_context(kind)
    form_ctx.update({
        "selected_ids": [], "qty": "1", "status": Asset.Status.AVAILABLE,
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

        form_ctx = _container_form_context(kind, container=container)
        form_ctx.update({
            "engine": container,
            "asset_id": asset_id, "make_model": make_model, "serial": serial,
            "qty": qty, "status": status, "archived": archived, "notes": notes,
            "last_updated_by_id": last_updated_by_id, "last_updated_date": last_updated_date, "last_updated_notes": last_updated_notes,
            "selected_ids": selected_ids,
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

    items = Asset.objects.filter(
        asset_type=kind
    ).select_related("last_updated_by").prefetch_related("nested_assets", "kits")

    if not show_archived:
        items = items.filter(archived=False)
    if make_model:
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
        "query": query,
        "active_nav": meta["active_nav"],
        "kind": kind,
        "kind_label": meta["label"],
        "kind_label_plural": meta["label_plural"],
        "new_url": meta["new_url"],
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
def sonnet_create_view(request):
    return _container_create_view(request, Asset.AssetType.SONNET)


@login_required
def sonnet_edit_view(request, sonnet_id):
    return _container_edit_view(request, Asset.AssetType.SONNET, sonnet_id)


@login_required
@require_POST
def sonnet_delete_view(request, sonnet_id):
    return _container_delete_view(request, Asset.AssetType.SONNET, sonnet_id)


@login_required
def sonnet_list_view(request):
    return _container_list_view(request, Asset.AssetType.SONNET)





@login_required
def license_list_view(request):
    show_archived = request.GET.get("archived") == "1"
    license_type = request.GET.get("type", "")
    func_id = request.GET.get("func", "")
    query = request.GET.get("q", "").strip()

    licenses = Asset.objects.filter(
        asset_type=Asset.AssetType.LICENSE
    ).select_related("last_updated_by").prefetch_related("kits", "functionalities")
    if not show_archived:
        licenses = licenses.filter(archived=False)
    if license_type:
        licenses = licenses.filter(license_type=license_type)
    if func_id.isdigit():
        licenses = licenses.filter(functionalities__id=int(func_id))
    if query:
        licenses = licenses.filter(Q(asset_id__icontains=query) | Q(notes__icontains=query))

    licenses = list(licenses.order_by("asset_id").distinct())
    for lic in licenses:
        lic.func_tags = list(lic.functionalities.all())

    context = {
        "licenses": licenses,
        "show_archived": show_archived,
        "total": len(licenses),
        "active_nav": "licenses",
        "license_types": Asset.LicenseType.choices,
        "selected_type": license_type,
        "functionality_options": list(LicenseFunctionality.objects.all()),
        "selected_func": func_id,
        "query": query,
    }
    return render(request, "inventory/license_list.html", context)


def _license_form_context(license_obj=None):
    return {
        "staff_members": list(StaffMember.objects.filter(active=True).order_by("name")),
        "statuses": Asset.Status.choices,
        "license_types": Asset.LicenseType.choices,
        "functionality_options": list(LicenseFunctionality.objects.all()),
        "active_nav": "licenses",
    }


@login_required
def license_create_view(request):
    if request.method == "POST":
        asset_id = request.POST.get("asset_id", "").strip()
        status = request.POST.get("status", Asset.Status.AVAILABLE)
        archived = request.POST.get("archived") == "on"
        notes = request.POST.get("notes", "").strip()
        license_type = request.POST.get("license_type", "").strip()
        func_ids = [int(i) for i in request.POST.getlist("functionalities") if i.isdigit()]
        duration_start = request.POST.get("license_duration_start", "").strip()
        duration_end = request.POST.get("license_duration_end", "").strip()
        last_updated_by_id = request.POST.get("last_updated_by", "").strip()
        last_updated_date = request.POST.get("last_updated_date", "").strip()
        last_updated_notes = request.POST.get("last_updated_notes", "").strip()

        form_ctx = _license_form_context()
        form_ctx.update({
            "asset_id": asset_id, "status": status, "archived": archived, "notes": notes,
            "license_type": license_type, "selected_func_ids": func_ids,
            "license_duration_start": duration_start, "license_duration_end": duration_end,
            "last_updated_by_id": last_updated_by_id, "last_updated_date": last_updated_date,
            "last_updated_notes": last_updated_notes,
        })

        if not asset_id:
            form_ctx["error"] = "License name/ID is required."
            return render(request, "inventory/license_form.html", form_ctx)

        if not last_updated_date:
            form_ctx["error"] = "Last updated date is required before you can save."
            return render(request, "inventory/license_form.html", form_ctx)

        if Asset.objects.filter(asset_id=asset_id).exists():
            form_ctx["error"] = f'An asset with ID "{asset_id}" already exists.'
            return render(request, "inventory/license_form.html", form_ctx)

        if status not in Asset.Status.values:
            status = Asset.Status.AVAILABLE
        if license_type not in Asset.LicenseType.values:
            license_type = ""

        last_updated_by = None
        if last_updated_by_id.isdigit():
            last_updated_by = StaffMember.objects.filter(pk=last_updated_by_id).first()

        try:
            parsed_date = datetime.date.fromisoformat(last_updated_date)
        except ValueError:
            form_ctx["error"] = "Last updated date is invalid."
            return render(request, "inventory/license_form.html", form_ctx)

        start_val = None
        if duration_start:
            try:
                start_val = datetime.date.fromisoformat(duration_start)
            except ValueError:
                start_val = None
        end_val = None
        if duration_end:
            try:
                end_val = datetime.date.fromisoformat(duration_end)
            except ValueError:
                end_val = None
        if start_val and end_val and start_val > end_val:
            form_ctx["error"] = "Duration end cannot be before duration start."
            return render(request, "inventory/license_form.html", form_ctx)

        lic = Asset.objects.create(
            asset_id=asset_id, asset_type=Asset.AssetType.LICENSE,
            status=status, archived=archived, notes=notes,
            license_type=license_type,
            license_duration_start=start_val, license_duration_end=end_val,
            last_updated_by=last_updated_by, last_updated_date=parsed_date,
            last_updated_notes=last_updated_notes,
        )
        lic.functionalities.set(LicenseFunctionality.objects.filter(id__in=func_ids))

        return redirect("/licenses/")

    form_ctx = _license_form_context()
    form_ctx.update({
        "status": Asset.Status.AVAILABLE, "selected_func_ids": [],
        "last_updated_date": datetime.date.today().isoformat(),
    })
    return render(request, "inventory/license_form.html", form_ctx)


@login_required
def license_edit_view(request, license_id):
    lic = get_object_or_404(Asset, pk=license_id, asset_type=Asset.AssetType.LICENSE)

    if request.method == "POST":
        asset_id = request.POST.get("asset_id", "").strip()
        status = request.POST.get("status", Asset.Status.AVAILABLE)
        archived = request.POST.get("archived") == "on"
        notes = request.POST.get("notes", "").strip()
        license_type = request.POST.get("license_type", "").strip()
        func_ids = [int(i) for i in request.POST.getlist("functionalities") if i.isdigit()]
        duration_start = request.POST.get("license_duration_start", "").strip()
        duration_end = request.POST.get("license_duration_end", "").strip()
        last_updated_by_id = request.POST.get("last_updated_by", "").strip()
        last_updated_date = request.POST.get("last_updated_date", "").strip()
        last_updated_notes = request.POST.get("last_updated_notes", "").strip()

        form_ctx = _license_form_context(lic)
        form_ctx.update({
            "license": lic,
            "asset_id": asset_id, "status": status, "archived": archived, "notes": notes,
            "license_type": license_type, "selected_func_ids": func_ids,
            "license_duration_start": duration_start, "license_duration_end": duration_end,
            "last_updated_by_id": last_updated_by_id, "last_updated_date": last_updated_date,
            "last_updated_notes": last_updated_notes,
        })

        if not asset_id:
            form_ctx["error"] = "License name/ID is required."
            return render(request, "inventory/license_form.html", form_ctx)

        if not last_updated_date:
            form_ctx["error"] = "Last updated date is required before you can save."
            return render(request, "inventory/license_form.html", form_ctx)

        if Asset.objects.filter(asset_id=asset_id).exclude(pk=lic.pk).exists():
            form_ctx["error"] = f'An asset with ID "{asset_id}" already exists.'
            return render(request, "inventory/license_form.html", form_ctx)

        if status not in Asset.Status.values:
            status = Asset.Status.AVAILABLE
        if license_type not in Asset.LicenseType.values:
            license_type = ""

        last_updated_by = None
        if last_updated_by_id.isdigit():
            last_updated_by = StaffMember.objects.filter(pk=last_updated_by_id).first()

        try:
            parsed_date = datetime.date.fromisoformat(last_updated_date)
        except ValueError:
            form_ctx["error"] = "Last updated date is invalid."
            return render(request, "inventory/license_form.html", form_ctx)

        start_val = None
        if duration_start:
            try:
                start_val = datetime.date.fromisoformat(duration_start)
            except ValueError:
                start_val = None
        end_val = None
        if duration_end:
            try:
                end_val = datetime.date.fromisoformat(duration_end)
            except ValueError:
                end_val = None
        if start_val and end_val and start_val > end_val:
            form_ctx["error"] = "Duration end cannot be before duration start."
            return render(request, "inventory/license_form.html", form_ctx)

        lic.asset_id = asset_id
        lic.status = status
        lic.archived = archived
        lic.notes = notes
        lic.license_type = license_type
        lic.license_duration_start = start_val
        lic.license_duration_end = end_val
        lic.last_updated_by = last_updated_by
        lic.last_updated_date = parsed_date
        lic.last_updated_notes = last_updated_notes
        lic.save()
        lic.functionalities.set(LicenseFunctionality.objects.filter(id__in=func_ids))

        return redirect("/licenses/")

    form_ctx = _license_form_context(lic)
    form_ctx.update({
        "license": lic,
        "asset_id": lic.asset_id,
        "status": lic.status,
        "archived": lic.archived,
        "notes": lic.notes,
        "license_type": lic.license_type,
        "selected_func_ids": list(lic.functionalities.values_list("id", flat=True)),
        "license_duration_start": lic.license_duration_start.isoformat() if lic.license_duration_start else "",
        "license_duration_end": lic.license_duration_end.isoformat() if lic.license_duration_end else "",
        "last_updated_by_id": str(lic.last_updated_by_id) if lic.last_updated_by_id else "",
        "last_updated_date": lic.last_updated_date.isoformat() if lic.last_updated_date else "",
        "last_updated_notes": lic.last_updated_notes,
    })
    return render(request, "inventory/license_form.html", form_ctx)


@login_required
@require_POST
def license_delete_view(request, license_id):
    lic = get_object_or_404(Asset, pk=license_id, asset_type=Asset.AssetType.LICENSE)
    lic.delete()
    return redirect("/licenses/")


@login_required
def settings_view(request):
    categories = Job.Category.choices
    colors = {cc.category: cc.color for cc in CategoryColor.objects.all()}

    if request.method == "POST":
        for value, _ in categories:
            color = request.POST.get(f"color_{value}", "").strip()
            if color and re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
                CategoryColor.objects.update_or_create(
                    category=value, defaults={"color": color}
                )
        return redirect("/settings/")

    return render(request, "inventory/settings.html", {
        "categories": categories,
        "colors": colors,
        "tags": Tag.objects.all(),
        "functionalities": LicenseFunctionality.objects.all(),
        "active_nav": "settings",
    })


@login_required
@require_POST
def settings_tag_add(request):
    name = request.POST.get("name", "").strip()
    color = request.POST.get("color", "").strip()
    if name and not Tag.objects.filter(name__iexact=name).exists():
        Tag.objects.create(name=name, color=color if re.fullmatch(r"#[0-9A-Fa-f]{6}", color or "") else "")
    return redirect("/settings/")


@login_required
@require_POST
def settings_tag_delete(request, tag_id):
    Tag.objects.filter(pk=tag_id).delete()
    return redirect("/settings/")


@login_required
@require_POST
def settings_functionality_add(request):
    name = request.POST.get("name", "").strip()
    if name and not LicenseFunctionality.objects.filter(name__iexact=name).exists():
        LicenseFunctionality.objects.create(name=name)
    return redirect("/settings/")


@login_required
@require_POST
def settings_functionality_delete(request, func_id):
    LicenseFunctionality.objects.filter(pk=func_id).delete()
    return redirect("/settings/")


@login_required
def export_csv_view(request):
    today_str = datetime.date.today().isoformat()
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        assets_io = io.StringIO()
        writer = csv.writer(assets_io)
        writer.writerow(["asset_id", "type", "make_model", "serial", "qty", "status",
                         "archived", "license_type", "license_functionalities",
                         "license_duration_start", "license_duration_end", "parent_engine", "notes"])
        for a in Asset.objects.select_related("parent_engine").prefetch_related("functionalities").order_by("asset_type", "asset_id"):
            writer.writerow([
                a.asset_id, a.get_asset_type_display(), a.make_model, a.serial,
                a.qty, a.get_status_display(), "yes" if a.archived else "no",
                a.get_license_type_display() if a.license_type else "",
                ", ".join(f.name for f in a.functionalities.all()),
                a.license_duration_start or "", a.license_duration_end or "",
                a.parent_engine.asset_id if a.parent_engine_id else "", a.notes,
            ])
        zf.writestr("assets.csv", assets_io.getvalue())

        kits_io = io.StringIO()
        writer = csv.writer(kits_io)
        writer.writerow(["kit_name", "asset_id", "asset_type", "make_model", "tag"])
        for kit in Kit.objects.prefetch_related("assets", "kit_asset_tags__tag"):
            tag_by_asset = {kat.asset_id: kat.tag.name for kat in kit.kit_asset_tags.all() if kat.tag_id}
            for asset in kit.assets.all():
                writer.writerow([
                    kit.name, asset.asset_id, asset.get_asset_type_display(), asset.make_model,
                    tag_by_asset.get(asset.id, ""),
                ])
        zf.writestr("kits.csv", kits_io.getvalue())

        jobs_io = io.StringIO()
        writer = csv.writer(jobs_io)
        writer.writerow(["job_name", "category", "start_date", "end_date", "notes"])
        for job in Job.objects.order_by("start_date"):
            writer.writerow([job.name, job.get_category_display(), job.start_date, job.end_date, job.notes])
        zf.writestr("jobs.csv", jobs_io.getvalue())

    buffer.seek(0)
    response = HttpResponse(buffer.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="kitscheduler-export-{today_str}.zip"'
    return response


@login_required
def kit_detail_api(request, kit_id):
    kit = get_object_or_404(Kit, pk=kit_id)
    direct = list(kit.assets.values_list("asset_id", flat=True))
    all_ids = kit.all_asset_ids()
    nested_only_ids = all_ids - set(kit.assets.values_list("id", flat=True))
    nested = list(Asset.objects.filter(id__in=nested_only_ids).values_list("asset_id", flat=True))
    return JsonResponse({"kit_id": kit_id, "name": kit.name, "assets": direct, "nested": nested})


@login_required
def job_detail_api(request, job_id):
    job = get_object_or_404(Job, pk=job_id)
    kit_names = list(KitBooking.objects.filter(job=job).select_related("kit").values_list("kit__name", flat=True))
    staff_names = list(StaffBooking.objects.filter(job=job).select_related("staff_member").values_list("staff_member__name", flat=True))
    asset_ids = list(AssetBooking.objects.filter(job=job).select_related("asset").values_list("asset__asset_id", flat=True))
    return JsonResponse({
        "id": job.id, "name": job.name, "category": job.category,
        "category_display": job.get_category_display(),
        "start_date": job.start_date.isoformat(), "end_date": job.end_date.isoformat(),
        "notes": job.notes, "color": job.resolve_color(),
        "kits": kit_names, "staff": staff_names, "assets": asset_ids,
    })


@login_required
@require_POST
def delete_job(request, job_id):
    job = get_object_or_404(Job, pk=job_id)
    job.delete()
    return JsonResponse({"ok": True})


@login_required
@require_POST
def clone_job(request, job_id):
    original = get_object_or_404(Job, pk=job_id)
    new_job = Job.objects.create(
        name=f"Copy - {original.name}", category=original.category,
        start_date=original.start_date, end_date=original.end_date,
        notes=original.notes, custom_color=original.custom_color,
    )
    for kb in KitBooking.objects.filter(job=original):
        KitBooking.objects.create(kit=kb.kit, job=new_job, start_date=kb.start_date, end_date=kb.end_date)
    for ab in AssetBooking.objects.filter(job=original):
        AssetBooking.objects.create(asset=ab.asset, job=new_job, start_date=ab.start_date, end_date=ab.end_date)
    for sb in StaffBooking.objects.filter(job=original):
        StaffBooking.objects.create(
            staff_member=sb.staff_member, job=new_job,
            start_date=sb.start_date, end_date=sb.end_date, notes=sb.notes,
        )
    return JsonResponse({"ok": True, "job_id": new_job.id})


@login_required
@require_POST
def job_create_view(request):
    name = request.POST.get("name", "").strip()
    category = request.POST.get("category", "").strip()
    notes = request.POST.get("notes", "").strip()
    custom_color = request.POST.get("custom_color", "").strip()
    start_date = request.POST.get("start_date")
    end_date = request.POST.get("end_date")

    if not name:
        return JsonResponse({"error": "Job name is required."}, status=400)
    if not start_date or not end_date:
        return JsonResponse({"error": "Start and end dates are required."}, status=400)
    if custom_color and not re.fullmatch(r"#[0-9A-Fa-f]{6}", custom_color):
        custom_color = ""

    try:
        start = datetime.date.fromisoformat(start_date)
        end = datetime.date.fromisoformat(end_date)
    except ValueError:
        return JsonResponse({"error": "Invalid date format."}, status=400)

    if start > end:
        return JsonResponse({"error": "End date cannot be before start date."}, status=400)

    category = category if category in Job.Category.values else Job.Category.TX

    job = Job.objects.create(
        name=name, category=category, notes=notes,
        custom_color=custom_color, start_date=start, end_date=end,
    )
    return JsonResponse({"ok": True, "job_id": job.id})


@login_required
@require_POST
def create_booking(request):
    kit_id = request.POST.get("kit_id")
    job_id = request.POST.get("job_id")
    new_job_name = request.POST.get("new_job_name", "").strip()
    new_job_category = request.POST.get("new_job_category", "").strip()
    new_job_notes = request.POST.get("new_job_notes", "").strip()
    new_job_color = request.POST.get("new_job_color", "").strip()
    start_date = request.POST.get("start_date")
    end_date = request.POST.get("end_date")

    if not all([kit_id, start_date, end_date]):
        return JsonResponse({"error": "Missing required fields."}, status=400)
    if not job_id and not new_job_name:
        return JsonResponse({"error": "Pick an existing job or enter a name for a new one."}, status=400)
    if new_job_color and not re.fullmatch(r"#[0-9A-Fa-f]{6}", new_job_color):
        new_job_color = ""

    kit = get_object_or_404(Kit, pk=kit_id)

    try:
        start = datetime.date.fromisoformat(start_date)
        end = datetime.date.fromisoformat(end_date)
    except ValueError:
        return JsonResponse({"error": "Invalid date format."}, status=400)

    if start > end:
        return JsonResponse({"error": "End date cannot be before start date."}, status=400)

    if job_id:
        job = get_object_or_404(Job, pk=job_id)
    else:
        category = new_job_category if new_job_category in Job.Category.values else Job.Category.TX
        job = Job.objects.create(
            name=new_job_name, category=category, notes=new_job_notes,
            custom_color=new_job_color, start_date=start, end_date=end,
        )

    conflict = KitBooking.objects.filter(kit=kit, start_date__lte=end, end_date__gte=start).exclude(job=job)
    if conflict.exists():
        return JsonResponse({"error": f"{kit.name} is already booked on {conflict.first().job.name} in that window."}, status=409)

    booking = KitBooking.objects.create(kit=kit, job=job, start_date=start, end_date=end)
    return JsonResponse({"ok": True, "booking_id": booking.id, "job_id": job.id})


@login_required
@require_POST
def delete_booking(request, booking_id):
    booking = get_object_or_404(KitBooking, pk=booking_id)
    booking.delete()
    return JsonResponse({"ok": True})


@login_required
@require_POST
def create_staff_booking(request):
    staff_id = request.POST.get("staff_id")
    job_id = request.POST.get("job_id")
    new_job_name = request.POST.get("new_job_name", "").strip()
    new_job_category = request.POST.get("new_job_category", "").strip()
    new_job_notes = request.POST.get("new_job_notes", "").strip()
    new_job_color = request.POST.get("new_job_color", "").strip()
    start_date = request.POST.get("start_date")
    end_date = request.POST.get("end_date")

    if not all([staff_id, start_date, end_date]):
        return JsonResponse({"error": "Missing required fields."}, status=400)
    if not job_id and not new_job_name:
        return JsonResponse({"error": "Pick an existing job or enter a name for a new one."}, status=400)
    if new_job_color and not re.fullmatch(r"#[0-9A-Fa-f]{6}", new_job_color):
        new_job_color = ""

    staff_member = get_object_or_404(StaffMember, pk=staff_id)

    try:
        start = datetime.date.fromisoformat(start_date)
        end = datetime.date.fromisoformat(end_date)
    except ValueError:
        return JsonResponse({"error": "Invalid date format."}, status=400)

    if start > end:
        return JsonResponse({"error": "End date cannot be before start date."}, status=400)

    if job_id:
        job = get_object_or_404(Job, pk=job_id)
    else:
        category = new_job_category if new_job_category in Job.Category.values else Job.Category.TX
        job = Job.objects.create(
            name=new_job_name, category=category, notes=new_job_notes,
            custom_color=new_job_color, start_date=start, end_date=end,
        )

    booking = StaffBooking.objects.create(staff_member=staff_member, job=job, start_date=start, end_date=end)
    return JsonResponse({"ok": True, "booking_id": booking.id, "job_id": job.id})


@login_required
@require_POST
def delete_staff_booking(request, booking_id):
    booking = get_object_or_404(StaffBooking, pk=booking_id)
    booking.delete()
    return JsonResponse({"ok": True})


@login_required
@require_POST
def create_license_booking(request):
    asset_id = request.POST.get("asset_id")
    job_id = request.POST.get("job_id")
    new_job_name = request.POST.get("new_job_name", "").strip()
    new_job_category = request.POST.get("new_job_category", "").strip()
    new_job_notes = request.POST.get("new_job_notes", "").strip()
    new_job_color = request.POST.get("new_job_color", "").strip()
    start_date = request.POST.get("start_date")
    end_date = request.POST.get("end_date")
    functionalities = request.POST.get("functionalities", "").strip()

    if not all([asset_id, start_date, end_date]):
        return JsonResponse({"error": "Missing required fields."}, status=400)
    if not job_id and not new_job_name:
        return JsonResponse({"error": "Pick an existing job or enter a name for a new one."}, status=400)
    if new_job_color and not re.fullmatch(r"#[0-9A-Fa-f]{6}", new_job_color):
        new_job_color = ""

    asset = get_object_or_404(Asset, pk=asset_id, asset_type=Asset.AssetType.LICENSE)

    try:
        start = datetime.date.fromisoformat(start_date)
        end = datetime.date.fromisoformat(end_date)
    except ValueError:
        return JsonResponse({"error": "Invalid date format."}, status=400)

    if start > end:
        return JsonResponse({"error": "End date cannot be before start date."}, status=400)

    if job_id:
        job = get_object_or_404(Job, pk=job_id)
    else:
        category = new_job_category if new_job_category in Job.Category.values else Job.Category.TX
        job = Job.objects.create(
            name=new_job_name, category=category, notes=new_job_notes,
            custom_color=new_job_color, start_date=start, end_date=end,
        )

    conflict = AssetBooking.objects.filter(asset=asset, start_date__lte=end, end_date__gte=start).exclude(job=job)
    if conflict.exists():
        return JsonResponse(
            {"error": f"{asset.asset_id} is already booked on {conflict.first().job.name} in that window."},
            status=409,
        )

    booking = AssetBooking.objects.create(asset=asset, job=job, start_date=start, end_date=end, functionalities=functionalities)
    return JsonResponse({"ok": True, "booking_id": booking.id, "job_id": job.id})


@login_required
@require_POST
def delete_license_booking(request, booking_id):
    booking = get_object_or_404(AssetBooking, pk=booking_id)
    booking.delete()
    return JsonResponse({"ok": True})


# ---------------------------------------------------------------------------
# Ticketing (fault reports)
# ---------------------------------------------------------------------------

def _public_asset_picker_json():
    assets = Asset.objects.filter(archived=False).order_by("asset_type", "asset_id")
    return [
        {
            "id": a.id, "assetId": a.asset_id, "makeModel": a.make_model,
            "type": a.get_asset_type_display(),
        }
        for a in assets
    ]


def ticket_report_view(request):
    """Public fault-report form. No login required - anyone with the link can submit."""
    if request.method == "POST":
        reporter_name = request.POST.get("reporter_name", "").strip()
        reporter_contact = request.POST.get("reporter_contact", "").strip()
        description = request.POST.get("description", "").strip()
        asset_id = request.POST.get("asset", "").strip()

        ctx = {
            "reporter_name": reporter_name,
            "reporter_contact": reporter_contact,
            "description": description,
            "selected_asset_id": asset_id,
            "assets_json": _public_asset_picker_json(),
        }

        if not reporter_name:
            ctx["error"] = "Please tell us your name."
            return render(request, "inventory/ticket_report.html", ctx)
        if not description:
            ctx["error"] = "Please describe the fault."
            return render(request, "inventory/ticket_report.html", ctx)

        asset = None
        if asset_id.isdigit():
            asset = Asset.objects.filter(pk=asset_id).first()

        ticket = Ticket.objects.create(
            asset=asset,
            reporter_name=reporter_name,
            reporter_contact=reporter_contact,
            description=description,
        )
        TicketHistory.objects.create(
            ticket=ticket,
            changed_by=None,
            field_changed="created",
            new_value=ticket.get_status_display(),
            note="Ticket submitted via public report form.",
        )
        return render(request, "inventory/ticket_report.html", {"submitted": True, "ticket": ticket})

    ctx = {
        "assets_json": _public_asset_picker_json(),
    }
    return render(request, "inventory/ticket_report.html", ctx)


@staff_member_required
def ticket_list_view(request):
    tickets = Ticket.objects.select_related("asset").all()

    status_filter = request.GET.get("status", "")
    if status_filter in Ticket.Status.values:
        tickets = tickets.filter(status=status_filter)

    q = request.GET.get("q", "").strip()
    if q:
        tickets = tickets.filter(
            Q(asset__asset_id__icontains=q)
            | Q(reporter_name__icontains=q)
            | Q(description__icontains=q)
        )

    open_count = Ticket.objects.filter(status=Ticket.Status.OPEN).count()

    return render(request, "inventory/ticket_list.html", {
        "tickets": tickets,
        "statuses": Ticket.Status.choices,
        "selected_status": status_filter,
        "search_query": q,
        "open_count": open_count,
        "active_nav": "tickets",
    })


@staff_member_required
def ticket_detail_view(request, ticket_id):
    ticket = get_object_or_404(Ticket.objects.select_related("asset"), pk=ticket_id)

    if request.method == "POST":
        new_status = request.POST.get("status", ticket.status)
        new_priority = request.POST.get("priority", ticket.priority)
        comment = request.POST.get("comment", "").strip()

        if new_status in Ticket.Status.values and new_status != ticket.status:
            TicketHistory.objects.create(
                ticket=ticket, changed_by=request.user, field_changed="status",
                old_value=ticket.get_status_display(),
                new_value=dict(Ticket.Status.choices).get(new_status, new_status),
            )
            ticket.status = new_status

        if new_priority in Ticket.Priority.values and new_priority != ticket.priority:
            TicketHistory.objects.create(
                ticket=ticket, changed_by=request.user, field_changed="priority",
                old_value=ticket.get_priority_display(),
                new_value=dict(Ticket.Priority.choices).get(new_priority, new_priority),
            )
            ticket.priority = new_priority

        ticket.save()

        if comment:
            TicketHistory.objects.create(
                ticket=ticket, changed_by=request.user, field_changed="comment",
                new_value=comment, note=comment,
            )

        return redirect("/tickets/%d/" % ticket.id)

    return render(request, "inventory/ticket_detail.html", {
        "ticket": ticket,
        "statuses": Ticket.Status.choices,
        "priorities": Ticket.Priority.choices,
        "history": ticket.history.select_related("changed_by").order_by("-created_at"),
        "active_nav": "tickets",
    })


@staff_member_required
@require_POST
def ticket_delete_view(request, ticket_id):
    ticket = get_object_or_404(Ticket, pk=ticket_id)
    ticket.delete()
    return redirect("/tickets/")


# ---------------------------------------------------------------------------
# Van management
# ---------------------------------------------------------------------------

@login_required
def van_list_view(request):
    vehicles = Vehicle.objects.filter(active=True).prefetch_related(
        "usage_logs", "maintenance_logs", "checklists"
    )
    rows = []
    for v in vehicles:
        last_checklist = v.last_checklist()
        rows.append({
            "vehicle": v,
            "last_usage": v.last_usage(),
            "last_maintenance": v.last_maintenance(),
            "last_checklist": last_checklist,
            "checklist_issues": last_checklist.issues_count() if last_checklist else 0,
        })
    return render(request, "inventory/van_list.html", {
        "rows": rows,
        "active_nav": "vans",
    })


@login_required
def van_create_view(request):
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        registration = request.POST.get("registration", "").strip()
        make_model = request.POST.get("make_model", "").strip()
        notes = request.POST.get("notes", "").strip()

        if not name:
            return render(request, "inventory/van_form.html", {
                "error": "Van name is required.", "active_nav": "vans",
                "name": name, "registration": registration, "make_model": make_model, "notes": notes,
            })
        if Vehicle.objects.filter(name=name).exists():
            return render(request, "inventory/van_form.html", {
                "error": f'A vehicle named "{name}" already exists.', "active_nav": "vans",
                "name": name, "registration": registration, "make_model": make_model, "notes": notes,
            })

        vehicle = Vehicle.objects.create(
            name=name, registration=registration, make_model=make_model, notes=notes,
        )
        return redirect(f"/vans/{vehicle.id}/")

    return render(request, "inventory/van_form.html", {"active_nav": "vans"})


@login_required
def van_edit_view(request, vehicle_id):
    vehicle = get_object_or_404(Vehicle, pk=vehicle_id)

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        registration = request.POST.get("registration", "").strip()
        make_model = request.POST.get("make_model", "").strip()
        notes = request.POST.get("notes", "").strip()
        active = request.POST.get("active") == "on"

        if not name:
            return render(request, "inventory/van_form.html", {
                "vehicle": vehicle, "error": "Van name is required.", "active_nav": "vans",
                "name": name, "registration": registration, "make_model": make_model,
                "notes": notes, "active": active,
            })
        if Vehicle.objects.filter(name=name).exclude(pk=vehicle_id).exists():
            return render(request, "inventory/van_form.html", {
                "vehicle": vehicle, "error": f'A vehicle named "{name}" already exists.', "active_nav": "vans",
                "name": name, "registration": registration, "make_model": make_model,
                "notes": notes, "active": active,
            })

        vehicle.name = name
        vehicle.registration = registration
        vehicle.make_model = make_model
        vehicle.notes = notes
        vehicle.active = active
        vehicle.save()
        return redirect(f"/vans/{vehicle.id}/")

    return render(request, "inventory/van_form.html", {
        "vehicle": vehicle, "active_nav": "vans",
        "name": vehicle.name, "registration": vehicle.registration,
        "make_model": vehicle.make_model, "notes": vehicle.notes, "active": vehicle.active,
    })


@login_required
@require_POST
def van_delete_view(request, vehicle_id):
    vehicle = get_object_or_404(Vehicle, pk=vehicle_id)
    vehicle.delete()
    return redirect("/vans/")


@login_required
def van_detail_view(request, vehicle_id):
    vehicle = get_object_or_404(Vehicle, pk=vehicle_id)
    return render(request, "inventory/van_detail.html", {
        "vehicle": vehicle,
        "usage_logs": vehicle.usage_logs.select_related("driver").all()[:30],
        "maintenance_logs": vehicle.maintenance_logs.all()[:30],
        "checklists": vehicle.checklists.select_related("checked_by").all()[:15],
        "staff_members": StaffMember.objects.filter(active=True).order_by("name"),
        "checklist_items": VAN_CHECKLIST_ITEMS,
        "today": datetime.date.today().isoformat(),
        "active_nav": "vans",
    })


@login_required
@require_POST
def van_usage_add(request, vehicle_id):
    vehicle = get_object_or_404(Vehicle, pk=vehicle_id)
    date_str = request.POST.get("date", "").strip()
    driver_id = request.POST.get("driver", "").strip()
    purpose = request.POST.get("purpose", "").strip()
    destination = request.POST.get("destination", "").strip()
    start_mileage = request.POST.get("start_mileage", "").strip()
    end_mileage = request.POST.get("end_mileage", "").strip()
    notes = request.POST.get("notes", "").strip()

    try:
        date = datetime.date.fromisoformat(date_str) if date_str else datetime.date.today()
    except ValueError:
        date = datetime.date.today()

    driver = StaffMember.objects.filter(pk=driver_id).first() if driver_id.isdigit() else None

    log = VanUsageLog(
        vehicle=vehicle, driver=driver, date=date, purpose=purpose, destination=destination,
        notes=notes, logged_by=request.user,
        start_mileage=int(start_mileage) if start_mileage.isdigit() else None,
        end_mileage=int(end_mileage) if end_mileage.isdigit() else None,
    )
    try:
        log.full_clean()
        log.save()
    except ValidationError:
        pass  # silently skip invalid mileage rather than break the page; the field is optional anyway

    return redirect(f"/vans/{vehicle.id}/")


@login_required
@require_POST
def van_maintenance_add(request, vehicle_id):
    vehicle = get_object_or_404(Vehicle, pk=vehicle_id)
    date_str = request.POST.get("date", "").strip()
    description = request.POST.get("description", "").strip()
    performed_by = request.POST.get("performed_by", "").strip()
    cost = request.POST.get("cost", "").strip()
    next_due_date_str = request.POST.get("next_due_date", "").strip()

    if not description:
        return redirect(f"/vans/{vehicle.id}/")

    try:
        date = datetime.date.fromisoformat(date_str) if date_str else datetime.date.today()
    except ValueError:
        date = datetime.date.today()

    next_due_date = None
    if next_due_date_str:
        try:
            next_due_date = datetime.date.fromisoformat(next_due_date_str)
        except ValueError:
            next_due_date = None

    cost_val = None
    if cost:
        try:
            cost_val = float(cost)
        except ValueError:
            cost_val = None

    VanMaintenanceLog.objects.create(
        vehicle=vehicle, date=date, description=description, performed_by=performed_by,
        cost=cost_val, next_due_date=next_due_date, logged_by=request.user,
    )
    return redirect(f"/vans/{vehicle.id}/")


@login_required
@require_POST
def van_checklist_add(request, vehicle_id):
    vehicle = get_object_or_404(Vehicle, pk=vehicle_id)
    date_str = request.POST.get("date", "").strip()
    checked_by_id = request.POST.get("checked_by", "").strip()
    notes = request.POST.get("notes", "").strip()

    try:
        date = datetime.date.fromisoformat(date_str) if date_str else datetime.date.today()
    except ValueError:
        date = datetime.date.today()

    checked_by = StaffMember.objects.filter(pk=checked_by_id).first() if checked_by_id.isdigit() else None

    items = []
    for idx, item_name in enumerate(VAN_CHECKLIST_ITEMS):
        ok = request.POST.get(f"item_ok_{idx}") == "on"
        note = request.POST.get(f"item_note_{idx}", "").strip()
        items.append({"item": item_name, "ok": ok, "note": note})

    VanChecklist.objects.create(
        vehicle=vehicle, date=date, checked_by=checked_by, items=items,
        notes=notes, logged_by=request.user,
    )
    return redirect(f"/vans/{vehicle.id}/")


@login_required
@require_POST
def van_usage_delete(request, log_id):
    log = get_object_or_404(VanUsageLog, pk=log_id)
    vehicle_id = log.vehicle_id
    log.delete()
    return redirect(f"/vans/{vehicle_id}/")


@login_required
@require_POST
def van_maintenance_delete(request, log_id):
    log = get_object_or_404(VanMaintenanceLog, pk=log_id)
    vehicle_id = log.vehicle_id
    log.delete()
    return redirect(f"/vans/{vehicle_id}/")


@login_required
@require_POST
def van_checklist_delete(request, checklist_id):
    checklist = get_object_or_404(VanChecklist, pk=checklist_id)
    vehicle_id = checklist.vehicle_id
    checklist.delete()
    return redirect(f"/vans/{vehicle_id}/")
