import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0026_kitassettag_sort_order"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="DashboardWidget",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("widget_id", models.CharField(
                    max_length=40,
                    choices=[
                        ("stats",       "Stats Strip"),
                        ("timeline",    "Mini Timeline"),
                        ("upcoming",    "Upcoming Jobs"),
                        ("attention",   "Needs Attention"),
                        ("active_kits", "Active Kits"),
                        ("stocktake",   "Stock Take Health"),
                        ("vans",        "Van Status"),
                        ("activity",    "Recent Activity"),
                    ],
                )),
                ("position", models.PositiveSmallIntegerField(default=0)),
                ("visible",  models.BooleanField(default=True)),
                ("user", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="dashboard_widgets",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={"ordering": ["position"]},
        ),
        migrations.AddConstraint(
            model_name="dashboardwidget",
            constraint=models.UniqueConstraint(
                fields=["user", "widget_id"],
                name="unique_widget_per_user",
            ),
        ),
    ]
