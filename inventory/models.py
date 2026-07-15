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


class LicenseFunctionality(models.Model):
    """Bank of tickable License functionalities (e.g. SDI Out, Unreal Render
    Blade, Viz), editable from Settings instead of typed freehand."""
    name = models.CharField(max_length=80, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "License functionality"
        verbose_name_plural = "License functionalities"

    def __str__(self):
        return self.name


class Asset(models.Model):
    class AssetType(models.TextChoices):
        ENGINE = "ENGINE", "Engine"
        SONNET = "SONNET", "Sonnet Box"
        COMPONENT = "COMPONENT", "Component"
        STANDALONE = "STANDALONE", "Standalone"
        PERIPHERAL = "PERIPHERAL", "Peripheral"
        CABLE = "CABLE", "Cable"
        IO_DEVICE = "IO_DEVICE", "I/O Device"
        LICENSE = "LICENSE", "License"

    # Types that other assets can be physically nested inside of.
    # Types that other assets can be physically nested inside of. ENGINE is
    # the top-level container; SONNET and IO_DEVICE are containers that can
    # themselves nest inside an Engine (e.g. a Sonnet Box or an I/O device
    # chassis holding a GPU, which then sits inside the Engine).
    NESTABLE_CONTAINER_TYPES = (AssetType.SONNET, AssetType.IO_DEVICE)
    CONTAINER_TYPES = (AssetType.ENGINE,) + NESTABLE_CONTAINER_TYPES

    class Status(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Available"
        IN_USE = "IN_USE", "In use"
        NEEDS_REPAIR = "NEEDS_REPAIR", "Needs repair"
        MAINTENANCE = "MAINTENANCE", "Maintenance"
        MISSING = "MISSING", "Missing"

    class LicenseType(models.TextChoices):
        PERMANENT = "PERMANENT", "Permanent"
        NETWORK = "NETWORK", "Network"
        DONGLE = "DONGLE", "Dongle"
        SOFTWARE = "SOFTWARE", "Software"

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
    license_type = models.CharField(
        max_length=20, choices=LicenseType.choices, blank=True,
        help_text="License only. Tick one: Permanent, Network, Dongle, or Software.",
    )
    license_functionality = models.CharField(
        max_length=200, blank=True,
        help_text="Deprecated free-text field, kept for old data. Use 'functionalities' instead.",
    )
    functionalities = models.ManyToManyField(
        "LicenseFunctionality",
        blank=True,
        related_name="licenses",
        help_text="License only. Tick every functionality this license unlocks (e.g. SDI Out, Unreal Render Blade, Viz).",
    )
    license_duration_start = models.DateField(
        null=True, blank=True,
        help_text="License only. Start of the overall period this license is usable/active for.",
    )
    license_duration_end = models.DateField(
        null=True, blank=True,
        help_text="License only. End of the overall period this license is usable/active for "
                   "(the 'Duration' / expiry shown on the timeline). Within this window it can "
                   "still be assigned to different jobs for shorter stretches.",
    )
    viz_ticket = models.CharField(
        max_length=120, blank=True,
        help_text="License only. The Viz ticket number/reference that authorized the current Duration.",
    )
    parent_engine = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="nested_assets",
        limit_choices_to={"asset_type__in": CONTAINER_TYPES},
        help_text="The Engine or Sonnet Box this item is physically installed in, if any.",
    )
    last_updated_by = models.ForeignKey(
        "StaffMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="updated_assets",
    )
    last_updated_date = models.DateField(null=True, blank=True)
    last_updated_notes = models.CharField(
        max_length=300, blank=True,
        help_text="Short note on what changed, e.g. 'Replaced fan, retested OK'.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["asset_id"]

    def __str__(self):
        return f"{self.asset_id} ({self.get_asset_type_display()})"

    def clean(self):
        if self.parent_engine_id:
            if self.asset_type == self.AssetType.ENGINE:
                raise ValidationError("An Engine cannot be nested inside another Engine or container.")
            if self.asset_type in self.NESTABLE_CONTAINER_TYPES and self.parent_engine.asset_type != self.AssetType.ENGINE:
                raise ValidationError(
                    f"A {self.get_asset_type_display()} can only be nested inside an Engine, not another container."
                )
            if self.parent_engine_id == self.pk:
                raise ValidationError("An asset cannot be its own parent.")
        if (self.license_type or self.license_functionality or self.license_duration_start
                or self.license_duration_end or self.viz_ticket) \
                and self.asset_type != self.AssetType.LICENSE:
            raise ValidationError("License fields can only be set on License assets.")
        if self.license_duration_start and self.license_duration_end and self.license_duration_start > self.license_duration_end:
            raise ValidationError({"license_duration_end": "Duration end cannot be before duration start."})

    @property
    def is_nested(self):
        return self.parent_engine_id is not None

    @property
    def is_container(self):
        return self.asset_type in self.CONTAINER_TYPES


class Tag(models.Model):
    """Bank of tags that can be attached to an asset within a specific kit,
    e.g. 'MAIN', 'BACKUP', 'SPARE' - editable from Settings."""
    name = models.CharField(max_length=40, unique=True)
    color = models.CharField(max_length=7, blank=True, help_text="Optional hex color, e.g. #EAB308.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def clean(self):
        if self.color and not re.fullmatch(r"#[0-9A-Fa-f]{6}", self.color):
            raise ValidationError({"color": "Enter a hex color like #EA9B08."})


class Kit(models.Model):
    name = models.CharField(max_length=120, unique=True)
    notes = models.TextField(blank=True)
    assets = models.ManyToManyField(
        Asset,
        blank=True,
        through="KitAssetTag",
        related_name="kits",
        help_text=(
            "Direct members of this kit - Engines and/or loose assets "
            "(including Licenses). Components nested inside a member Engine "
            "(or a Sonnet Box nested in one) travel with it automatically and "
            "do not need to be added here separately."
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
        container_ids = set(
            Asset.objects.filter(
                id__in=direct_ids, asset_type__in=Asset.CONTAINER_TYPES
            ).values_list("id", flat=True)
        )
        # Sonnet Boxes/I-O devices nested inside a member Engine also act as containers.
        nested_sonnets = set(
            Asset.objects.filter(
                parent_engine_id__in=container_ids, asset_type__in=Asset.NESTABLE_CONTAINER_TYPES
            ).values_list("id", flat=True)
        )
        all_container_ids = container_ids | nested_sonnets
        nested_ids = set(
            Asset.objects.filter(parent_engine_id__in=all_container_ids).values_list("id", flat=True)
        )
        return direct_ids | nested_sonnets | nested_ids


class KitAssetTag(models.Model):
    """Through model for Kit<->Asset membership, so a specific asset can be
    tagged (e.g. 'MAIN engine') within the context of one particular kit."""
    kit = models.ForeignKey(Kit, on_delete=models.CASCADE, related_name="kit_asset_tags")
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="kit_asset_tags")
    tag = models.ForeignKey(
        Tag, null=True, blank=True, on_delete=models.SET_NULL, related_name="kit_asset_tags",
        help_text="Optional label for what this asset is used for in this kit, e.g. MAIN.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["kit", "asset__asset_id"]
        constraints = [
            models.UniqueConstraint(fields=["kit", "asset"], name="unique_asset_per_kit"),
        ]

    def __str__(self):
        if self.tag_id:
            return f"{self.asset.asset_id} in {self.kit.name} ({self.tag.name})"
        return f"{self.asset.asset_id} in {self.kit.name}"


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


# ---------------------------------------------------------------------------
# Van management
# ---------------------------------------------------------------------------

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
        help_text="Uncheck instead of deleting once a vehicle is sold/retired, to keep its history.",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Vehicle"

    def __str__(self):
        return self.name

    def last_usage(self):
        return self.usage_logs.order_by("-date", "-id").first()

    def last_maintenance(self):
        return self.maintenance_logs.order_by("-date", "-id").first()

    def last_checklist(self):
        return self.checklists.order_by("-date", "-id").first()


class VanUsageLog(models.Model):
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name="usage_logs")
    driver = models.ForeignKey(StaffMember, on_delete=models.SET_NULL, null=True, blank=True, related_name="van_trips")
    date = models.DateField()
    purpose = models.CharField(max_length=200, blank=True, help_text="What the van was used for / job.")
    destination = models.CharField(max_length=200, blank=True)
    start_mileage = models.PositiveIntegerField(null=True, blank=True)
    end_mileage = models.PositiveIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)
    logged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]

    def clean(self):
        if self.start_mileage is not None and self.end_mileage is not None:
            if self.end_mileage < self.start_mileage:
                raise ValidationError("End mileage cannot be before start mileage.")

    @property
    def distance(self):
        if self.start_mileage is not None and self.end_mileage is not None:
            return self.end_mileage - self.start_mileage
        return None

    def __str__(self):
        return f"{self.vehicle.name} - {self.date}"


class VanMaintenanceLog(models.Model):
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name="maintenance_logs")
    date = models.DateField()
    description = models.TextField(help_text="What was done - service, repair, MOT, etc.")
    performed_by = models.CharField(max_length=120, blank=True, help_text="Garage or person who did the work.")
    cost = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    next_due_date = models.DateField(null=True, blank=True, help_text="Next service/MOT due, if known.")
    logged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.vehicle.name} - {self.date} - {self.description[:40]}"


class VanChecklist(models.Model):
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name="checklists")
    date = models.DateField()
    checked_by = models.ForeignKey(StaffMember, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    items = models.JSONField(
        default=list,
        help_text="List of {item, ok, note} results for VAN_CHECKLIST_ITEMS at time of submission.",
    )
    notes = models.TextField(blank=True)
    logged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name_plural = "Van checklists"

    def __str__(self):
        return f"{self.vehicle.name} - {self.date} checklist"

    def issues_count(self):
        return sum(1 for i in self.items if not i.get("ok"))
