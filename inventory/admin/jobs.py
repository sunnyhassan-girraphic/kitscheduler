from django import forms
from django.contrib import admin
from django.utils.html import format_html

from ..models import AssetBooking, CategoryColour, Job, KitBooking, StaffBooking


class CustomColorField(forms.MultiWidget):
    def __init__(self, *args, **kwargs):
        widgets = [
            forms.CheckboxInput(attrs={"class": "color-override-checkbox"}),
            forms.TextInput(attrs={"type": "color", "class": "color-swatch-input"}),
        ]
        super().__init__(widgets, *args, **kwargs)

    def decompress(self, value):
        if value:
            return [True, value]
        return [False, "#EAB308"]

    def value_from_datadict(self, data, files, name):
        checkbox_val = data.get(f"{name}_0") == "on"
        color_val = data.get(f"{name}_1", "#EAB308")
        return [checkbox_val, color_val]


class CustomColorFormField(forms.MultiValueField):
    widget = CustomColorField

    def __init__(self, *args, **kwargs):
        fields = [forms.BooleanField(required=False), forms.CharField(required=False)]
        super().__init__(fields=fields, require_all_fields=False, *args, **kwargs)

    def compress(self, data_list):
        if data_list and data_list[0]:
            return data_list[1] if len(data_list) > 1 else ""
        return ""


class JobAdminForm(forms.ModelForm):
    custom_color = CustomColorFormField(
        required=False,
        label="Override color",
    )

    class Meta:
        model = Job
        fields = "__all__"


# These three are the Kit<->Job, Asset<->Job, and Staff<->Job many-to-many
# relationships - not separate "extra" tables. Each row is one booking:
# which kit/asset/staff member, on which job, for what date range. They're
# shown here as inline forms on the Job page (where they're actually
# created and edited day-to-day) instead of as their own top-level admin
# sections, so the admin menu isn't cluttered with "Kit bookings", "Asset
# bookings", "Staff bookings" alongside "Kits", "Assets", "Staff members".
class KitBookingInline(admin.TabularInline):
    model = KitBooking
    extra = 0
    autocomplete_fields = ("kit",)
    fields = ("kit", "start_date", "end_date")


class AssetBookingInline(admin.TabularInline):
    model = AssetBooking
    extra = 0
    autocomplete_fields = ("asset",)
    fields = ("asset", "start_date", "end_date", "functionalities")


class StaffBookingInline(admin.TabularInline):
    model = StaffBooking
    extra = 0
    autocomplete_fields = ("staff_member",)
    fields = ("staff_member", "start_date", "end_date", "notes")


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    form = JobAdminForm
    list_display = ("name", "category", "color_swatch", "start_date", "end_date")
    list_filter = ("category",)
    search_fields = ("name", "notes")
    date_hierarchy = "start_date"
    inlines = [KitBookingInline, AssetBookingInline, StaffBookingInline]

    def save_model(self, request, obj, form, change):
        if change:
            original = Job.objects.get(pk=obj.pk)
            dates_changed = (
                obj.start_date != original.start_date
                or obj.end_date != original.end_date
            )
            if dates_changed:
                updated = 0
                for related_name in ("kit_bookings", "staff_bookings", "asset_bookings"):
                    for booking in getattr(obj, related_name).all():
                        booking.start_date = obj.start_date
                        booking.end_date = obj.end_date
                        booking.save(update_fields=["start_date", "end_date"])
                        updated += 1
                self.message_user(
                    request,
                    f"Updated {updated} booking(s) to match the job's new dates "
                    f"({obj.start_date} \u2013 {obj.end_date}).",
                )
        super().save_model(request, obj, form, change)

    def color_swatch(self, obj):
        color = obj.resolve_color()
        return format_html(
            '<span style="display:inline-block; width:16px; height:16px; '
            'border-radius:3px; background:{}; vertical-align:middle;"></span>',
            color
        )
    color_swatch.short_description = "Color"


@admin.register(CategoryColour)
class CategoryColourAdmin(admin.ModelAdmin):
    list_display = ("category", "colour_swatch", "colour")

    def colour_swatch(self, obj):
        return format_html(
            '<span style="display:inline-block; width:16px; height:16px; '
            'border-radius:3px; background:{}; vertical-align:middle;"></span>',
            obj.colour
        )
    colour_swatch.short_description = ""
