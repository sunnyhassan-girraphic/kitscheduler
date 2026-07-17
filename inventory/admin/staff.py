from django.contrib import admin

from ..models import StaffMember


@admin.register(StaffMember)
class StaffMemberAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "active", "notes")
    list_filter = ("active",)
    search_fields = ("name",)
    autocomplete_fields = ("user",)
