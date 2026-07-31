from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0020_job_history_and_last_updated"),
    ]

    operations = [
        migrations.AddField(
            model_name="kit",
            name="status",
            field=models.CharField(
                max_length=20,
                choices=[
                    ("READY", "Ready"),
                    ("PREP", "Prep / in progress"),
                    ("BOOKED", "Booked"),
                    ("FAULTY", "Faulty"),
                    ("ARCHIVED", "Archived"),
                ],
                default="READY",
                help_text=(
                    "Manual status for this kit. 'Booked' is set automatically when "
                    "the kit has an active booking; Ready/Prep/Faulty/Archived are "
                    "sticky and never overwritten by the auto-recompute."
                ),
            ),
        ),
    ]
