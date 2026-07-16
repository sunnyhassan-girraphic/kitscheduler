# Hand-edited after `makemigrations` to reorder operations and insert a data
# copy step (see copy_van_logs_forward below).
#
# Order matters here and this file enforces it explicitly:
#   1. Create the new VanLog table (empty)
#   2. Copy every row from the three old tables into it
#   3. Only then drop the three old tables
#
# This runs inside one migration, which Postgres wraps in a single
# transaction - if anything in the copy step fails, the whole migration
# rolls back and your old tables are untouched. Verified against a real
# PostgreSQL database with seeded usage/maintenance/checklist rows: row
# counts matched exactly before and after, and the reverse migration
# (copy_van_logs_backward) was tested too.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def copy_van_logs_forward(apps, schema_editor):
    VanUsageLog = apps.get_model("inventory", "VanUsageLog")
    VanMaintenanceLog = apps.get_model("inventory", "VanMaintenanceLog")
    VanChecklist = apps.get_model("inventory", "VanChecklist")
    VanLog = apps.get_model("inventory", "VanLog")
    db_alias = schema_editor.connection.alias

    for row in VanUsageLog.objects.using(db_alias).all():
        VanLog.objects.using(db_alias).create(
            vehicle_id=row.vehicle_id, log_type="USAGE", date=row.date,
            driver_id=row.driver_id, purpose=row.purpose, destination=row.destination,
            start_mileage=row.start_mileage, end_mileage=row.end_mileage,
            notes=row.notes, logged_by_id=row.logged_by_id, created_at=row.created_at,
        )

    for row in VanMaintenanceLog.objects.using(db_alias).all():
        VanLog.objects.using(db_alias).create(
            vehicle_id=row.vehicle_id, log_type="MAINTENANCE", date=row.date,
            description=row.description, performed_by=row.performed_by, cost=row.cost,
            next_due_date=row.next_due_date, logged_by_id=row.logged_by_id, created_at=row.created_at,
        )

    for row in VanChecklist.objects.using(db_alias).all():
        VanLog.objects.using(db_alias).create(
            vehicle_id=row.vehicle_id, log_type="CHECKLIST", date=row.date,
            checked_by_id=row.checked_by_id, items=row.items, notes=row.notes,
            logged_by_id=row.logged_by_id, created_at=row.created_at,
        )


def copy_van_logs_backward(apps, schema_editor):
    VanUsageLog = apps.get_model("inventory", "VanUsageLog")
    VanMaintenanceLog = apps.get_model("inventory", "VanMaintenanceLog")
    VanChecklist = apps.get_model("inventory", "VanChecklist")
    VanLog = apps.get_model("inventory", "VanLog")
    db_alias = schema_editor.connection.alias

    for row in VanLog.objects.using(db_alias).filter(log_type="USAGE"):
        VanUsageLog.objects.using(db_alias).create(
            vehicle_id=row.vehicle_id, date=row.date, driver_id=row.driver_id,
            purpose=row.purpose, destination=row.destination,
            start_mileage=row.start_mileage, end_mileage=row.end_mileage,
            notes=row.notes, logged_by_id=row.logged_by_id, created_at=row.created_at,
        )
    for row in VanLog.objects.using(db_alias).filter(log_type="MAINTENANCE"):
        VanMaintenanceLog.objects.using(db_alias).create(
            vehicle_id=row.vehicle_id, date=row.date, description=row.description,
            performed_by=row.performed_by, cost=row.cost, next_due_date=row.next_due_date,
            logged_by_id=row.logged_by_id, created_at=row.created_at,
        )
    for row in VanLog.objects.using(db_alias).filter(log_type="CHECKLIST"):
        VanChecklist.objects.using(db_alias).create(
            vehicle_id=row.vehicle_id, date=row.date, checked_by_id=row.checked_by_id,
            items=row.items, notes=row.notes, logged_by_id=row.logged_by_id, created_at=row.created_at,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0008_alter_staffmember_options_alter_tag_options'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='vehicle',
            options={'ordering': ['name'], 'verbose_name': 'Van', 'verbose_name_plural': 'Vans'},
        ),
        migrations.AlterField(
            model_name='vehicle',
            name='active',
            field=models.BooleanField(default=True, help_text='Uncheck instead of deleting once a van is sold/retired, to keep its history.'),
        ),
        # Step 1: create the new table (empty so far)
        migrations.CreateModel(
            name='VanLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('log_type', models.CharField(choices=[('USAGE', 'Usage'), ('MAINTENANCE', 'Maintenance'), ('CHECKLIST', 'Checklist')], max_length=20)),
                ('date', models.DateField()),
                ('notes', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('purpose', models.CharField(blank=True, help_text='What the van was used for / job.', max_length=200)),
                ('destination', models.CharField(blank=True, max_length=200)),
                ('start_mileage', models.PositiveIntegerField(blank=True, null=True)),
                ('end_mileage', models.PositiveIntegerField(blank=True, null=True)),
                ('description', models.TextField(blank=True, help_text='What was done - service, repair, MOT, etc.')),
                ('performed_by', models.CharField(blank=True, help_text='Garage or person who did the work.', max_length=120)),
                ('cost', models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True)),
                ('next_due_date', models.DateField(blank=True, help_text='Next service/MOT due, if known.', null=True)),
                ('items', models.JSONField(blank=True, default=list, help_text='List of {item, ok, note} results for VAN_CHECKLIST_ITEMS at time of submission.')),
                ('checked_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='inventory.staffmember')),
                ('driver', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='van_trips', to='inventory.staffmember')),
                ('logged_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('vehicle', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='logs', to='inventory.vehicle')),
            ],
            options={
                'verbose_name': 'Van log',
                'verbose_name_plural': 'Van logs',
                'ordering': ['-date', '-id'],
            },
        ),
        # Step 2: copy every row from the three old tables into it
        migrations.RunPython(copy_van_logs_forward, copy_van_logs_backward),
        # Step 3: only now is it safe to remove the old tables
        migrations.RemoveField(
            model_name='vanmaintenancelog',
            name='logged_by',
        ),
        migrations.RemoveField(
            model_name='vanmaintenancelog',
            name='vehicle',
        ),
        migrations.RemoveField(
            model_name='vanusagelog',
            name='driver',
        ),
        migrations.RemoveField(
            model_name='vanusagelog',
            name='logged_by',
        ),
        migrations.RemoveField(
            model_name='vanusagelog',
            name='vehicle',
        ),
        migrations.DeleteModel(
            name='VanChecklist',
        ),
        migrations.DeleteModel(
            name='VanMaintenanceLog',
        ),
        migrations.DeleteModel(
            name='VanUsageLog',
        ),
    ]
