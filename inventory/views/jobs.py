import datetime
import re

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from ..models import Asset, AssetBooking, Job, Kit, KitBooking, StaffBooking, StaffMember
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

    all_kits = [
        {"id": k.id, "name": k.name, "memberCount": k.assets.count(), "members": _kit_member_rows(k)}
        for k in Kit.objects.order_by("name")
    ]
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

        error = None
        start = end = None
        if not name:
            error = "Job name is required."
        elif category not in Job.Category.values:
            error = "Pick a valid category."
        else:
            try:
                start = datetime.date.fromisoformat(start_date_raw)
                end = datetime.date.fromisoformat(end_date_raw)
            except (TypeError, ValueError):
                error = "Invalid date format."
            else:
                if start > end:
                    error = "End date cannot be before start date."

        if custom_color and not re.fullmatch(r"#[0-9A-Fa-f]{6}", custom_color):
            custom_color = ""

        if error:
            context = _job_edit_context(job)
            context["error"] = error
            context["form_values"] = {
                "name": name, "category": category, "notes": notes,
                "custom_color": custom_color,
                "start_date": start_date_raw, "end_date": end_date_raw,
            }
            return render(request, "inventory/job_form.html", context)

        job.name = name
        job.category = category
        job.notes = notes
        job.custom_color = custom_color
        job.start_date = start
        job.end_date = end
        job.save()
        return redirect(f"/jobs/{job.id}/edit/")

    return render(request, "inventory/job_form.html", _job_edit_context(job))
