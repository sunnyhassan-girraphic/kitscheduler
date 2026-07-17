import datetime

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ..models import Asset, LicenseFunctionality, StaffMember


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
        viz_ticket = request.POST.get("viz_ticket", "").strip()
        last_updated_by_id = request.POST.get("last_updated_by", "").strip()
        last_updated_date = request.POST.get("last_updated_date", "").strip()
        last_updated_notes = request.POST.get("last_updated_notes", "").strip()

        form_ctx = _license_form_context()
        form_ctx.update({
            "asset_id": asset_id, "status": status, "archived": archived, "notes": notes,
            "license_type": license_type, "selected_func_ids": func_ids,
            "license_duration_start": duration_start, "license_duration_end": duration_end,
            "viz_ticket": viz_ticket,
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
            viz_ticket=viz_ticket,
            last_updated_by=last_updated_by, last_updated_date=parsed_date,
            last_updated_notes=last_updated_notes,
        )
        lic.functionalities.set(LicenseFunctionality.objects.filter(id__in=func_ids))

        return redirect("/licenses/")

    form_ctx = _license_form_context()
    current_staff = StaffMember.for_user(request.user)
    form_ctx.update({
        "status": Asset.Status.AVAILABLE, "selected_func_ids": [],
        "last_updated_by_id": str(current_staff.id) if current_staff else "",
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
        viz_ticket = request.POST.get("viz_ticket", "").strip()
        last_updated_by_id = request.POST.get("last_updated_by", "").strip()
        last_updated_date = request.POST.get("last_updated_date", "").strip()
        last_updated_notes = request.POST.get("last_updated_notes", "").strip()

        form_ctx = _license_form_context(lic)
        form_ctx.update({
            "license": lic,
            "asset_id": asset_id, "status": status, "archived": archived, "notes": notes,
            "license_type": license_type, "selected_func_ids": func_ids,
            "license_duration_start": duration_start, "license_duration_end": duration_end,
            "viz_ticket": viz_ticket,
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
        lic.viz_ticket = viz_ticket
        lic.last_updated_by = last_updated_by
        lic.last_updated_date = parsed_date
        lic.last_updated_notes = last_updated_notes
        lic.save()
        lic.functionalities.set(LicenseFunctionality.objects.filter(id__in=func_ids))

        return redirect("/licenses/")

    form_ctx = _license_form_context(lic)
    current_staff = StaffMember.for_user(request.user)
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
        "viz_ticket": lic.viz_ticket,
        # Defaults to whoever's signed in now and today's date - not the
        # previously-saved value - since opening this form means you're
        # about to log a new update, not review the last one.
        "last_updated_by_id": str(current_staff.id) if current_staff else "",
        "last_updated_date": datetime.date.today().isoformat(),
        "last_updated_notes": lic.last_updated_notes,
    })
    return render(request, "inventory/license_form.html", form_ctx)


@login_required
@require_POST
def license_delete_view(request, license_id):
    lic = get_object_or_404(Asset, pk=license_id, asset_type=Asset.AssetType.LICENSE)
    lic.delete()
    return redirect("/licenses/")


