from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0029_kit_archived_at"),
    ]

    operations = [
        # Rename existing vans widget rows to license_expiry
        migrations.RunSQL(
            sql="""
                UPDATE inventory_dashboardwidget
                SET widget_id = 'license_expiry'
                WHERE widget_id = 'vans';
            """,
            reverse_sql="""
                UPDATE inventory_dashboardwidget
                SET widget_id = 'vans'
                WHERE widget_id = 'license_expiry';
            """,
        ),
    ]
