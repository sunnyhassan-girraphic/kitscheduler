import datetime
import re

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from ..models import Asset, AssetBooking, Job, JobHistory, Kit, KitBooking, StaffBooking, StaffMember
from .timeline import _kit_member_rows


def _job_edit_context(job):
    kit_bookings = list(
        KitBooking.objects.filter(job=job).select_related("kit").order_by("start_date")
    )
    staff_bookings = list(
        StaffBooking.objects.filter(job=job).select_related("staff_member").order_by("start_date")
    )
    license_bookings = list(
        AssetBooking.objects.filter(job=job).select_related("asset").order_by("start_date")
    )
    for b in kit_bookings:
        b.kit_members = _kit_member_rows(b.kit)

    # Pre-fetch all kit bookings (excluding this job) so JS can check conflicts
    other_bookings_qs = KitBooking.objects.exclude(job=job).select_related("job").order_by("start_date")
    bookings_by_kit = {}
    for b in other_bookings_qs:
        bookings_by_kit.setdefault(b.kit_id, []).append({
            "jobName": b.job.name,
            "start": b.start_date.isoformat(),
            "end": b.end_date.isoformat(),
        })

    all_kits = []
    for k in Kit.objects.prefetch_related("assets", "kit_asset_tags__tag", "assets__nested_assets").order_by("name"):
        member_count = k.assets.count()
        nested_count = sum(
            m.nested_assets.count() for m in k.assets.all()
        )
        all_kits.append({
            "id": k.id,
            "name": k.name,
            "memberCount": member_count,
            "nestedCount": nested_count,
            "members": _kit_member_rows(k),
            "bookings": bookings_by_kit.get(k.id, []),
        })
    all_staff = [
        {"id": s.id, "name": s.name}
        for s in StaffMember.objects.filter(active=True).order_by("name")
    ]
    all_licenses = []
    for lic in Asset.objects.filter(
        asset_type=Asset.AssetType.LICENSE, archived=False
    ).prefetch_related("functionalities").order_by("asset_id"):
        all_licenses.append({
            "id": lic.id,
            "assetId": lic.asset_id,
            "functionalities": [f.name for f in lic.functionalities.all()],
        })

    return {
        "job": job,
        "job_categories": Job.Category.choices,
        "kit_bookings": kit_bookings,
        "staff_bookings": staff_bookings,
        "license_bookings": license_bookings,
        "all_kits_json": all_kits,
        "all_staff_json": all_staff,
        "all_licenses_json": all_licenses,
        "staff_members": list(StaffMember.objects.filter(active=True).order_by("name")),
        "active_nav": "timeline",
    }


@login_required
def job_edit_view(request, job_id):
    job = get_object_or_404(Job, pk=job_id)

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        category = request.POST.get("category", "").strip()
        notes = request.POST.get("notes", "").strip()
        custom_color = request.POST.get("custom_color", "").strip()
        start_date_raw = request.POST.get("start_date", "")
        end_date_raw = request.POST.get("end_date", "")
        last_updated_by_id = request.POST.get("last_updated_by", "").strip()
        last_updated_date_raw = request.POST.get("last_updated_date", "").strip()
        last_updated_notes = request.POST.get("last_updated_notes", "").strip()

        error = None
        start = end = None
        if not name:
            error = "Job name is required."
        elif category not in Job.Category.values:
            error = "Pick a valid category."
        elif not last_updated_date_raw:
            error = "Last updated date is required before you can save."
        else:
            try:
                start = datetime.date.fromisoformat(start_date_raw)
                end = datetime.date.fromisoformat(end_date_raw)
            except (TypeError, ValueError):
                error = "Invalid date format."
            else:
                if start > end:
                    error = "End date cannot be before start date."
            if not error:
                try:
                    datetime.date.fromisoformat(last_updated_date_raw)
                except ValueError:
                    error = "Last updated date is invalid."

        if custom_color and not re.fullmatch(r"#[0-9A-Fa-f]{6}", custom_color):
            custom_color = ""

        if error:
            context = _job_edit_context(job)
            context["error"] = error
            context["form_values"] = {
                "name": name, "category": category, "notes": notes,
                "custom_color": custom_color,
                "start_date": start_date_raw, "end_date": end_date_raw,
                "last_updated_by_id": last_updated_by_id,
                "last_updated_date": last_updated_date_raw,
                "last_updated_notes": last_updated_notes,
            }
            return render(request, "inventory/job_form.html", context)

        last_updated_by = None
        if last_updated_by_id.isdigit():
            last_updated_by = StaffMember.objects.filter(pk=last_updated_by_id).first()

        before_values = {
            f: getattr(job, f)
            for f in ["name", "category", "start_date", "end_date", "notes", "custom_color"]
        }
        before_note = job.last_updated_notes

        job.name = name
        job.category = category
        job.notes = notes
        job.custom_color = custom_color
        job.start_date = start
        job.end_date = end
        job.last_updated_by = last_updated_by
        job.last_updated_date = datetime.date.fromisoformat(last_updated_date_raw)
        job.last_updated_notes = last_updated_notes
        job.save()

        JobHistory.record_scalar_changes(job, before_values, last_updated_by)
        JobHistory.record_note(job, last_updated_by, before_note, last_updated_notes)

        return redirect(f"/jobs/{job.id}/edit/")

    history_mode = request.GET.get("history", "month")
    if history_mode not in ("week", "month", "all"):
        history_mode = "month"
    history_page_num = request.GET.get("history_page", 1)
    current_staff = StaffMember.for_user(request.user)
    context = _job_edit_context(job)
    context.update({
        "history_mode": history_mode,
        "history_page": JobHistory.filtered_for(job, mode=history_mode, page=history_page_num),
        "last_updated_by_id": str(current_staff.id) if current_staff else "",
        "last_updated_date": datetime.date.today().isoformat(),
        "last_updated_notes": job.last_updated_notes,
    })
    return render(request, "inventory/job_form.html", context)
