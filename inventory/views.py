import csv
import datetime
import io
import re
import zipfile

from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.db import models
from django.db.models import Count, Q
from django.http import HttpResponseRedirect, JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import (
    Asset, AssetBooking, CategoryColor, Job, Kit, KitBooking,
    StaffBooking, StaffMember, Ticket, TicketHistory,
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
    ).order_by("asset_id"))
    for lic in licenses:
        lic.func_tags = [t.strip() for t in lic.license_functionality.split(",")] if lic.license_functionality else []

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
    today = datetime.date.today()

    kits = Kit.objects.prefetch_related("assets__nested_assets", "bookings__job")
    if query:
        kits = kits.filter(name__icontains=query)
    kits = list(kits.order_by("name"))

    rows = []
    for kit in kits:
        members = list(kit.assets.all().order_by("asset_type", "asset_id"))
        nested_count = sum(
            m.nested_assets.count() for m in members if m.asset_type == Asset.AssetType.ENGINE
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
        "total_kits": len(kits),
        "active_nav": "kits",
    }
    return render(request, "inventory/kit_list.html", context)


def _kit_eligible_assets_qs(current_kit=None):
    """Assets eligible to be direct kit members: not archived, not COMPONENT
    type, and not already claimed by a DIFFERENT kit (directly, or nested
    inside an Engine that's a member of a different kit) - an asset can only
    live in one kit at a time. Assets already in the current kit are always
    included so they remain visible/removable even in edge cases."""
    other_kit_asset_ids = set(
        Kit.objects.exclude(pk=current_kit.pk if current_kit else None)
        .values_list("assets__id", flat=True)
    )
    other_kit_asset_ids.discard(None)
    other_kit_engine_ids = set(
        Asset.objects.filter(
            id__in=other_kit_asset_ids, asset_type=Asset.AssetType.ENGINE
        ).values_list("id", flat=True)
    )
    current_kit_asset_ids = set(current_kit.assets.values_list("id", flat=True)) if current_kit else set()

    return Asset.objects.filter(
        archived=False
    ).exclude(
        asset_type=Asset.AssetType.COMPONENT
    ).filter(
        Q(id__in=current_kit_asset_ids)
        | (~Q(id__in=other_kit_asset_ids) & ~Q(parent_engine_id__in=other_kit_engine_ids))
    )


def _kit_picker_assets(current_kit=None):
    """Assets eligible to be direct kit members, with nested-component info for engines."""
    assets = _kit_eligible_assets_qs(current_kit).order_by(
        "asset_type", "asset_id"
    ).prefetch_related("nested_assets")

    data = []
    for a in assets:
        nested = []
        if a.asset_type == Asset.AssetType.ENGINE:
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


@login_required
def kit_create_view(request):
    assets, assets_json = _kit_picker_assets()

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        notes = request.POST.get("notes", "").strip()
        asset_ids = request.POST.getlist("assets")
        selected_ids = [int(i) for i in asset_ids if i.isdigit()]

        if not name:
            return render(request, "inventory/kit_form.html", {
                "assets": assets, "assets_json": assets_json, "error": "Kit name is required.",
                "selected_ids": selected_ids, "notes": notes, "active_nav": "kits",
            })

        if Kit.objects.filter(name=name).exists():
            return render(request, "inventory/kit_form.html", {
                "assets": assets, "assets_json": assets_json, "error": f'A kit named "{name}" already exists.',
                "selected_ids": selected_ids, "name": name, "notes": notes, "active_nav": "kits",
            })

        kit = Kit.objects.create(name=name, notes=notes)
        if selected_ids:
            valid_assets = _kit_eligible_assets_qs(kit).filter(id__in=selected_ids)
            kit.assets.set(valid_assets)

        return redirect("/kits/")

    return render(request, "inventory/kit_form.html", {
        "assets": assets, "assets_json": assets_json, "selected_ids": [], "active_nav": "kits",
    })


@login_required
def kit_edit_view(request, kit_id):
    kit = get_object_or_404(Kit, pk=kit_id)
    assets, assets_json = _kit_picker_assets(current_kit=kit)

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        notes = request.POST.get("notes", "").strip()
        asset_ids = request.POST.getlist("assets")
        selected_ids = [int(i) for i in asset_ids if i.isdigit()]

        if not name:
            return render(request, "inventory/kit_form.html", {
                "kit": kit, "assets": assets, "assets_json": assets_json, "error": "Kit name is required.",
                "selected_ids": selected_ids, "notes": notes, "active_nav": "kits",
            })

        if Kit.objects.filter(name=name).exclude(pk=kit_id).exists():
            return render(request, "inventory/kit_form.html", {
                "kit": kit, "assets": assets, "assets_json": assets_json,
                "error": f'A kit named "{name}" already exists.',
                "selected_ids": selected_ids, "name": name, "notes": notes, "active_nav": "kits",
            })

        kit.name = name
        kit.notes = notes
        kit.save()

        valid_assets = _kit_eligible_assets_qs(kit).filter(id__in=selected_ids)
        kit.assets.set(valid_assets)

        return redirect("/kits/")

    return render(request, "inventory/kit_form.html", {
        "kit": kit,
        "assets": assets,
        "assets_json": assets_json,
        "selected_ids": list(kit.assets.values_list("id", flat=True)),
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


def _engine_form_context(engine=None):
    """Shared context builder for the engine create/edit form."""
    components_qs = Asset.objects.filter(
        archived=False
    ).exclude(
        asset_type=Asset.AssetType.ENGINE
    ).filter(
        Q(parent_engine__isnull=True) | Q(parent_engine=engine)
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

    return {
        "components": components,
        "components_json": components_json,
        "staff_members": list(StaffMember.objects.filter(active=True).order_by("name")),
        "statuses": Asset.Status.choices,
        "active_nav": "engines",
    }


def _apply_component_selection(engine, selected_ids):
    """Nest/un-nest components against an engine, mirroring the admin form's save()."""
    valid_components = Asset.objects.filter(
        id__in=selected_ids, archived=False
    ).exclude(asset_type=Asset.AssetType.ENGINE)
    selected = set(valid_components)
    currently_nested = set(engine.nested_assets.all())
    for asset in selected - currently_nested:
        asset.parent_engine = engine
        asset.save(update_fields=["parent_engine"])
    for asset in currently_nested - selected:
        asset.parent_engine = None
        asset.save(update_fields=["parent_engine"])


@login_required
def engine_create_view(request):
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

        form_ctx = _engine_form_context()
        form_ctx.update({
            "asset_id": asset_id, "make_model": make_model, "serial": serial,
            "qty": qty, "status": status, "archived": archived, "notes": notes,
            "last_updated_by_id": last_updated_by_id, "last_updated_date": last_updated_date, "last_updated_notes": last_updated_notes,
            "selected_ids": selected_ids,
        })

        if not asset_id:
            form_ctx["error"] = "Engine ID is required."
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

        parsed_date = None
        if last_updated_date:
            try:
                parsed_date = datetime.date.fromisoformat(last_updated_date)
            except ValueError:
                parsed_date = None

        engine = Asset.objects.create(
            asset_id=asset_id, asset_type=Asset.AssetType.ENGINE,
            make_model=make_model, serial=serial, qty=qty_val, status=status,
            archived=archived, notes=notes,
            last_updated_by=last_updated_by, last_updated_date=parsed_date, last_updated_notes=last_updated_notes,
        )
        _apply_component_selection(engine, selected_ids)

        return redirect("/engines/")

    form_ctx = _engine_form_context()
    form_ctx.update({
        "selected_ids": [], "qty": "1", "status": Asset.Status.AVAILABLE,
        "last_updated_date": datetime.date.today().isoformat(),
    })
    return render(request, "inventory/engine_form.html", form_ctx)


@login_required
def engine_edit_view(request, engine_id):
    engine = get_object_or_404(Asset, pk=engine_id, asset_type=Asset.AssetType.ENGINE)

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

        form_ctx = _engine_form_context(engine=engine)
        form_ctx.update({
            "engine": engine,
            "asset_id": asset_id, "make_model": make_model, "serial": serial,
            "qty": qty, "status": status, "archived": archived, "notes": notes,
            "last_updated_by_id": last_updated_by_id, "last_updated_date": last_updated_date, "last_updated_notes": last_updated_notes,
            "selected_ids": selected_ids,
        })

        if not asset_id:
            form_ctx["error"] = "Engine ID is required."
            return render(request, "inventory/engine_form.html", form_ctx)

        if Asset.objects.filter(asset_id=asset_id).exclude(pk=engine.pk).exists():
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

        parsed_date = engine.last_updated_date
        if last_updated_date:
            try:
                parsed_date = datetime.date.fromisoformat(last_updated_date)
            except ValueError:
                pass
        else:
            parsed_date = None

        engine.asset_id = asset_id
        engine.make_model = make_model
        engine.serial = serial
        engine.qty = qty_val
        engine.status = status
        engine.archived = archived
        engine.notes = notes
        engine.last_updated_by = last_updated_by
        engine.last_updated_date = parsed_date
        engine.last_updated_notes = last_updated_notes
        engine.save()

        _apply_component_selection(engine, selected_ids)

        return redirect("/engines/")

    form_ctx = _engine_form_context(engine=engine)
    form_ctx.update({
        "engine": engine,
        "asset_id": engine.asset_id,
        "make_model": engine.make_model,
        "serial": engine.serial,
        "qty": str(engine.qty),
        "status": engine.status,
        "archived": engine.archived,
        "notes": engine.notes,
        "last_updated_by_id": str(engine.last_updated_by_id) if engine.last_updated_by_id else "",
        "last_updated_date": engine.last_updated_date.isoformat() if engine.last_updated_date else "", "last_updated_notes": engine.last_updated_notes,
        "selected_ids": list(engine.nested_assets.values_list("id", flat=True)),
    })
    return render(request, "inventory/engine_form.html", form_ctx)


@login_required
@require_POST
def engine_delete_view(request, engine_id):
    engine = get_object_or_404(Asset, pk=engine_id, asset_type=Asset.AssetType.ENGINE)
    engine.delete()
    return redirect("/engines/")


@login_required
def engine_list_view(request):
    make_model = request.GET.get("make_model", "")
    show_archived = request.GET.get("archived") == "1"

    engines = Asset.objects.filter(
        asset_type=Asset.AssetType.ENGINE
    ).select_related("last_updated_by").prefetch_related("nested_assets", "kits")

    if not show_archived:
        engines = engines.filter(archived=False)
    if make_model:
        engines = engines.filter(make_model=make_model)

    engines = list(engines.order_by("make_model", "asset_id"))

    engine_models = (
        Asset.objects.filter(asset_type=Asset.AssetType.ENGINE, archived=False)
        .exclude(make_model="")
        .order_by("make_model")
        .values_list("make_model", flat=True)
        .distinct()
    )

    context = {
        "engines": engines,
        "engine_models": list(engine_models),
        "selected_make_model": make_model,
        "show_archived": show_archived,
        "active_nav": "engines",
    }
    return render(request, "inventory/engine_list.html", context)


@login_required
def license_list_view(request):
    show_archived = request.GET.get("archived") == "1"
    licenses = Asset.objects.filter(
        asset_type=Asset.AssetType.LICENSE
    ).select_related("last_updated_by").prefetch_related("kits")
    if not show_archived:
        licenses = licenses.filter(archived=False)
    licenses = list(licenses.order_by("asset_id"))
    for lic in licenses:
        lic.func_tags = [t.strip() for t in lic.license_functionality.split(",")] if lic.license_functionality else []
    context = {
        "licenses": licenses,
        "show_archived": show_archived,
        "total": len(licenses),
        "active_nav": "licenses",
    }
    return render(request, "inventory/license_list.html", context)


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
        "active_nav": "settings",
    })


@login_required
def export_csv_view(request):
    today_str = datetime.date.today().isoformat()
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        assets_io = io.StringIO()
        writer = csv.writer(assets_io)
        writer.writerow(["asset_id", "type", "make_model", "serial", "qty", "status",
                         "archived", "license_type", "license_functionality", "parent_engine", "notes"])
        for a in Asset.objects.select_related("parent_engine").order_by("asset_type", "asset_id"):
            writer.writerow([
                a.asset_id, a.get_asset_type_display(), a.make_model, a.serial,
                a.qty, a.get_status_display(), "yes" if a.archived else "no",
                a.license_type, a.license_functionality,
                a.parent_engine.asset_id if a.parent_engine_id else "", a.notes,
            ])
        zf.writestr("assets.csv", assets_io.getvalue())

        kits_io = io.StringIO()
        writer = csv.writer(kits_io)
        writer.writerow(["kit_name", "asset_id", "asset_type", "make_model"])
        for kit in Kit.objects.prefetch_related("assets"):
            for asset in kit.assets.all():
                writer.writerow([kit.name, asset.asset_id, asset.get_asset_type_display(), asset.make_model])
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
    nested = list(
        Asset.objects.filter(
            parent_engine__in=kit.assets.filter(asset_type=Asset.AssetType.ENGINE)
        ).values_list("asset_id", flat=True)
    )
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
