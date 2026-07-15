from django.db import migrations


DEFAULT_TAGS = ["MAIN", "BACKUP", "SPARE", "TEST"]

DEFAULT_FUNCTIONALITIES = [
    "SDI In", "SDI Out", "HDMI Out", "NDI", "Streaming",
    "Ingest", "Graphics", "Unreal Render Blade", "Viz",
]

# Best-effort mapping from old free-text license_type values to the new
# fixed choice set. Anything not recognised is left blank rather than guessed.
LICENSE_TYPE_MAP = {
    "permanent": "PERMANENT",
    "perm": "PERMANENT",
    "network": "NETWORK",
    "floating": "NETWORK",
    "dongle": "DONGLE",
    "hasp": "DONGLE",
    "software": "SOFTWARE",
    "subscription": "SOFTWARE",
}


def table_exists(schema_editor, table_name):
    return table_name in schema_editor.connection.introspection.table_names()


def seed_and_migrate(apps, schema_editor):
    Tag = apps.get_model("inventory", "Tag")
    LicenseFunctionality = apps.get_model("inventory", "LicenseFunctionality")
    Asset = apps.get_model("inventory", "Asset")
    KitAssetTag = apps.get_model("inventory", "KitAssetTag")

    for name in DEFAULT_TAGS:
        Tag.objects.get_or_create(name=name)

    func_by_lower = {}
    for name in DEFAULT_FUNCTIONALITIES:
        obj, _ = LicenseFunctionality.objects.get_or_create(name=name)
        func_by_lower[name.lower()] = obj

    # Carry over rows from the old implicit Kit<->Asset M2M table (which
    # existed before `assets` gained a `through` model) into KitAssetTag,
    # so nobody's existing kits lose their members on upgrade.
    old_table = "inventory_kit_assets"
    if table_exists(schema_editor, old_table):
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(f"SELECT kit_id, asset_id FROM {old_table}")
            rows = cursor.fetchall()
        for kit_id, asset_id in rows:
            KitAssetTag.objects.get_or_create(kit_id=kit_id, asset_id=asset_id, defaults={"tag_id": None})

    # Migrate old free-text license fields onto the new structured ones.
    for asset in Asset.objects.filter(asset_type="LICENSE"):
        changed = False
        raw_type = (asset.license_type or "").strip().lower()
        if raw_type and raw_type in LICENSE_TYPE_MAP:
            asset.license_type = LICENSE_TYPE_MAP[raw_type]
            changed = True
        elif raw_type and raw_type.upper() not in ("PERMANENT", "NETWORK", "DONGLE", "SOFTWARE"):
            # Unrecognised free text - clear it so the field holds a valid
            # choice; the original text is still visible via notes/history.
            asset.license_type = ""
            changed = True
        if changed:
            asset.save(update_fields=["license_type"])

        if asset.license_functionality:
            names = [n.strip() for n in asset.license_functionality.split(",") if n.strip()]
            for n in names:
                func = func_by_lower.get(n.lower())
                if not func:
                    func, _ = LicenseFunctionality.objects.get_or_create(name=n)
                    func_by_lower[n.lower()] = func
                asset.functionalities.add(func)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0005_licensefunctionality_tag_asset_license_duration_end_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_and_migrate, noop),
    ]
