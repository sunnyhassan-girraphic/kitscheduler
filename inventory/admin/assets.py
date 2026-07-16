import datetime

from django import forms
from django.contrib import admin
from django.db import models
from django.utils.html import format_html
from django.utils import dateformat

from ..models import Asset, LicenseFunctionality


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
                    "license_duration_start", "license_duration_end", "viz_ticket",
                ),
                "description": (
                    "License only. Type is a single tick, Functionality supports "
                    "multiple ticks. Duration is the overall active/expiry window - "
                    "it can still be assigned to individual jobs for shorter stretches "
                    "within that window via booking. Viz Ticket is the reference Viz "
                    "gave us to authorize the current Duration."
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
            "license_duration_start", "license_duration_end", "viz_ticket", "parent_engine",
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


@admin.register(LicenseFunctionality)
class LicenseFunctionalityAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)
