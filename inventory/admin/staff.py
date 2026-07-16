from django.contrib import admin

from ..models import StaffMember


@admin.register(StaffMember)
class StaffMemberAdmin(admin.ModelAdmin):
    list_display = ("name", "active", "notes")
    list_filter = ("active",)
    search_fields = ("name",)
