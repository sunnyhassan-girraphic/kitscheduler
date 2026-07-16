from django.contrib import admin
from django.utils.html import format_html

from ..models import Asset, Kit, KitAssetTag, Tag


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
