from django.conf import settings
from django.db import models

from .assets import Asset


class Ticket(models.Model):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        IN_PROGRESS = "IN_PROGRESS", "In progress"
        RESOLVED = "RESOLVED", "Resolved"
        CLOSED = "CLOSED", "Closed"

    class Priority(models.TextChoices):
        LOW = "LOW", "Low"
        MEDIUM = "MEDIUM", "Medium"
        HIGH = "HIGH", "High"
        URGENT = "URGENT", "Urgent"

    asset = models.ForeignKey(
        Asset,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tickets",
        help_text="The faulty or problem asset this ticket is about.",
    )
    reporter_name = models.CharField(max_length=120)
    reporter_contact = models.CharField(
        max_length=120, blank=True,
        help_text="Optional - email or phone, in case follow-up is needed.",
    )
    description = models.TextField(help_text="What's wrong with it?")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    priority = models.CharField(max_length=20, choices=Priority.choices, default=Priority.MEDIUM)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        asset_label = self.asset.asset_id if self.asset_id else "No asset"
        return f"#{self.id} - {asset_label} ({self.get_status_display()})"


class TicketHistory(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="history")
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Blank means it was the original public submission.",
    )
    field_changed = models.CharField(
        max_length=40,
        help_text="e.g. 'created', 'status', 'priority', 'comment'.",
    )
    old_value = models.CharField(max_length=200, blank=True)
    new_value = models.CharField(max_length=200, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name_plural = "Ticket history"

    def __str__(self):
        who = self.changed_by.get_username() if self.changed_by_id else "Public submission"
        return f"Ticket #{self.ticket_id} - {self.field_changed} by {who}"
