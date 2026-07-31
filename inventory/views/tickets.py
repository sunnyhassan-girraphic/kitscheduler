from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ..models import StaffMember

from ..models import Asset, Ticket, TicketHistory


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

    priority_filter = request.GET.get("priority", "")
    if priority_filter in Ticket.Priority.values:
        tickets = tickets.filter(priority=priority_filter)

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
        "priorities": Ticket.Priority.choices,
        "selected_status": status_filter,
        "selected_priority": priority_filter,
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

    history_qs = ticket.history.select_related("changed_by").order_by("-created_at")

    # Pre-resolve Django User → StaffMember name for consistent display.
    # Other history feeds use StaffMember.name; this was using get_username()
    # (raw Django username, always lowercase). Build a user_id → name map once.
    user_ids = {e.changed_by_id for e in history_qs if e.changed_by_id}
    staff_by_user_id = {}
    if user_ids:
        for sm in StaffMember.objects.filter(user_id__in=user_ids).select_related("user"):
            staff_by_user_id[sm.user_id] = sm.name

    # Build label→css_key maps so the template can render coloured badges.
    # History stores display labels (e.g. "In progress") not raw values.
    # CSS classes are badge-{value.lower().replace(' ','_')}, e.g. badge-in_progress.
    status_label_to_key = {label: value.lower() for value, label in Ticket.Status.choices}
    priority_label_to_key = {label: value.lower() for value, label in Ticket.Priority.choices}

    history = []
    for entry in history_qs:
        if entry.changed_by_id:
            display_name = staff_by_user_id.get(entry.changed_by_id) or entry.changed_by.get_username()
            is_public = False
        else:
            display_name = None
            is_public = True

        # For status/priority entries, add CSS keys for badge colouring
        old_css = new_css = None
        if entry.field_changed == "status":
            old_css = status_label_to_key.get(entry.old_value)
            new_css = status_label_to_key.get(entry.new_value)
        elif entry.field_changed == "priority":
            old_css = priority_label_to_key.get(entry.old_value)
            new_css = priority_label_to_key.get(entry.new_value)

        history.append({
            "entry": entry,
            "display_name": display_name,
            "is_public": is_public,
            "old_css": old_css,
            "new_css": new_css,
        })

    return render(request, "inventory/ticket_detail.html", {
        "ticket": ticket,
        "statuses": Ticket.Status.choices,
        "priorities": Ticket.Priority.choices,
        "history": history,
        "active_nav": "tickets",
    })


@staff_member_required
@require_POST
def ticket_delete_view(request, ticket_id):
    ticket = get_object_or_404(Ticket, pk=ticket_id)
    ticket.delete()
    return redirect("/tickets/")


