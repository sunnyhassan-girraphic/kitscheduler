import datetime
from django.db import models
from .assets import Asset
from .staff import StaffMember


class StockTakeSession(models.Model):
    class Status(models.TextChoices):
        IN_PROGRESS = "IN_PROGRESS", "In progress"
        COMPLETE = "COMPLETE", "Complete"

    label = models.CharField(
        max_length=120, blank=True,
        help_text="Optional name for this session, e.g. 'August warehouse check'.",
    )
    asset_types = models.JSONField(
        default=list,
        help_text="List of AssetType values included in this session, e.g. ['PERIPHERAL', 'CABLE'].",
    )
    staleness_days = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Only include assets not updated in this many days. Null = all assets.",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.IN_PROGRESS)
    created_by = models.ForeignKey(
        StaffMember, null=True, blank=True, on_delete=models.SET_NULL, related_name="stock_takes",
    )
    started_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        label = self.label or f"Stock take #{self.pk}"
        return f"{label} ({self.get_status_display()})"

    @property
    def display_label(self):
        return self.label or f"Stock take #{self.pk}"

    @property
    def total_count(self):
        return self.entries.count()

    @property
    def confirmed_count(self):
        return self.entries.filter(outcome=StockTakeEntry.Outcome.CONFIRMED).count()

    @property
    def flagged_count(self):
        return self.entries.filter(outcome=StockTakeEntry.Outcome.FLAGGED).count()

    @property
    def skipped_count(self):
        return self.entries.filter(outcome=StockTakeEntry.Outcome.SKIPPED).count()

    @property
    def pending_count(self):
        return self.entries.filter(outcome=StockTakeEntry.Outcome.PENDING).count()

    @property
    def reviewed_count(self):
        return self.entries.exclude(outcome=StockTakeEntry.Outcome.PENDING).count()

    def asset_types_display(self):
        labels = dict(Asset.AssetType.choices)
        return ", ".join(labels.get(t, t) for t in self.asset_types) or "All types"

    def staleness_display(self):
        if not self.staleness_days:
            return "All assets"
        return f"Not seen in {self.staleness_days}+ days"


class StockTakeEntry(models.Model):
    class Outcome(models.TextChoices):
        PENDING = "PENDING", "Pending"
        CONFIRMED = "CONFIRMED", "Confirmed"
        FLAGGED = "FLAGGED", "Flagged"
        SKIPPED = "SKIPPED", "Skipped"

    session = models.ForeignKey(StockTakeSession, on_delete=models.CASCADE, related_name="entries")
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="stock_take_entries")
    outcome = models.CharField(max_length=20, choices=Outcome.choices, default=Outcome.PENDING)
    notes = models.CharField(max_length=300, blank=True)
    # Snapshot of the asset's last_updated_date at session start - for the session summary
    last_updated_snapshot = models.DateField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        StaffMember, null=True, blank=True, on_delete=models.SET_NULL, related_name="stock_take_reviews",
    )

    class Meta:
        ordering = ["asset__last_updated_date", "asset__asset_id"]
        unique_together = [("session", "asset")]

    def __str__(self):
        return f"{self.asset.asset_id} in {self.session}"
