import datetime

from django import forms
from django.contrib import admin, messages
from django.db import models
from django.utils.html import format_html
from django.utils import dateformat

from .models import (
    Asset, AssetBooking, CategoryColor, Job, Kit, KitAssetTag,
    KitBooking, LicenseFunctionality, StaffBooking, StaffMember, Tag,
    Ticket, TicketHistory, Vehicle, VanUsageLog, VanMaintenanceLog, VanChecklist,
)


class ComponentPickerField(forms.ModelMultipleChoiceField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("queryset", Asset.objects.none())
        kwargs.setdefault("required", False)
        kwargs.setdefault("label", "Components")
        kwargs.setdefault(
            "help_text",
            "Pick from existing, unassigned assets to install them in this engine. "
            "To add a component that doesn't exist as an asset yet, create it first "
            "in the main Asset list, then come back here and select it.",
        )
        kwargs.setdefault(
            "widget", admin.widgets.FilteredSelectMultiple("components", is_stacked=False)
        )
        super().__init__(*args, **kwargs)

    def label_from_instance(self, obj):
        label = obj.asset_id
        if obj.make_model:
            label += f" - {obj.make_model}"
        return label


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = (
        "asset_id", "asset_type", "parent_link", "make_model",
        "serial", "qty", "status_badge", "archived", "last_updated_display",
    )
    list_filter = ("asset_type", "status", "archived", "license_type", "functionalities")
    search_fields = ("asset_id", "make_model", "serial", "notes", "license_functionality")
    autocomplete_fields = ("parent_engine",)
    filter_horizontal = ("functionalities",)
    list_select_related = ("parent_engine", "last_updated_by")

    fieldsets = (
        (None, {"fields": ("asset_id", "asset_type", "status", "archived")}),
        ("Details", {"fields": ("make_model", "serial", "qty", "notes")}),
        (
            "License details",
            {
                "fields": (
                    "license_type", "functionalities",
                    "license_duration_start", "license_duration_end",
                ),
                "description": (
                    "License only. Type is a single tick, Functionality supports "
                    "multiple ticks. Duration is the overall active/expiry window - "
                    "it can still be assigned to individual jobs for shorter stretches "
                    "within that window via booking."
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Nesting",
            {"fields": ("parent_engine",)},
        ),
        (
            "Last updated",
            {"fields": ("last_updated_by", "last_updated_date", "last_updated_notes")},
        ),
    )

    def get_form(self, request, obj=None, **kwargs):
        is_container = obj is not None and obj.asset_type in Asset.CONTAINER_TYPES

        if not is_container:
            form = super().get_form(request, obj, **kwargs)
            form = self._with_license_widgets(form)
            return self._with_default_update_date(form, obj)

        from django.forms import modelform_factory
        from django.contrib.admin.widgets import AdminDateWidget

        real_fields = [
            "asset_id", "asset_type", "status", "archived", "make_model", "serial",
            "qty", "notes", "license_type", "functionalities",
            "license_duration_start", "license_duration_end", "parent_engine",
            "last_updated_by", "last_updated_date", "last_updated_notes",
        ]
        base_form = modelform_factory(
            Asset, form=forms.ModelForm, fields=real_fields,
            widgets={
                "last_updated_date": AdminDateWidget,
                "license_duration_start": AdminDateWidget,
                "license_duration_end": AdminDateWidget,
                "license_type": forms.RadioSelect,
                "functionalities": forms.CheckboxSelectMultiple,
            },
        )
        container = obj

        class ContainerAssetForm(base_form):
            components = ComponentPickerField()

            def __init__(self_inner, *a, **kw):
                super().__init__(*a, **kw)
                excluded_types = (
                    [Asset.AssetType.ENGINE] if obj.asset_type == Asset.AssetType.ENGINE
                    else list(Asset.CONTAINER_TYPES)
                )
                self_inner.fields["components"].queryset = Asset.objects.filter(
                    archived=False,
                ).exclude(
                    asset_type__in=excluded_types,
                ).filter(
                    models.Q(parent_engine__isnull=True) | models.Q(parent_engine=container)
                ).order_by("asset_type", "asset_id")
                if container:
                    self_inner.fields["components"].initial = container.nested_assets.values_list("pk", flat=True)

            def save(self_inner, commit=True):
                instance = super().save(commit=commit)
                selected = set(self_inner.cleaned_data.get("components") or [])
                currently_nested = set(instance.nested_assets.all())
                for asset in selected - currently_nested:
                    asset.parent_engine = instance
                    asset.save(update_fields=["parent_engine"])
                for asset in currently_nested - selected:
                    asset.parent_engine = None
                    asset.save(update_fields=["parent_engine"])
                return instance

        return self._with_default_update_date(ContainerAssetForm, obj)

    @staticmethod
    def _with_license_widgets(form_class):
        if "license_type" in form_class.base_fields:
            form_class.base_fields["license_type"].widget = forms.RadioSelect(
                choices=form_class.base_fields["license_type"].choices
            )
        if "functionalities" in form_class.base_fields:
            form_class.base_fields["functionalities"].widget = forms.CheckboxSelectMultiple()
        return form_class

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        is_container = obj is not None and obj.asset_type in Asset.CONTAINER_TYPES
        if not is_container:
            return fieldsets
        components_section = (
            "Components",
            {"fields": ("components",)},
        )
        adjusted = list(fieldsets)
        adjusted.insert(2, components_section)
        return adjusted

    @staticmethod
    def _with_default_update_date(form_class, obj):
        if obj is not None and obj.last_updated_date:
            return form_class

        class FormWithDefaultDate(form_class):
            def __init__(self_inner, *a, **kw):
                super().__init__(*a, **kw)
                if "last_updated_date" in self_inner.fields and not self_inner.initial.get("last_updated_date"):
                    self_inner.initial["last_updated_date"] = datetime.date.today()

        return FormWithDefaultDate

    def status_badge(self, obj):
        colors = {
            "AVAILABLE": "#3fb950",
            "IN_USE": "#58a6ff",
            "NEEDS_REPAIR": "#f85149",
            "MAINTENANCE": "#EAB308",
            "MISSING": "#f85149",
        }
        color = colors.get(obj.status, "#888")
        return format_html(
            '<span style="color:{}; font-weight:600;">{}</span>',
            color, obj.get_status_display()
        )
    status_badge.short_description = "Status"

    def parent_link(self, obj):
        if obj.parent_engine_id:
            return format_html("in {}", obj.parent_engine.asset_id)
        return "-"
    parent_link.short_description = "Parent engine"

    def last_updated_display(self, obj):
        if obj.last_updated_by_id and obj.last_updated_date:
            return format_html(
                "{} on {}", obj.last_updated_by.name,
                dateformat.format(obj.last_updated_date, "j M Y")
            )
        if obj.last_updated_by_id:
            return obj.last_updated_by.name
        if obj.last_updated_date:
            return dateformat.format(obj.last_updated_date, "j M Y")
        return "-"
    last_updated_display.short_description = "Last updated"


@admin.register(StaffMember)
class StaffMemberAdmin(admin.ModelAdmin):
    list_display = ("name", "active", "notes")
    list_filter = ("active",)
    search_fields = ("name",)


class KitAssetTagInline(admin.TabularInline):
    model = KitAssetTag
    extra = 1
    autocomplete_fields = ("asset",)
    fields = ("asset", "tag")


@admin.register(Kit)
class KitAdmin(admin.ModelAdmin):
    list_display = ("name", "member_count", "nested_count")
    search_fields = ("name",)
    inlines = [KitAssetTagInline]

    def member_count(self, obj):
        return obj.assets.count()
    member_count.short_description = "Members"

    def nested_count(self, obj):
        container_ids = obj.assets.filter(
            asset_type__in=Asset.CONTAINER_TYPES
        ).values_list("id", flat=True)
        return Asset.objects.filter(parent_engine_id__in=container_ids).count()
    nested_count.short_description = "Nested"


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name", "color_swatch")
    search_fields = ("name",)

    def color_swatch(self, obj):
        if not obj.color:
            return "-"
        return format_html(
            '<span style="display:inline-block; width:16px; height:16px; '
            'border-radius:3px; background:{}; vertical-align:middle;"></span> {}',
            obj.color, obj.color,
        )
    color_swatch.short_description = "Color"


@admin.register(LicenseFunctionality)
class LicenseFunctionalityAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


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


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    form = JobAdminForm
    list_display = ("name", "category", "color_swatch", "start_date", "end_date")
    list_filter = ("category",)
    search_fields = ("name", "notes")
    date_hierarchy = "start_date"

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


@admin.register(CategoryColor)
class CategoryColorAdmin(admin.ModelAdmin):
    list_display = ("category", "color_swatch", "color")

    def color_swatch(self, obj):
        return format_html(
            '<span style="display:inline-block; width:16px; height:16px; '
            'border-radius:3px; background:{}; vertical-align:middle;"></span>',
            obj.color
        )
    color_swatch.short_description = ""


@admin.register(KitBooking)
class KitBookingAdmin(admin.ModelAdmin):
    list_display = ("kit", "job", "start_date", "end_date")
    list_filter = ("kit", "job")
    date_hierarchy = "start_date"


@admin.register(AssetBooking)
class AssetBookingAdmin(admin.ModelAdmin):
    list_display = ("asset", "job", "start_date", "end_date", "functionalities")
    list_filter = ("asset__asset_type",)
    date_hierarchy = "start_date"


@admin.register(StaffBooking)
class StaffBookingAdmin(admin.ModelAdmin):
    list_display = ("staff_member", "job", "start_date", "end_date")
    list_filter = ("staff_member",)
    date_hierarchy = "start_date"


class TicketHistoryInline(admin.TabularInline):
    model = TicketHistory
    extra = 0
    readonly_fields = ("changed_by", "field_changed", "old_value", "new_value", "note", "created_at")
    can_delete = False
    ordering = ("created_at",)


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ("id", "asset", "reporter_name", "status", "priority", "created_at")
    list_filter = ("status", "priority")
    search_fields = ("reporter_name", "reporter_contact", "description", "asset__asset_id")
    date_hierarchy = "created_at"
    inlines = [TicketHistoryInline]

    def save_model(self, request, obj, form, change):
        if change:
            original = Ticket.objects.get(pk=obj.pk)
            if original.status != obj.status:
                TicketHistory.objects.create(
                    ticket=obj, changed_by=request.user, field_changed="status",
                    old_value=original.get_status_display(), new_value=obj.get_status_display(),
                )
            if original.priority != obj.priority:
                TicketHistory.objects.create(
                    ticket=obj, changed_by=request.user, field_changed="priority",
                    old_value=original.get_priority_display(), new_value=obj.get_priority_display(),
                )
        super().save_model(request, obj, form, change)


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ("name", "registration", "make_model", "active")
    list_filter = ("active",)
    search_fields = ("name", "registration", "make_model")


@admin.register(VanUsageLog)
class VanUsageLogAdmin(admin.ModelAdmin):
    list_display = ("vehicle", "date", "driver", "purpose")
    list_filter = ("vehicle",)
    date_hierarchy = "date"


@admin.register(VanMaintenanceLog)
class VanMaintenanceLogAdmin(admin.ModelAdmin):
    list_display = ("vehicle", "date", "description", "cost", "next_due_date")
    list_filter = ("vehicle",)
    date_hierarchy = "date"


@admin.register(VanChecklist)
class VanChecklistAdmin(admin.ModelAdmin):
    list_display = ("vehicle", "date", "checked_by", "issues_count")
    list_filter = ("vehicle",)
    date_hierarchy = "date"
