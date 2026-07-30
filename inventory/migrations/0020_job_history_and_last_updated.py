from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0019_add_job_categories"),
    ]

    operations = [
        migrations.AddField(
            model_name="job",
            name="last_updated_by",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="+", to="inventory.staffmember",
                help_text="Who made the last recorded update.",
            ),
        ),
        migrations.AddField(
            model_name="job",
            name="last_updated_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="job",
            name="last_updated_notes",
            field=models.TextField(blank=True),
        ),
        migrations.CreateModel(
            name="JobHistory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("field_changed", models.CharField(max_length=40)),
                ("old_value", models.CharField(blank=True, max_length=200)),
                ("new_value", models.CharField(blank=True, max_length=200)),
                ("note", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "changed_by",
                    models.ForeignKey(
                        blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+", to="inventory.staffmember",
                    ),
                ),
                (
                    "job",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="history", to="inventory.job",
                    ),
                ),
            ],
            options={"verbose_name_plural": "Job history", "ordering": ["-created_at"]},
        ),
    ]
