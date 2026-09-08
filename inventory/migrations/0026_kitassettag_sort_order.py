from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0025_ticket_photo"),
    ]

    operations = [
        migrations.AddField(
            model_name="kitassettag",
            name="sort_order",
            field=models.PositiveIntegerField(
                default=0,
                help_text="Manual display order within this kit. Lower values appear first.",
            ),
        ),
        migrations.AlterModelOptions(
            name="kitassettag",
            options={"ordering": ["kit", "sort_order", "created_at"]},
        ),
    ]
