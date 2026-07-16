from django.contrib import admin

from ..models import Ticket, TicketHistory


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
