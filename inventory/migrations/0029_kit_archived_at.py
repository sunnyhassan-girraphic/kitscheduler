from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0028_dashboardwidget_column"),
    ]

    operations = [
        migrations.AddField(
            model_name="kit",
            name="archived_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text="Set automatically when the kit status is changed to Archived.",
            ),
        ),
    ]
