from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from .staff import StaffMember

# Fixed bi-weekly safety checklist items. Edit this list to change what's
# checked - no migration needed, since results are stored as JSON.
VAN_CHECKLIST_ITEMS = [
    "Tyres & tyre pressure",
    "Lights (headlights, indicators, brake lights)",
    "Oil level",
    "Coolant level",
    "Windscreen, wipers & washer fluid",
    "Mirrors",
    "Brakes",
    "Seatbelts",
    "Fire extinguisher & first aid kit",
    "Fuel level",
    "Bodywork / damage check",
    "Documents present (insurance, MOT, tax)",
]


class Vehicle(models.Model):
    name = models.CharField(max_length=80, unique=True, help_text="e.g. 'Van 1' or a nickname.")
    registration = models.CharField(max_length=20, blank=True, help_text="Number plate.")
    make_model = models.CharField(max_length=120, blank=True)
    active = models.BooleanField(
        default=True,
        help_text="Uncheck instead of deleting once a van is sold/retired, to keep its history.",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Van"
        verbose_name_plural = "Vans"

    def __str__(self):
        return self.name

    def last_usage(self):
        return self.logs.filter(log_type=VanLog.LogType.USAGE).order_by("-date", "-id").first()

    def last_maintenance(self):
        return self.logs.filter(log_type=VanLog.LogType.MAINTENANCE).order_by("-date", "-id").first()

    def last_checklist(self):
        return self.logs.filter(log_type=VanLog.LogType.CHECKLIST).order_by("-date", "-id").first()


class VanLog(models.Model):
    """One row per dated event for one Van - a trip, a service, or a
    bi-weekly safety checklist. These used to be three separate tables
    (VanUsageLog, VanMaintenanceLog, VanChecklist); merged into one here
    since they're all fundamentally the same shape ('something happened to
    this van on this date') and having three near-identical tables in the
    admin was more confusing than having some blank columns on one.

    Only the fields for `log_type` are expected to be filled in on any
    given row - Usage rows use driver/purpose/destination/mileage,
    Maintenance rows use description/performed_by/cost/next_due_date,
    Checklist rows use checked_by/items. The admin form only shows the
    relevant fields for whichever type you're adding.
    """

    class LogType(models.TextChoices):
        USAGE = "USAGE", "Usage"
        MAINTENANCE = "MAINTENANCE", "Maintenance"
        CHECKLIST = "CHECKLIST", "Checklist"

    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name="logs")
    log_type = models.CharField(max_length=20, choices=LogType.choices)
    date = models.DateField()
    notes = models.TextField(blank=True)
    logged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    # Usage fields
    driver = models.ForeignKey(
        StaffMember, on_delete=models.SET_NULL, null=True, blank=True, related_name="van_trips"
    )
    purpose = models.CharField(max_length=200, blank=True, help_text="What the van was used for / job.")
    destination = models.CharField(max_length=200, blank=True)
    start_mileage = models.PositiveIntegerField(null=True, blank=True)
    end_mileage = models.PositiveIntegerField(null=True, blank=True)

    # Maintenance fields
    description = models.TextField(blank=True, help_text="What was done - service, repair, MOT, etc.")
    performed_by = models.CharField(max_length=120, blank=True, help_text="Garage or person who did the work.")
    cost = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    next_due_date = models.DateField(null=True, blank=True, help_text="Next service/MOT due, if known.")

    # Checklist fields
    checked_by = models.ForeignKey(
        StaffMember, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    items = models.JSONField(
        default=list, blank=True,
        help_text="List of {item, ok, note} results for VAN_CHECKLIST_ITEMS at time of submission.",
    )

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "Van log"
        verbose_name_plural = "Van logs"

    def __str__(self):
        return f"{self.vehicle.name} - {self.date} ({self.get_log_type_display()})"

    def clean(self):
        if self.start_mileage is not None and self.end_mileage is not None:
            if self.end_mileage < self.start_mileage:
                raise ValidationError("End mileage cannot be before start mileage.")

    @property
    def distance(self):
        if self.start_mileage is not None and self.end_mileage is not None:
            return self.end_mileage - self.start_mileage
        return None

    def issues_count(self):
        return sum(1 for i in self.items if not i.get("ok"))

    def summary(self):
        if self.log_type == self.LogType.MAINTENANCE:
            return self.description[:60]
        if self.log_type == self.LogType.CHECKLIST:
            issues = self.issues_count()
            return f"{issues} issue(s)" if issues else "All OK"
        return self.purpose[:60]
