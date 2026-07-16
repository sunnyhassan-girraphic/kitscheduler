import csv
import datetime
import io
import re
import zipfile

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from ..models import Asset, CategoryColour, Job, Kit, LicenseFunctionality, Tag


@login_required
def settings_view(request):
    categories = Job.Category.choices
    colours = {cc.category: cc.colour for cc in CategoryColour.objects.all()}

    if request.method == "POST":
        for value, _ in categories:
            colour = request.POST.get(f"colour_{value}", "").strip()
            if colour and re.fullmatch(r"#[0-9A-Fa-f]{6}", colour):
                CategoryColour.objects.update_or_create(
                    category=value, defaults={"colour": colour}
                )
        return redirect("/settings/")

    return render(request, "inventory/settings.html", {
        "categories": categories,
        "colours": colours,
        "tags": Tag.objects.all(),
        "functionalities": LicenseFunctionality.objects.all(),
        "active_nav": "settings",
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


