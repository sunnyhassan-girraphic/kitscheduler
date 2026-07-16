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
