import datetime
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from ..models import Asset, AssetBooking, AssetHistory, DashboardWidget, Job, Kit, KitBooking, KitHistory, StaffBooking, StaffMember, Ticket
from ..models import StockTakeSession
from .common import _date_range, _week_availability


@login_required
def dashboard_view(request):
    today = datetime.date.today()

    # ── Widget config for this user ──────────────────────────────────────────
    widget_qs = DashboardWidget.for_user(request.user)
    widget_col   = {w.widget_id: w.column  for w in widget_qs}
    widget_visible = {w.widget_id: w.visible for w in widget_qs}
    # Build ordered lists per column
    left_widgets  = [w for w in widget_qs if w.column == "left"]
    right_widgets = [w for w in widget_qs if w.column == "right"]
    full_widgets  = [w for w in widget_qs if w.column == "full"]
    widget_meta = [
        {"id": w.widget_id, "label": w.get_widget_id_display(), "visible": w.visible, "position": w.position, "column": w.column}
        for w in widget_qs
    ]

    # ── Week window ──────────────────────────────────────────────────────────
    week_start = today - datetime.timedelta(days=today.weekday())
    week_days  = _date_range(week_start, days=7)

    # ── Kit availability this week ───────────────────────────────────────────
    kits = list(Kit.objects.all())
    kit_bookings_week = list(KitBooking.objects.select_related("job", "kit").filter(
        start_date__lte=week_days[-1], end_date__gte=week_days[0]
    ))
    bookings_by_kit = {}
    for b in kit_bookings_week:
        bookings_by_kit.setdefault(b.kit_id, []).append(b)
    kits_fully_free, kits_partially_free = _week_availability(kits, bookings_by_kit, week_days)
    kits_booked_count = len(kits) - kits_fully_free

    # ── Licenses expiring soon (within 30 days) ──────────────────────────────
    expiry_threshold = today + datetime.timedelta(days=30)
    licenses_expiring = Asset.objects.filter(
        asset_type=Asset.AssetType.LICENSE,
        archived=False,
        license_duration_end__isnull=False,
        license_duration_end__gte=today,
        license_duration_end__lte=expiry_threshold,
    ).count()
    licenses_expired = Asset.objects.filter(
        asset_type=Asset.AssetType.LICENSE,
        archived=False,
        license_duration_end__isnull=False,
        license_duration_end__lt=today,
    ).count()
    open_tickets       = Ticket.objects.filter(status__in=[Ticket.Status.OPEN, Ticket.Status.IN_PROGRESS])
    open_ticket_count  = open_tickets.count()
    urgent_ticket_count = open_tickets.filter(priority__in=[Ticket.Priority.HIGH, Ticket.Priority.URGENT]).count()

    # ── Needs attention ───────────────────────────────────────────────────────
    attention_qs = Asset.objects.filter(
        archived=False, status__in=[Asset.Status.NEEDS_REPAIR, Asset.Status.MISSING]
    )
    attention_count  = attention_qs.count()
    attention_assets = list(attention_qs.order_by("status", "asset_id")[:12])

    # ── Active jobs today (with kits) ────────────────────────────────────────
    active_jobs_qs  = list(Job.objects.filter(start_date__lte=today, end_date__gte=today).order_by("start_date"))
    active_job_ids  = [j.id for j in active_jobs_qs]
    active_kit_bookings = list(
        KitBooking.objects.select_related("kit").filter(
            job_id__in=active_job_ids, start_date__lte=today, end_date__gte=today
        )
    )
    kits_by_job = {}
    for b in active_kit_bookings:
        kits_by_job.setdefault(b.job_id, []).append(b.kit.name)

    active_jobs = []
    for job in active_jobs_qs:
        active_jobs.append({
            "job":      job,
            "kits":     kits_by_job.get(job.id, []),
            "days_left": (job.end_date - today).days,
        })

    # ── Active kits summary (kits with a booking overlapping today) ──────────
    active_kit_rows = []
    for b in kit_bookings_week:
        if b.start_date <= today <= b.end_date:
            active_kit_rows.append({
                "kit":     b.kit,
                "job":     b.job,
                "ends":    b.end_date,
                "days_left": (b.end_date - today).days,
            })
    active_kit_rows.sort(key=lambda r: r["ends"])

    # ── Mini timeline strip (kits only, this week) ───────────────────────────
    strip_data = {
        "days": [d.isoformat() for d in week_days],
        "today": today.isoformat(),
        "jobs": [],
    }
    # Collect jobs that have kit bookings this week, deduplicated
    seen_job_ids = set()
    for b in kit_bookings_week:
        if b.job_id not in seen_job_ids:
            seen_job_ids.add(b.job_id)
            strip_data["jobs"].append({
                "id":       b.job.id,
                "name":     b.job.name,
                "color":    b.job.resolve_color(),
                "start":    b.job.start_date.isoformat(),
                "end":      b.job.end_date.isoformat(),
                "category": b.job.get_category_display(),
            })

    # ── Upcoming jobs (starting in future) ───────────────────────────────────
    upcoming_jobs = list(Job.objects.filter(start_date__gt=today).order_by("start_date")[:8])

    # ── Jobs this week count (for stats strip) ────────────────────────────────
    jobs_today_count = len(active_jobs_qs)
    jobs_this_week   = Job.objects.filter(
        start_date__lte=week_days[-1], end_date__gte=week_days[0]
    ).count()

    # ── License expiry ────────────────────────────────────────────────────────
    expiry_threshold = today + datetime.timedelta(days=60)
    _expiring_qs = list(
        Asset.objects.filter(
            asset_type=Asset.AssetType.LICENSE,
            archived=False,
            license_duration_end__isnull=False,
            license_duration_end__gte=today,
            license_duration_end__lte=expiry_threshold,
        ).order_by("license_duration_end")
    )
    expiring_licenses = [
        {"lic": l, "days_left": (l.license_duration_end - today).days}
        for l in _expiring_qs
    ]
    _expired_qs = list(
        Asset.objects.filter(
            asset_type=Asset.AssetType.LICENSE,
            archived=False,
            license_duration_end__isnull=False,
            license_duration_end__lt=today,
        ).order_by("-license_duration_end")[:10]
    )
    expired_licenses = [
        {"lic": l, "days_ago": (today - l.license_duration_end).days}
        for l in _expired_qs
    ]
    latest_stocktake = StockTakeSession.objects.first()  # ordered by -started_at
    stocktake_data = None
    if latest_stocktake:
        total    = latest_stocktake.total_count
        reviewed = latest_stocktake.reviewed_count
        flagged  = latest_stocktake.flagged_count
        pending  = latest_stocktake.pending_count
        pct      = int(reviewed / total * 100) if total else 0
        stocktake_data = {
            "session":  latest_stocktake,
            "total":    total,
            "reviewed": reviewed,
            "flagged":  flagged,
            "pending":  pending,
            "pct":      pct,
            "complete": latest_stocktake.status == StockTakeSession.Status.COMPLETE,
        }

    # ── Recent activity (last 10 asset + kit changes) ─────────────────────────
    asset_history = list(
        AssetHistory.objects.select_related("asset", "changed_by")
        .exclude(field_changed="created")
        .order_by("-created_at")[:15]
    )
    kit_history = list(
        KitHistory.objects.select_related("kit", "changed_by")
        .order_by("-created_at")[:15]
    )
    # Merge and sort by created_at, take top 10
    activity_feed = sorted(
        [{"type": "asset", "obj": h, "ts": h.created_at} for h in asset_history] +
        [{"type": "kit",   "obj": h, "ts": h.created_at} for h in kit_history],
        key=lambda x: x["ts"], reverse=True
    )[:10]

    context = {
        "today":              today,
        "widget_visible":     widget_visible,
        "widget_col":         widget_col,
        "widget_meta_json":   json.dumps(widget_meta),
        "left_widgets":       left_widgets,
        "right_widgets":      right_widgets,
        "full_widgets":       full_widgets,
        # Stats strip
        "jobs_today_count":   jobs_today_count,
        "jobs_this_week":     jobs_this_week,
        "kits_booked_count":  kits_booked_count,
        "total_kits":         len(kits),
        "open_ticket_count":  open_ticket_count,
        "urgent_ticket_count": urgent_ticket_count,
        "licenses_expiring":  licenses_expiring,
        "licenses_expired":   licenses_expired,
        "attention_count":    attention_count,
        # Widgets
        "active_jobs":        active_jobs,
        "active_kit_rows":    active_kit_rows,
        "upcoming_jobs":      upcoming_jobs,
        "attention_assets":   attention_assets,
        "strip_data_json":    json.dumps(strip_data),
        "job_categories":     Job.Category.choices,
        "active_nav":         "dashboard",
        # New widgets
        "stocktake_data":     stocktake_data,
        "expiring_licenses":  expiring_licenses,
        "expired_licenses":   expired_licenses,
        "activity_feed":      activity_feed,
    }
    return render(request, "inventory/dashboard.html", context)


@login_required
@require_POST
def dashboard_widgets_save(request):
    """Save widget order and visibility for the current user."""
    try:
        payload = json.loads(request.body)
        widgets = payload.get("widgets", [])
        valid_ids = {wid for wid, _, _, _ in DashboardWidget.DEFAULTS}
        valid_cols = {c for c, _ in DashboardWidget.COLUMN_CHOICES}
        for item in widgets:
            wid = item.get("id")
            if wid not in valid_ids:
                continue
            col = item.get("column", "left")
            if col not in valid_cols:
                col = "left"
            DashboardWidget.objects.update_or_create(
                user=request.user,
                widget_id=wid,
                defaults={
                    "position": int(item.get("position", 0)),
                    "visible":  bool(item.get("visible", True)),
                    "column":   col,
                },
            )
        return JsonResponse({"ok": True})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
