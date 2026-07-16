import re
from django.core.exceptions import ValidationError
from django.db import models


class LicenseFunctionality(models.Model):
    """Bank of tickable License functionalities (e.g. SDI Out, Unreal Render
    Blade, Viz), editable from Settings instead of typed freehand."""
    name = models.CharField(max_length=80, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "License Functionality"
        verbose_name_plural = "License Functionalities"

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
        verbose_name = "Kit tag"
        verbose_name_plural = "Kit tags"

    def __str__(self):
        return self.name

    def clean(self):
        if self.color and not re.fullmatch(r"#[0-9A-Fa-f]{6}", self.color):
            raise ValidationError({"color": "Enter a hex color like #EA9B08."})
