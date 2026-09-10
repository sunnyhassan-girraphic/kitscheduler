import csv
import datetime
import io
import os
import re
import shutil
import subprocess
import tempfile
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


# ── Database backup ──────────────────────────────────────────────────────────

def _db_info():
    """Return (db_type, db_settings_dict) for the default database."""
    from django.db import connection
    vendor = connection.vendor  # 'postgresql' or 'sqlite'
    from django.conf import settings as django_settings
    db = django_settings.DATABASES["default"]
    return vendor, db


def _pg_bin(tool):
    # Return full path to a PostgreSQL CLI tool, respecting PG_BIN_PATH env var.
    pg_bin = os.environ.get("PG_BIN_PATH", "").strip()
    if pg_bin:
        return os.path.join(pg_bin, tool)
    return tool


@login_required
def db_backup_view(request):
    if not _is_admin(request.user):
        return redirect("/settings/")

    vendor, db = _db_info()
    today = datetime.date.today().isoformat()

    if vendor == "postgresql":
        env = os.environ.copy()
        env["PGPASSWORD"] = db.get("PASSWORD", "")
        cmd = [
            _pg_bin("pg_dump"),
            "-h", db.get("HOST", "localhost"),
            "-p", str(db.get("PORT", 5432)),
            "-U", db.get("USER", ""),
            "-d", db.get("NAME", ""),
            "--no-password",
            "--format=plain",
            "--encoding=UTF8",
            "--clean",        # adds DROP statements before each CREATE
            "--if-exists",    # prevents errors if objects don't exist yet
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, env=env, timeout=120)
            if result.returncode != 0:
                return HttpResponse(
                    f"pg_dump failed: {result.stderr.decode()}", status=500, content_type="text/plain"
                )
            response = HttpResponse(result.stdout, content_type="application/sql")
            response["Content-Disposition"] = f'attachment; filename="greg-backup-{today}.sql"'
            return response
        except FileNotFoundError:
            return HttpResponse(
                "pg_dump not found. Set PG_BIN_PATH in your .env to the PostgreSQL bin directory, e.g. C:\\Program Files\\PostgreSQL\\18\\bin",
                status=500, content_type="text/plain"
            )

    elif vendor == "sqlite":
        db_path = str(db.get("NAME", ""))
        if not os.path.exists(db_path):
            return HttpResponse("SQLite database file not found.", status=404, content_type="text/plain")
        with open(db_path, "rb") as f:
            data = f.read()
        response = HttpResponse(data, content_type="application/octet-stream")
        response["Content-Disposition"] = f'attachment; filename="greg-backup-{today}.db"'
        return response

    return HttpResponse("Unsupported database type.", status=500, content_type="text/plain")


@login_required
@require_POST
def db_restore_view(request):
    if not _is_admin(request.user):
        return redirect("/settings/")

    confirm = request.POST.get("confirm_word", "").strip()
    if confirm != "RESTORE":
        from django.contrib import messages
        messages.error(request, "Type RESTORE to confirm.")
        return redirect("/settings/#data")

    uploaded = request.FILES.get("restore_file")
    if not uploaded:
        return redirect("/settings/#data")

    vendor, db = _db_info()
    filename = uploaded.name.lower()

    if vendor == "postgresql":
        if not filename.endswith(".sql"):
            return HttpResponse("Upload a .sql file for PostgreSQL restore.", status=400, content_type="text/plain")

        with tempfile.NamedTemporaryFile(suffix=".sql", delete=False) as tmp:
            for chunk in uploaded.chunks():
                tmp.write(chunk)
            tmp_path = tmp.name

        try:
            env = os.environ.copy()
            env["PGPASSWORD"] = db.get("PASSWORD", "")
            psql_base = [
                _pg_bin("psql"),
                "-h", db.get("HOST", "localhost"),
                "-p", str(db.get("PORT", 5432)),
                "-U", db.get("USER", ""),
                "-d", db.get("NAME", ""),
                "--no-password",
            ]

            # Step 1: drop all existing tables/sequences/views in public schema
            # This ensures a clean slate before restoring
            drop_sql = (
                "DROP SCHEMA public CASCADE; "
                "CREATE SCHEMA public; "
                "GRANT ALL ON SCHEMA public TO public;"
            )
            drop_result = subprocess.run(
                psql_base + ["-c", drop_sql],
                capture_output=True, env=env, timeout=60
            )
            if drop_result.returncode != 0:
                return HttpResponse(
                    f"Failed to clear existing schema: {drop_result.stderr.decode()}",
                    status=500, content_type="text/plain"
                )

            # Step 2: restore the backup
            restore_result = subprocess.run(
                psql_base + ["-f", tmp_path],
                capture_output=True, env=env, timeout=300
            )
            if restore_result.returncode != 0:
                return HttpResponse(
                    f"psql restore failed: {restore_result.stderr.decode()}",
                    status=500, content_type="text/plain"
                )
        finally:
            os.unlink(tmp_path)

        from django.contrib import messages
        messages.success(request, "Database restored successfully.")
        return redirect("/settings/#data")

    elif vendor == "sqlite":
        if not filename.endswith(".db"):
            return HttpResponse("Upload a .db file for SQLite restore.", status=400, content_type="text/plain")

        db_path = str(db.get("NAME", ""))
        # Backup existing before overwrite
        if os.path.exists(db_path):
            shutil.copy2(db_path, db_path + ".pre-restore-backup")

        with open(db_path, "wb") as f:
            for chunk in uploaded.chunks():
                f.write(chunk)

        from django.contrib import messages
        messages.success(request, "Database restored. Previous database saved as .pre-restore-backup.")
        return redirect("/settings/#data")

    return HttpResponse("Unsupported database type.", status=500, content_type="text/plain")


# ── CSV import ───────────────────────────────────────────────────────────────

IMPORTABLE_MODELS = {
    "assets": {
        "label": "Assets",
        "required_headers": ["asset_id", "type", "status"],
        "optional_headers": ["make_model", "serial", "qty", "notes", "archived"],
    },
    "jobs": {
        "label": "Jobs",
        "required_headers": ["job_name", "start_date", "end_date"],
        "optional_headers": ["category", "notes"],
    },
}


@login_required
@require_POST
def csv_import_view(request):
    if not _is_admin(request.user):
        return redirect("/settings/")

    from django.contrib import messages

    model_key = request.POST.get("model", "").strip()
    uploaded = request.FILES.get("csv_file")
    action = request.POST.get("action", "preview")

    if model_key not in IMPORTABLE_MODELS or not uploaded:
        messages.error(request, "Select a model and upload a CSV file.")
        return redirect("/settings/#data")

    meta = IMPORTABLE_MODELS[model_key]

    try:
        decoded = uploaded.read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(decoded))
        rows = list(reader)
        headers = reader.fieldnames or []
    except Exception as e:
        messages.error(request, f"Could not read CSV: {e}")
        return redirect("/settings/#data")

    # Validate required headers
    missing = [h for h in meta["required_headers"] if h not in headers]
    if missing:
        messages.error(request, f"CSV is missing required columns: {', '.join(missing)}")
        return redirect("/settings/#data")

    if action == "preview":
        # Return JSON preview for the frontend
        from django.http import JsonResponse
        preview = rows[:20]
        return JsonResponse({
            "headers": headers,
            "rows": preview,
            "total": len(rows),
            "model": model_key,
            "model_label": meta["label"],
        })

    # action == "import"
    created = 0
    skipped = 0
    errors = []

    if model_key == "assets":
        TYPE_MAP = {label.lower(): value for value, label in Asset.AssetType.choices}
        STATUS_MAP = {label.lower(): value for value, label in Asset.Status.choices}
        for row in rows:
            asset_id = row.get("asset_id", "").strip().upper()
            if not asset_id:
                skipped += 1
                continue
            if Asset.objects.filter(asset_id=asset_id).exists():
                skipped += 1
                continue
            asset_type = TYPE_MAP.get(row.get("type", "").strip().lower(), "")
            status = STATUS_MAP.get(row.get("status", "").strip().lower(), Asset.Status.AVAILABLE)
            if not asset_type:
                errors.append(f"{asset_id}: unknown type '{row.get('type', '')}'")
                continue
            try:
                qty_val = int(row.get("qty", 1) or 1)
            except ValueError:
                qty_val = 1
            Asset.objects.create(
                asset_id=asset_id,
                asset_type=asset_type,
                make_model=row.get("make_model", "").strip(),
                serial=row.get("serial", "").strip().upper(),
                qty=qty_val,
                status=status,
                notes=row.get("notes", "").strip(),
                archived=row.get("archived", "").strip().lower() == "yes",
            )
            created += 1

    elif model_key == "jobs":
        CAT_MAP = {label.lower(): value for value, label in Job.Category.choices}
        for row in rows:
            name = row.get("job_name", "").strip()
            if not name:
                skipped += 1
                continue
            try:
                start = datetime.date.fromisoformat(row.get("start_date", "").strip())
                end = datetime.date.fromisoformat(row.get("end_date", "").strip())
            except ValueError:
                errors.append(f"'{name}': invalid dates")
                continue
            category = CAT_MAP.get(row.get("category", "").strip().lower(), Job.Category.TX)
            Job.objects.create(
                name=name, category=category, start_date=start, end_date=end,
                notes=row.get("notes", "").strip(),
            )
            created += 1

    result_msg = f"Import complete: {created} created, {skipped} skipped."
    if errors:
        result_msg += f" Errors: {'; '.join(errors[:5])}"
        messages.warning(request, result_msg)
    else:
        messages.success(request, result_msg)

    return redirect("/settings/#data")


@login_required
def db_info_view(request):
    """Return the database vendor so the frontend can label the backup button."""
    vendor, _ = _db_info()
    return HttpResponse(
        f'{{"vendor": "{vendor}"}}',
        content_type="application/json"
    )
