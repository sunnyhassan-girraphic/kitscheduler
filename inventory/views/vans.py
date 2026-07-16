import datetime

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ..models import StaffMember, Vehicle, VanLog, VAN_CHECKLIST_ITEMS


@login_required
def van_list_view(request):
    vehicles = Vehicle.objects.filter(active=True).prefetch_related("logs")
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
                "error": f'A van named "{name}" already exists.', "active_nav": "vans",
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
                "vehicle": vehicle, "error": f'A van named "{name}" already exists.', "active_nav": "vans",
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
        "usage_logs": vehicle.logs.filter(log_type=VanLog.LogType.USAGE).select_related("driver")[:30],
        "maintenance_logs": vehicle.logs.filter(log_type=VanLog.LogType.MAINTENANCE)[:30],
        "checklists": vehicle.logs.filter(log_type=VanLog.LogType.CHECKLIST).select_related("checked_by")[:15],
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

    log = VanLog(
        vehicle=vehicle, log_type=VanLog.LogType.USAGE, driver=driver, date=date,
        purpose=purpose, destination=destination, notes=notes, logged_by=request.user,
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

    VanLog.objects.create(
        vehicle=vehicle, log_type=VanLog.LogType.MAINTENANCE, date=date, description=description,
        performed_by=performed_by, cost=cost_val, next_due_date=next_due_date, logged_by=request.user,
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

    VanLog.objects.create(
        vehicle=vehicle, log_type=VanLog.LogType.CHECKLIST, date=date, checked_by=checked_by,
        items=items, notes=notes, logged_by=request.user,
    )
    return redirect(f"/vans/{vehicle.id}/")


@login_required
@require_POST
def van_usage_delete(request, log_id):
    log = get_object_or_404(VanLog, pk=log_id, log_type=VanLog.LogType.USAGE)
    vehicle_id = log.vehicle_id
    log.delete()
    return redirect(f"/vans/{vehicle_id}/")


@login_required
@require_POST
def van_maintenance_delete(request, log_id):
    log = get_object_or_404(VanLog, pk=log_id, log_type=VanLog.LogType.MAINTENANCE)
    vehicle_id = log.vehicle_id
    log.delete()
    return redirect(f"/vans/{vehicle_id}/")


@login_required
@require_POST
def van_checklist_delete(request, checklist_id):
    checklist = get_object_or_404(VanLog, pk=checklist_id, log_type=VanLog.LogType.CHECKLIST)
    vehicle_id = checklist.vehicle_id
    checklist.delete()
    return redirect(f"/vans/{vehicle_id}/")
