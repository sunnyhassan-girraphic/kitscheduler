from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0022_backfill_kit_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="kitassettag",
            name="tag_2",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="kit_asset_tags_2",
                to="inventory.tag",
                help_text="Second tag for G3 engines (channel 2 output).",
            ),
        ),
    ]
