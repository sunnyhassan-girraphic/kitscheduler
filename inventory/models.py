import re
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class StaffMember(models.Model):
    name = models.CharField(max_length=120, unique=True)
    notes = models.TextField(blank=True)
    active = models.BooleanField(
        default=True,
        help_text="Uncheck instead of deleting once someone leaves, to keep their booking history.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Asset(models.Model):
    class AssetType(models.TextChoices):
        ENGINE = "ENGINE", "Engine"
        COMPONENT = "COMPONENT", "Component"
        STANDALONE = "STANDALONE", "Standalone"
        PERIPHERAL = "PERIPHERAL", "Peripheral"
        CABLE = "CABLE", "Cable"
        IO_DEVICE = "IO_DEVICE", "I/O Device"
        LICENSE = "LICENSE", "License"

    class Status(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Available"
        IN_USE = "IN_USE", "In use"
        NEEDS_REPAIR = "NEEDS_REPAIR", "Needs repair"
        MAINTENANCE = "MAINTENANCE", "Maintenance"
        MISSING = "MISSING", "Missing"

    asset_id = models.CharField(max_length=64, unique=True)
    asset_type = models.CharField(max_length=20, choices=AssetType.choices)
    make_model = models.CharField(max_length=120, blank=True)
    serial = models.CharField(max_length=120, blank=True)
    qty = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.AVAILABLE)
    archived = models.BooleanField(
        default=False,
        help_text="Archived assets are hidden from availability and pickers, but kept for booking history. Use this instead of deleting.",
    )
    notes = models.TextField(blank=True)
    license_type = models.CharField(max_length=60, blank=True)
    license_functionality = models.CharField(max_length=200, blank=True)
    parent_engine = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="nested_assets",
        limit_choices_to={"asset_type": AssetType.ENGINE},
        help_text="The Engine this item is physically installed in, if any.",
    )
    last_updated_by = models.ForeignKey(
        "StaffMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="updated_assets",
    )
    last_updated_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["asset_id"]

    def __str__(self):
        return f"{self.asset_id} ({self.get_asset_type_display()})"

    def clean(self):
        if self.parent_engine_id:
            if self.asset_type == self.AssetType.ENGINE:
                raise ValidationError("An Engine cannot be nested inside another Engine.")
            if self.parent_engine_id == self.pk:
                raise ValidationError("An asset cannot be its own parent.")
        if (self.license_type or self.license_functionality) and self.asset_type != self.AssetType.LICENSE:
            raise ValidationError("License fields can only be set on License assets.")

    @property
    def is_nested(self):
        return self.parent_engine_id is not None


class Kit(models.Model):
    name = models.CharField(max_length=120, unique=True)
    notes = models.TextField(blank=True)
    assets = models.ManyToManyField(
        Asset,
        blank=True,
        related_name="kits",
        help_text=(
            "Direct members of this kit - Engines and/or loose assets "
            "(including Licenses). Components nested inside a member Engine "
            "travel with it automatically and do not need to be added here separately."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def all_asset_ids(self):
        direct_ids = set(self.assets.values_list("id", flat=True))
        engine_ids = list(self.assets.filter(asset_type=Asset.AssetType.ENGINE).values_list("id", flat=True))
        nested_ids = set(Asset.objects.filter(parent_engine_id__in=engine_ids).values_list("id", flat=True))
        return direct_ids | nested_ids


class Job(models.Model):
    class Category(models.TextChoices):
        PREP = "PREP", "Prep"
        RIG = "RIG", "Rig"
        TX = "TX", "TX"
        WAREHOUSE = "WAREHOUSE", "Warehouse"
        TECH_DEVELOPMENT = "TECH_DEVELOPMENT", "Tech development"

    name = models.CharField(max_length=200)
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.TX)
    start_date = models.DateField()
    end_date = models.DateField()
    notes = models.TextField(blank=True)
    custom_color = models.CharField(max_length=7, blank=True)
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
        category_color = CategoryColor.objects.filter(category=self.category).first()
        if category_color:
            return category_color.color
        return "#EAB308"


class CategoryColor(models.Model):
    category = models.CharField(max_length=20, choices=Job.Category.choices, unique=True)
    color = models.CharField(max_length=7, default="#EAB308")

    class Meta:
        verbose_name = "Category color"
        verbose_name_plural = "Category colors"
        ordering = ["category"]

    def __str__(self):
        return f"{self.get_category_display()} -> {self.color}"

    def clean(self):
        if self.color and not re.fullmatch(r"#[0-9A-Fa-f]{6}", self.color):
            raise ValidationError({"color": "Enter a hex color like #EA9B08."})


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
        "Asset",
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
