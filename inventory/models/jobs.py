import re
from django.core.exceptions import ValidationError
from django.db import models

from .assets import Asset
from .kits import Kit
from .staff import StaffMember


class Job(models.Model):
    class Category(models.TextChoices):
        PREP = "PREP", "Prep"
        RIG = "RIG", "Rig"
        TX = "TX", "TX"
        WAREHOUSE = "WAREHOUSE", "Warehouse"
        TECH_DEVELOPMENT = "TECH_DEVELOPMENT", "Tech development"
        PERMANENT_INSTALL = "PERMANENT_INSTALL", "Permanent Install"
        LONG_TERM_LOAN = "LONG_TERM_LOAN", "Long Term Loan"

    name = models.CharField(max_length=200)
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.TX)
    start_date = models.DateField()
    end_date = models.DateField()
    notes = models.TextField(blank=True)
    custom_color = models.CharField(max_length=7, blank=True)
    last_updated_by = models.ForeignKey(
        StaffMember, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+", help_text="Who made the last recorded update."
    )
    last_updated_date = models.DateField(null=True, blank=True)
    last_updated_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["start_date"]

    def __str__(self):
        return self.name

    def clean(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValidationError("End date cannot be before start date.")
        if self.custom_color and not re.fullmatch(r"#[0-9A-Fa-f]{6}", self.custom_color):
            raise ValidationError({"custom_color": "Enter a hex color like #EA9B08."})

    def resolve_color(self):
        if self.custom_color:
            return self.custom_color
        category_colour = CategoryColour.objects.filter(category=self.category).first()
        if category_colour:
            return category_colour.colour
        return "#EAB308"


class CategoryColour(models.Model):
    category = models.CharField(max_length=20, choices=Job.Category.choices, unique=True)
    colour = models.CharField(max_length=7, default="#EAB308")

    class Meta:
        verbose_name = "Category colour"
        verbose_name_plural = "Category colours"
        ordering = ["category"]

    def __str__(self):
        return f"{self.get_category_display()} -> {self.colour}"

    def clean(self):
        if self.colour and not re.fullmatch(r"#[0-9A-Fa-f]{6}", self.colour):
            raise ValidationError({"colour": "Enter a hex color like #EA9B08."})


class KitBooking(models.Model):
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="kit_bookings")
    kit = models.ForeignKey(Kit, on_delete=models.CASCADE, related_name="bookings")
    start_date = models.DateField()
    end_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["start_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["job", "kit", "start_date", "end_date"],
                name="unique_kit_booking_per_job_window",
            )
        ]

    def __str__(self):
        return f"{self.kit.name} -> {self.job.name} ({self.start_date} to {self.end_date})"

    def clean(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValidationError("End date cannot be before start date.")

    def overlaps(self, on_date):
        return self.start_date <= on_date <= self.end_date


class AssetBooking(models.Model):
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="asset_bookings")
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="direct_bookings")
    start_date = models.DateField()
    end_date = models.DateField()
    functionalities = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["start_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["job", "asset", "start_date", "end_date"],
                name="unique_asset_booking_per_job_window",
            )
        ]

    def __str__(self):
        return f"{self.asset.asset_id} -> {self.job.name} ({self.start_date} to {self.end_date})"

    def clean(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValidationError("End date cannot be before start date.")

    def overlaps(self, on_date):
        return self.start_date <= on_date <= self.end_date


class StaffBooking(models.Model):
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="staff_bookings")
    staff_member = models.ForeignKey(StaffMember, on_delete=models.CASCADE, related_name="bookings")
    start_date = models.DateField()
    end_date = models.DateField()
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["start_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["job", "staff_member", "start_date", "end_date"],
                name="unique_staff_booking_per_job_window",
            )
        ]

    def __str__(self):
        return f"{self.staff_member.name} -> {self.job.name} ({self.start_date} to {self.end_date})"

    def clean(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValidationError("End date cannot be before start date.")

    def overlaps(self, on_date):
        return self.start_date <= on_date <= self.end_date


JOB_HISTORY_FIELD_LABELS = {
    "name": "Job name",
    "category": "Category",
    "start_date": "Start date",
    "end_date": "End date",
    "notes": "Notes",
    "custom_color": "Custom colour",
    "last_updated_by": "Updated by",
    "last_updated_date": "Last updated date",
    "last_updated_notes": "What changed",
}

JOB_HISTORY_SCALAR_FIELDS = [
    "name", "category", "start_date", "end_date", "notes", "custom_color",
]


class JobHistory(models.Model):
    """Change log for the Job edit form - mirrors KitHistory/AssetHistory shape."""
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="history")
    changed_by = models.ForeignKey(
        StaffMember, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    field_changed = models.CharField(max_length=40)
    old_value = models.CharField(max_length=200, blank=True)
    new_value = models.CharField(max_length=200, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "Job history"

    def __str__(self):
        return f"{self.job.name} - {self.field_changed}"

    @property
    def field_label(self):
        return JOB_HISTORY_FIELD_LABELS.get(self.field_changed, self.field_changed.replace("_", " ").title())

    @staticmethod
    def filtered_for(job, mode="month", page=1, page_size=25):
        import datetime as _dt
        from django.core.paginator import Paginator
        qs = job.history.select_related("changed_by").order_by("-created_at")
        if mode == "week":
            qs = qs.filter(created_at__date__gte=_dt.date.today() - _dt.timedelta(days=7))
        elif mode == "month":
            qs = qs.filter(created_at__date__gte=_dt.date.today() - _dt.timedelta(days=30))
        return Paginator(qs, page_size).get_page(page)

    @staticmethod
    def record_scalar_changes(job, before_values, changed_by):
        for field in JOB_HISTORY_SCALAR_FIELDS:
            old_raw = before_values.get(field)
            new_raw = getattr(job, field)
            if str(old_raw) == str(new_raw):
                continue
            JobHistory.objects.create(
                job=job, changed_by=changed_by, field_changed=field,
                old_value=str(old_raw)[:200] if old_raw not in (None, "") else "(none)",
                new_value=str(new_raw)[:200] if new_raw not in (None, "") else "(none)",
            )

    @staticmethod
    def record_note(job, changed_by, before_note, after_note):
        after_note = (after_note or "").strip()
        if after_note and after_note != (before_note or ""):
            JobHistory.objects.create(
                job=job, changed_by=changed_by, field_changed="note", note=after_note,
            )
