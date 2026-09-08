from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0027_dashboardwidget"),
    ]

    operations = [
        migrations.AddField(
            model_name="dashboardwidget",
            name="column",
            field=models.CharField(
                max_length=10,
                choices=[("full", "Full width"), ("left", "Left column"), ("right", "Right column")],
                default="left",
            ),
        ),
        migrations.RunSQL(
            sql="""
                UPDATE inventory_dashboardwidget SET "column" = 'full'  WHERE widget_id = 'stats';
                UPDATE inventory_dashboardwidget SET "column" = 'left'  WHERE widget_id IN ('timeline', 'upcoming', 'attention');
                UPDATE inventory_dashboardwidget SET "column" = 'right' WHERE widget_id IN ('active_kits', 'stocktake', 'vans', 'activity');
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
