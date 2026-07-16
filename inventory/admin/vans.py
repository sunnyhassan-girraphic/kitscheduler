from django.contrib import admin

from ..models import Vehicle, VanLog


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ("name", "registration", "make_model", "active")
    list_filter = ("active",)
    search_fields = ("name", "registration", "make_model")


FIELDS_BY_TYPE = {
    VanLog.LogType.USAGE: (
        "driver", "purpose", "destination", "start_mileage", "end_mileage", "notes",
    ),
    VanLog.LogType.MAINTENANCE: (
        "description", "performed_by", "cost", "next_due_date",
    ),
    VanLog.LogType.CHECKLIST: (
        "checked_by", "items", "notes",
    ),
}


@admin.register(VanLog)
class VanLogAdmin(admin.ModelAdmin):
    list_display = ("vehicle", "date", "log_type", "summary")
    list_filter = ("log_type", "vehicle")
    date_hierarchy = "date"

    def get_fieldsets(self, request, obj=None):
        base = ("vehicle", "log_type", "date", "logged_by")
        # New/unsaved: show every field, since we don't know the type yet.
        # Editing an existing row: only show the fields that type actually uses.
        type_fields = FIELDS_BY_TYPE.get(obj.log_type, ()) if obj else sum(FIELDS_BY_TYPE.values(), ())
        return (
            (None, {"fields": base}),
            ("Details for this log type", {"fields": tuple(type_fields)}),
        )

    def summary(self, obj):
        return obj.summary()
    summary.short_description = "Summary"
