# Sonnet Box was an AssetType choice left over from an earlier structure
# that was tried and explicitly reverted - Sonnet Boxes are meant to be
# categorised as I/O Devices, which they already correctly are for every
# real asset. Confirmed zero rows with asset_type='SONNET' before writing
# this, but the RunPython step below reassigns any to IO_DEVICE anyway as
# a safety net, rather than assuming the count is still zero by the time
# this actually runs against production.
#
# This is a genuine schema change even though 'choices' isn't enforced by
# a Postgres constraint on its own: it changes what values the app will
# accept/display going forward, and the FK limit_choices_to on
# parent_engine also referenced SONNET and needs to drop it too so admin's
# dropdown for "what can this be nested inside" doesn't offer a type that
# no longer exists.
#
# Verified against a real PostgreSQL database: seeded a handful of assets
# including one with asset_type='SONNET' nested inside an Engine, ran this
# migration forward, confirmed it was reassigned to IO_DEVICE with its
# parent_engine link intact, confirmed 'SONNET' no longer appears as a
# choice anywhere the app renders it, then ran the reverse migration
# (`migrate inventory 0011`) to confirm it re-adds the choice cleanly
# (the reassigned row stays IO_DEVICE on reverse - reassignment is
# intentionally one-way, matching "we don't use Sonnet Box as its own
# category anymore").
from django.db import migrations, models
import django.db.models.deletion


def reassign_sonnet_to_io_device(apps, schema_editor):
    Asset = apps.get_model("inventory", "Asset")
    Asset.objects.filter(asset_type="SONNET").update(asset_type="IO_DEVICE")


def noop_reverse(apps, schema_editor):
    # Deliberately not reassigning IO_DEVICE rows back to SONNET on reverse -
    # we don't have a way to tell which ones (if any) used to be SONNET,
    # and re-guessing would be worse than leaving them correctly categorised.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0011_rename_categorycolor_to_categorycolour"),
    ]

    operations = [
        migrations.RunPython(reassign_sonnet_to_io_device, noop_reverse),
        migrations.AlterField(
            model_name="asset",
            name="asset_type",
            field=models.CharField(
                max_length=20,
                choices=[
                    ("ENGINE", "Engine"),
                    ("COMPONENT", "Component"),
                    ("STANDALONE", "Standalone"),
                    ("PERIPHERAL", "Peripheral"),
                    ("CABLE", "Cable"),
                    ("IO_DEVICE", "I/O Device"),
                    ("LICENSE", "License"),
                ],
            ),
        ),
        migrations.AlterField(
            model_name="asset",
            name="parent_engine",
            field=models.ForeignKey(
                blank=True,
                help_text="The Engine or I/O Device this item is physically installed in, if any.",
                limit_choices_to={"asset_type__in": ("ENGINE", "IO_DEVICE")},
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="nested_assets",
                to="inventory.asset",
            ),
        ),
    ]
