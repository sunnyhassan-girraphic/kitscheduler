import datetime

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from ..models import Asset, AssetBooking, Job, Kit, KitBooking, StaffBooking, StaffMember, Ticket
from .common import _date_range, _week_availability


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


