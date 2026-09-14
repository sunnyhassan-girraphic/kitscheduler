import datetime
import re

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from ..models import Asset, AssetBooking, Job, Kit, KitBooking, StaffBooking, StaffMember
from .common import STEP_DAYS, _build_rows, _date_range, _month_range, _parse_anchor, _week_availability


def _kit_member_rows(kit):
    tags_by_asset_id = {
        kat.asset_id: {"name": kat.tag.name, "color": kat.tag.color or "#EAB308"}
        for kat in kit.kit_asset_tags.select_related("tag", "tag_2") if kat.tag_id
    }
    tags2_by_asset_id = {
        kat.asset_id: {"name": kat.tag_2.name, "color": kat.tag_2.color or "#EAB308"}
        for kat in kit.kit_asset_tags.select_related("tag_2") if kat.tag_2_id
    }
    qty_by_asset_id = {kat.asset_id: kat.quantity for kat in kit.kit_asset_tags.all()}
    rows = []
    for m in kit.assets.all().order_by("asset_type", "asset_id"):
        row = {
            "assetId": m.asset_id,
            "type": m.get_asset_type_display(),
            "model": m.make_model or None,
            "statusKey": m.status.lower(),
            "status": m.get_status_display(),
            "tag": tags_by_asset_id.get(m.id),
            "tag2": tags2_by_asset_id.get(m.id),
            "qty": qty_by_asset_id.get(m.id, 1),
            "nested": [],
        }
        if m.asset_type in Asset.CONTAINER_TYPES:
            for comp in m.nested_assets.all().order_by("asset_id"):
                row["nested"].append({
                    "assetId": comp.asset_id, "type": comp.get_asset_type_display(),
                    "model": comp.make_model or None, "statusKey": comp.status.lower(),
                    "status": comp.get_status_display(), "indent": False,
                })
                if comp.asset_type in Asset.NESTABLE_CONTAINER_TYPES:
                    for sub in comp.nested_assets.all().order_by("asset_id"):
                        row["nested"].append({
                            "assetId": sub.asset_id, "type": sub.get_asset_type_display(),
                            "model": sub.make_model or None, "statusKey": sub.status.lower(),
                            "status": sub.get_status_display(), "indent": True,
                        })
        rows.append(row)
    return rows


@login_required
def timeline_view(request):
    range_mode = request.GET.get("range", "2week")
    if range_mode not in ("2week", "month"):
        range_mode = "2week"

    show_engines = request.GET.get("engines", "1") != "0"
    show_laptops = request.GET.get("laptops", "1") != "0"
    show_kits = request.GET.get("kits", "0") != "0"

    anchor = _parse_anchor(request, range_mode=range_mode)
    if range_mode == "month":
        days = _month_range(anchor)
    else:
        days = _date_range(anchor, days=14)

    today = datetime.date.today()

    # --- Engines ---
    engines = list(Asset.objects.filter(
        asset_type=Asset.AssetType.ENGINE, archived=False, parent_engine__isnull=True
    ).order_by("asset_id")) if show_engines else []
    engine_bookings = list(
        AssetBooking.objects.select_related("job", "asset").filter(
            asset__asset_type=Asset.AssetType.ENGINE,
            start_date__lte=days[-1], end_date__gte=days[0]
        )
    ) if show_engines else []
    bookings_by_engine = {}
    for b in engine_bookings:
        bookings_by_engine.setdefault(b.asset_id, []).append(b)

    # --- Laptops ---
    laptops = list(Asset.objects.filter(
        asset_type=Asset.AssetType.LAPTOP, archived=False
    ).order_by("asset_id")) if show_laptops else []
    laptop_bookings = list(
        AssetBooking.objects.select_related("job", "asset").filter(
            asset__asset_type=Asset.AssetType.LAPTOP,
            start_date__lte=days[-1], end_date__gte=days[0]
        )
    ) if show_laptops else []
    bookings_by_laptop = {}
    for b in laptop_bookings:
        bookings_by_laptop.setdefault(b.asset_id, []).append(b)

    # --- Kits (optional) ---
    kits = list(Kit.objects.exclude(status=Kit.Status.ARCHIVED).prefetch_related(
        "assets", "bookings__job"
    ).order_by("name")) if show_kits else []
    kit_bookings_qs = list(
        KitBooking.objects.select_related("job", "kit").filter(
            start_date__lte=days[-1], end_date__gte=days[0]
        )
    ) if show_kits else []
    bookings_by_kit = {}
    for b in kit_bookings_qs:
        bookings_by_kit.setdefault(b.kit_id, []).append(b)
    kit_rows = _build_rows(kits, bookings_by_kit, days)

    def _build_strip_rows(assets, bookings_by_asset_id, days):
        day_index = {d: i for i, d in enumerate(days)}
        rows = []
        for asset in assets:
            asset_bookings = bookings_by_asset_id.get(asset.id, [])
            strips = []
            for d in days:
                hit = next((b for b in asset_bookings if b.start_date <= d <= b.end_date), None)
                strips.append({
                    "date": d,
                    "booking": hit,
                    "is_weekend": d.weekday() >= 5,
                    "is_today": d == today,
                })
            visible = [b for b in asset_bookings if b.start_date <= days[-1] and b.end_date >= days[0]]
            visible.sort(key=lambda b: b.start_date)
            spans = []
            for b in visible:
                clipped_start = max(b.start_date, days[0])
                clipped_end = min(b.end_date, days[-1])
                spans.append({
                    "booking": b,
                    "color": b.job.resolve_color(),
                    "grid_col_start": day_index[clipped_start] + 1,
                    "grid_col_end": day_index[clipped_end] + 2,
                    "continues_before": b.start_date < days[0],
                    "continues_after": b.end_date > days[-1],
                })
            rows.append({"asset": asset, "strips": strips, "spans": spans})
        return rows

    def _group_by_make_model(rows):
        """Group strip rows by make_model. Assets with no make_model go last under 'No model set'."""
        seen = {}
        order = []
        ungrouped = []
        for row in rows:
            key = row["asset"].make_model.strip() if row["asset"].make_model and row["asset"].make_model.strip() else None
            if key is None:
                ungrouped.append(row)
            else:
                if key not in seen:
                    seen[key] = []
                    order.append(key)
                seen[key].append(row)
        result = [{"make_model": k, "rows": seen[k]} for k in order]
        if ungrouped:
            result.append({"make_model": "No model set", "rows": ungrouped})
        return result

    engine_rows = _build_strip_rows(engines, bookings_by_engine, days)
    laptop_rows = _build_strip_rows(laptops, bookings_by_laptop, days)
    engine_groups = _group_by_make_model(engine_rows)
    laptop_groups = _group_by_make_model(laptop_rows)

    jobs = list(Job.objects.order_by("-start_date")[:200])

    if range_mode == "month":
        prev_anchor = (anchor.replace(day=1) - datetime.timedelta(days=1)).replace(day=1)
        next_month = anchor.replace(day=1)
        if next_month.month == 12:
            next_anchor = next_month.replace(year=next_month.year + 1, month=1)
        else:
            next_anchor = next_month.replace(month=next_month.month + 1)
    else:
        prev_anchor = anchor - datetime.timedelta(days=14)
        next_anchor = anchor + datetime.timedelta(days=14)

    context = {
        "range_mode": range_mode,
        "show_engines": show_engines,
        "show_laptops": show_laptops,
        "show_kits": show_kits,
        "days": days,
        "engine_rows": engine_rows,
        "laptop_rows": laptop_rows,
        "engine_groups": engine_groups,
        "laptop_groups": laptop_groups,
        "kit_rows": kit_rows,
        "jobs": jobs,
        "job_categories": Job.Category.choices,
        "today": today,
        "anchor": anchor,
        "prev_anchor": prev_anchor,
        "next_anchor": next_anchor,
        "total_engines": len(engines),
        "total_laptops": len(laptops),
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
    notes = request.POST.get("notes", "").strip()

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
        if existing.notes != notes:
            existing.notes = notes
            existing.save(update_fields=["notes"])
        return JsonResponse({"ok": True, "booking_id": existing.id, "job_id": job.id})

    booking = StaffBooking.objects.create(staff_member=staff_member, job=job, start_date=start, end_date=end, notes=notes)
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


@login_required
@require_POST
def create_asset_booking(request):
    """Book an engine or laptop on a job from the availability timeline."""
    asset_id = request.POST.get("asset_id")
    job_id = request.POST.get("job_id")
    new_job_name = request.POST.get("new_job_name", "").strip()
    new_job_category = request.POST.get("new_job_category", "").strip()
    new_job_notes = request.POST.get("new_job_notes", "").strip()
    new_job_color = request.POST.get("new_job_color", "").strip()
    start_date = request.POST.get("start_date")
    end_date = request.POST.get("end_date")

    if not all([asset_id, start_date, end_date]):
        return JsonResponse({"error": "Missing required fields."}, status=400)
    if not job_id and not new_job_name:
        return JsonResponse({"error": "Pick an existing job or enter a name for a new one."}, status=400)
    if new_job_color and not re.fullmatch(r"#[0-9A-Fa-f]{6}", new_job_color):
        new_job_color = ""

    asset = get_object_or_404(
        Asset, pk=asset_id,
        asset_type__in=[Asset.AssetType.ENGINE, Asset.AssetType.LAPTOP]
    )

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

    same_job_existing = AssetBooking.objects.filter(
        asset=asset, job=job, start_date__lte=end, end_date__gte=start
    ).first()
    if same_job_existing:
        same_job_existing.start_date = start
        same_job_existing.end_date = end
        same_job_existing.save(update_fields=["start_date", "end_date"])
        return JsonResponse({"ok": True, "booking_id": same_job_existing.id, "job_id": job.id})

    conflict = AssetBooking.objects.filter(
        asset=asset, start_date__lte=end, end_date__gte=start
    ).exclude(job=job)
    if conflict.exists():
        return JsonResponse(
            {"error": f"{asset.asset_id} is already booked on {conflict.first().job.name} in that window."},
            status=409,
        )

    booking = AssetBooking.objects.create(asset=asset, job=job, start_date=start, end_date=end)
    return JsonResponse({"ok": True, "booking_id": booking.id, "job_id": job.id})


@login_required
@require_POST
def delete_asset_booking(request, booking_id):
    booking = get_object_or_404(
        AssetBooking, pk=booking_id,
        asset__asset_type__in=[Asset.AssetType.ENGINE, Asset.AssetType.LAPTOP]
    )
    booking.delete()
    return JsonResponse({"ok": True})


