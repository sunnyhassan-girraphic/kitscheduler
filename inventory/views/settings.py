import csv
import datetime
import io
import re
import zipfile

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

import json

from ..models import Asset, CategoryColour, DashboardWidget, Job, Kit, LicenseFunctionality, Tag


def _is_admin(user):
    return user.is_authenticated and (
        user.is_superuser or user.groups.filter(name="Admin").exists()
    )


@login_required
def settings_view(request):
    categories = Job.Category.choices
    colours = {cc.category: cc.colour for cc in CategoryColour.objects.all()}
    is_admin = _is_admin(request.user)

    if request.method == "POST":
        if not is_admin:
            return redirect("/settings/")
        for value, _ in categories:
            colour = request.POST.get(f"colour_{value}", "").strip()
            if colour and re.fullmatch(r"#[0-9A-Fa-f]{6}", colour):
                CategoryColour.objects.update_or_create(
                    category=value, defaults={"colour": colour}
                )
        return redirect("/settings/")

    from django.core.cache import cache
    ticket_defaults = {
        "default_priority": cache.get("greg_setting_default_ticket_priority", "MEDIUM"),
        "require_photo": cache.get("greg_setting_ticket_require_photo", "") == "1",
        "thanks_message": cache.get("greg_setting_ticket_thanks_message", ""),
    }

    widget_qs   = DashboardWidget.for_user(request.user)
    widget_meta = [
        {"id": w.widget_id, "label": w.get_widget_id_display(), "visible": w.visible, "position": w.position, "column": w.column}
        for w in widget_qs
    ]

    return render(request, "inventory/settings.html", {
        "categories":       categories,
        "is_admin":         is_admin,
        "ticket_defaults":  ticket_defaults,
        "colours":          colours,
        "tags":             Tag.objects.all(),
        "functionalities":  LicenseFunctionality.objects.all(),
        "widget_meta_json": json.dumps(widget_meta),
        "active_nav":       "settings",
    })


@login_required
@require_POST
def settings_tag_add(request):
    name = request.POST.get("name", "").strip()
    color = request.POST.get("color", "").strip()
    if name and not Tag.objects.filter(name__iexact=name).exists():
        Tag.objects.create(name=name, color=color if re.fullmatch(r"#[0-9A-Fa-f]{6}", color or "") else "")
    return redirect("/settings/")


@login_required
@require_POST
def settings_tag_delete(request, tag_id):
    Tag.objects.filter(pk=tag_id).delete()
    return redirect("/settings/")


@login_required
@require_POST
def settings_functionality_add(request):
    name = request.POST.get("name", "").strip()
    if name and not LicenseFunctionality.objects.filter(name__iexact=name).exists():
        LicenseFunctionality.objects.create(name=name)
    return redirect("/settings/")


@login_required
@require_POST
def settings_functionality_delete(request, func_id):
    LicenseFunctionality.objects.filter(pk=func_id).delete()
    return redirect("/settings/")


@login_required
def export_csv_view(request):
    today_str = datetime.date.today().isoformat()
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        assets_io = io.StringIO()
        writer = csv.writer(assets_io)
        writer.writerow(["asset_id", "type", "make_model", "serial", "qty", "status",
                         "archived", "license_type", "license_functionalities",
                         "license_duration_start", "license_duration_end", "parent_engine", "notes"])
        for a in Asset.objects.select_related("parent_engine").prefetch_related("functionalities").order_by("asset_type", "asset_id"):
            writer.writerow([
                a.asset_id, a.get_asset_type_display(), a.make_model, a.serial,
                a.qty, a.get_status_display(), "yes" if a.archived else "no",
                a.get_license_type_display() if a.license_type else "",
                ", ".join(f.name for f in a.functionalities.all()),
                a.license_duration_start or "", a.license_duration_end or "",
                a.parent_engine.asset_id if a.parent_engine_id else "", a.notes,
            ])
        zf.writestr("assets.csv", assets_io.getvalue())

        kits_io = io.StringIO()
        writer = csv.writer(kits_io)
        writer.writerow(["kit_name", "asset_id", "asset_type", "make_model", "tag"])
        for kit in Kit.objects.prefetch_related("assets", "kit_asset_tags__tag"):
            tag_by_asset = {kat.asset_id: kat.tag.name for kat in kit.kit_asset_tags.all() if kat.tag_id}
            for asset in kit.assets.all():
                writer.writerow([
                    kit.name, asset.asset_id, asset.get_asset_type_display(), asset.make_model,
                    tag_by_asset.get(asset.id, ""),
                ])
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


from django.contrib.auth import logout as auth_logout

@require_POST
def logout_view(request):
    auth_logout(request)
    return redirect("/login/")


from django.contrib.auth import update_session_auth_hash

@login_required
@require_POST
def change_password_view(request):
    current  = request.POST.get("current_password", "")
    new      = request.POST.get("new_password", "")
    confirm  = request.POST.get("confirm_password", "")

    def render_settings(error=None, success=None):
        from ..models import CategoryColour, Tag, LicenseFunctionality, Job
        categories = Job.Category.choices
        colours = {cc.category: cc.colour for cc in CategoryColour.objects.all()}
        return render(request, "inventory/settings.html", {
            "categories": categories,
            "colours": colours,
            "tags": Tag.objects.all(),
            "functionalities": LicenseFunctionality.objects.all(),
            "is_admin": _is_admin(request.user),
            "pw_error": error,
            "pw_success": success,
        })

    if not request.user.check_password(current):
        return render_settings(error="Current password is incorrect.")
    if len(new) < 8:
        return render_settings(error="New password must be at least 8 characters.")
    if new != confirm:
        return render_settings(error="New passwords don't match.")

    request.user.set_password(new)
    request.user.save()
    update_session_auth_hash(request, request.user)
    return render_settings(success="Password updated successfully.")


@login_required
@require_POST
def settings_tag_edit(request, tag_id):
    if not _is_admin(request.user):
        return redirect("/settings/#inventory")
    tag = Tag.objects.filter(pk=tag_id).first()
    if tag:
        name = request.POST.get("name", "").strip()
        color = request.POST.get("color", tag.color)
        if name:
            tag.name = name
            tag.color = color
            tag.save()
    return redirect("/settings/#inventory")


@login_required
@require_POST
def settings_functionality_edit(request, func_id):
    if not _is_admin(request.user):
        return redirect("/settings/#inventory")
    func = LicenseFunctionality.objects.filter(pk=func_id).first()
    if func:
        name = request.POST.get("name", "").strip()
        if name:
            func.name = name
            func.save()
    return redirect("/settings/#inventory")


@login_required
@require_POST
def settings_key_value(request):
    """Save a simple key/value app setting."""
    if not _is_admin(request.user):
        return redirect("/settings/#tickets")
    from django.contrib.sites.shortcuts import get_current_site
    key = request.POST.get("setting_key", "").strip()
    value = request.POST.get("setting_value", "").strip()
    allowed = {"default_ticket_priority", "ticket_require_photo", "ticket_thanks_message"}
    if key in allowed:
        from django.core.cache import cache
        cache.set(f"greg_setting_{key}", value, timeout=None)
    if key.startswith("ticket_"):
        return redirect("/settings/#tickets")
    return redirect("/settings/")
