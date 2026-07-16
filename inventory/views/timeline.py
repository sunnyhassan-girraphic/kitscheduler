import datetime
import re

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from ..models import Asset, AssetBooking, Job, Kit, KitBooking, StaffBooking, StaffMember
from .common import STEP_DAYS, _build_rows, _date_range, _month_range, _parse_anchor, _week_availability


def _kit_member_rows(kit):
    tags_by_asset_id = {kat.asset_id: kat.tag.name for kat in kit.kit_asset_tags.select_related("tag") if kat.tag_id}
    rows = []
    for m in kit.assets.all().order_by("asset_type", "asset_id"):
        row = {
            "assetId": m.asset_id,
            "type": m.get_asset_type_display(),
            "model": m.make_model or None,
            "status": m.get_status_display(),
            "tag": tags_by_asset_id.get(m.id),
            "nested": [],
        }
        if m.asset_type in Asset.CONTAINER_TYPES:
            for comp in m.nested_assets.all().order_by("asset_id"):
                row["nested"].append({
                    "assetId": comp.asset_id, "type": comp.get_asset_type_display(),
                    "model": comp.make_model or None, "status": comp.get_status_display(),
                    "indent": False,
                })
                if comp.asset_type in Asset.NESTABLE_CONTAINER_TYPES:
                    for sub in comp.nested_assets.all().order_by("asset_id"):
                        row["nested"].append({
                            "assetId": sub.asset_id, "type": sub.get_asset_type_display(),
                            "model": sub.make_model or None, "status": sub.get_status_display(),
                            "indent": True,
                        })
        rows.append(row)
    return rows


@login_required
def timeline_view(request):
    range_mode = request.GET.get("range", "week")
    if range_mode not in ("week", "month"):
        range_mode = "week"

    show_kits = request.GET.get("kits", "1") != "0"
    show_staff = request.GET.get("staff", "0") != "0"
    show_licenses = request.GET.get("licenses", "1") != "0"

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
    for row in license_rows:
        lic = row["item"]
        start, end = lic.license_duration_start, lic.license_duration_end
        for cell in row["cells"]:
            cell["in_duration"] = bool(start and end and start <= cell["date"] <= end)

    license_details = {
        row["item"].id: {
            "id": row["item"].id,
            "assetId": row["item"].asset_id,
            "editUrl": f"/licenses/{row['item'].id}/edit/",
            "type": row["item"].get_license_type_display() or None,
            "functionalities": [f.name for f in row["item"].functionalities.all()],
            "durationStart": row["item"].license_duration_start.strftime("%d %b %Y") if row["item"].license_duration_start else None,
            "durationEnd": row["item"].license_duration_end.strftime("%d %b %Y") if row["item"].license_duration_end else None,
            "vizTicket": row["item"].viz_ticket or None,
            "status": row["item"].get_status_display(),
            "archived": row["item"].archived,
            "serial": row["item"].serial or None,
            "notes": row["item"].notes or None,
            "kits": [k.name for k in row["item"].kits.all()],
            "lastUpdatedBy": row["item"].last_updated_by.name if row["item"].last_updated_by_id else None,
            "lastUpdatedDate": row["item"].last_updated_date.strftime("%d %b %Y") if row["item"].last_updated_date else None,
            "lastUpdatedNotes": row["item"].last_updated_notes or None,
        }
        for row in license_rows
    }

    today = datetime.date.today()
    kit_details = {}
    for row in kit_rows:
        kit = row["item"]
        current_booking = kit.bookings.filter(
            start_date__lte=today, end_date__gte=today
        ).select_related("job").first()
        kit_details[kit.id] = {
            "id": kit.id,
            "name": kit.name,
            "editUrl": f"/kits/{kit.id}/edit/",
            "notes": kit.notes or None,
            "memberCount": kit.assets.count(),
            "members": _kit_member_rows(kit),
            "currentJob": current_booking.job.name if current_booking else None,
            "currentJobDates": (
                f"{current_booking.start_date.strftime('%d %b')} \u2013 {current_booking.end_date.strftime('%d %b %Y')}"
                if current_booking else None
            ),
        }

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
        "license_details_json": license_details,
        "kit_details_json": kit_details,
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

    same_job_existing = KitBooking.objects.filter(
        kit=kit, job=job, start_date__lte=end, end_date__gte=start
    ).first()
    if same_job_existing:
        same_job_existing.start_date = start
        same_job_existing.end_date = end
        same_job_existing.save(update_fields=["start_date", "end_date"])
        return JsonResponse({"ok": True, "booking_id": same_job_existing.id, "job_id": job.id})

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

    existing = StaffBooking.objects.filter(
        staff_member=staff_member, job=job, start_date=start, end_date=end
    ).first()
    if existing:
        return JsonResponse({"ok": True, "booking_id": existing.id, "job_id": job.id})

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

    # If this license already has a booking on THIS job that overlaps the
    # requested window, treat this as an update (e.g. re-ticking
    # functionalities, or nudging the dates) rather than creating a second
    # row - a second row for the same job/asset/window would violate the
    # unique_asset_booking_per_job_window constraint.
    same_job_existing = AssetBooking.objects.filter(
        asset=asset, job=job, start_date__lte=end, end_date__gte=start
    ).first()
    if same_job_existing:
        same_job_existing.start_date = start
        same_job_existing.end_date = end
        same_job_existing.functionalities = functionalities
        same_job_existing.save(update_fields=["start_date", "end_date", "functionalities"])
        return JsonResponse({"ok": True, "booking_id": same_job_existing.id, "job_id": job.id})

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


