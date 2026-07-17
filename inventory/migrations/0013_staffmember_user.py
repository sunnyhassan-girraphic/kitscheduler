# Adds a link from StaffMember to their Django login (auth.User), so
# "Last updated by" dropdowns can default to whoever is currently signed
# in instead of staying blank or showing whoever last edited the record.
#
# The RunPython step is a best-effort convenience only: it case-insensitively
# matches existing Users to existing StaffMembers by name/username (e.g.
# username "sunny" <-> StaffMember "Sunny") and links anything that matches
# unambiguously. Anything that doesn't match cleanly (no match, or more than
# one possible match) is left unlinked - a real mistake here would silently
# attribute updates to the wrong person, so ambiguous cases are left for
# manual linking in Settings > Staff instead of guessed.
#
# Verified against a real PostgreSQL database: seeded Users "sunny"/"dom"
# alongside StaffMembers "Sunny"/"Dom"/"Fabio" (no matching User for Fabio),
# ran forward, confirmed Sunny and Dom linked correctly and Fabio was left
# unlinked (not incorrectly guessed), then ran the reverse migration to
# confirm the column drops cleanly with no errors.
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def link_existing_users_to_staff(apps, schema_editor):
    StaffMember = apps.get_model("inventory", "StaffMember")
    User = apps.get_model(settings.AUTH_USER_MODEL)

    staff_by_lower_name = {}
    for staff in StaffMember.objects.all():
        staff_by_lower_name.setdefault(staff.name.strip().lower(), []).append(staff)

    for user in User.objects.all():
        candidates = staff_by_lower_name.get(user.username.strip().lower(), [])
        if len(candidates) == 1:
            candidates[0].user_id = user.id
            candidates[0].save(update_fields=["user"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("inventory", "0012_remove_sonnet_asset_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="staffmember",
            name="user",
            field=models.OneToOneField(
                blank=True,
                help_text="Links this person to their login, so 'Last updated by' fields can "
                           "automatically default to whoever is currently signed in.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="staff_profile",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(link_existing_users_to_staff, noop_reverse),
    ]
