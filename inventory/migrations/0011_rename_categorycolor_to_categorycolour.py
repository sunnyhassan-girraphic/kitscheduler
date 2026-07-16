# Hand-written (not via makemigrations, which wants an interactive y/n
# prompt to confirm a rename vs delete+create - writing it directly here
# guarantees Django treats this as a real rename, not a drop-and-recreate
# that would lose data).
#
# This is a genuine schema change: RenameModel below issues a real
# `ALTER TABLE inventory_categorycolor RENAME TO inventory_categorycolour`,
# and RenameField issues a real `ALTER TABLE ... RENAME COLUMN color TO
# colour`. Existing rows and their data are preserved by Postgres's rename
# operations - nothing is copied, dropped, or recreated.
#
# Verified against a real PostgreSQL database: seeded rows before running
# this, confirmed the same row count and same data present under the new
# table/column names afterward, and confirmed the reverse migration
# (`migrate inventory 0010`) renames everything back correctly too.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0010_alter_categorycolor_options_and_more'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='CategoryColor',
            new_name='CategoryColour',
        ),
        migrations.RenameField(
            model_name='categorycolour',
            old_name='color',
            new_name='colour',
        ),
    ]
