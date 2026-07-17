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
        COMPONENT = "COMPONENT", "Component"
        STANDALONE = "STANDALONE", "Standalone"
        PERIPHERAL = "PERIPHERAL", "Peripheral"
        CABLE = "CABLE", "Cable"
        IO_DEVICE = "IO_DEVICE", "I/O Device"
        LICENSE = "LICENSE", "License"

    # Types that other assets can be physically nested inside of. ENGINE is
    # the top-level container; IO_DEVICE is a container that can itself nest
    # inside an Engine (e.g. an I/O device chassis holding a GPU, which then
    # sits inside the Engine).
    NESTABLE_CONTAINER_TYPES = (AssetType.IO_DEVICE,)
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
        help_text="The Engine or I/O Device this item is physically installed in, if any.",
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


# Scalar fields that get an automatic history entry when they change, split
# by whether they apply to every Asset type or only to Licenses. Fields not
# listed here (make_model, serial, free-text notes, etc.) were deliberately
# left out - flagged early as things that would just generate noise, not
# useful history (agreed with Sunny when designing this).
ASSET_HISTORY_SHARED_FIELDS = ["status", "archived", "qty", "asset_id"]
ASSET_HISTORY_LICENSE_FIELDS = [
    "license_type", "viz_ticket", "license_duration_start", "license_duration_end",
]

ASSET_HISTORY_FIELD_LABELS = {
    "status": "Status",
    "archived": "Archived",
    "qty": "Quantity",
    "asset_id": "Asset ID",
    "license_type": "License type",
    "viz_ticket": "Viz ticket",
    "license_duration_start": "Duration start",
    "license_duration_end": "Duration end",
}


def _history_display_value(field, raw_value):
    """Renders a raw field value (old OR new - this takes plain values, not
    an Asset instance, so it works equally for 'before' snapshots) into the
    same human label the UI would show, e.g. 'AVAILABLE' -> 'Available'."""
    if field == "status":
        return dict(Asset.Status.choices).get(raw_value, raw_value or "(none)")
    if field == "license_type":
        return dict(Asset.LicenseType.choices).get(raw_value, raw_value) if raw_value else "(none)"
    if field == "archived":
        return "Archived" if raw_value else "Active"
    if raw_value in (None, ""):
        return "(none)"
    return str(raw_value)


class AssetHistory(models.Model):
    """Change log for License and Engine/I-O Device pages, shown on the
    right-hand side of their edit forms - mirrors TicketHistory's shape,
    with one deliberate difference: changed_by is a StaffMember (matching
    the existing 'Last updated by' picker, which allows logging an update
    on someone else's behalf) rather than the Django login, since that's
    already how this app's 'Last updated by' concept works everywhere else."""
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="history")
    changed_by = models.ForeignKey(
        "StaffMember", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    field_changed = models.CharField(
        max_length=40,
        help_text="e.g. 'status', 'qty', 'component_added', 'note', 'created'.",
    )
    old_value = models.CharField(max_length=200, blank=True)
    new_value = models.CharField(max_length=200, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "Asset history"

    def __str__(self):
        return f"{self.asset.asset_id} - {self.field_changed}"

    @property
    def field_label(self):
        return ASSET_HISTORY_FIELD_LABELS.get(self.field_changed, self.field_changed.replace("_", " ").title())

    @staticmethod
    def filtered_for(asset, mode="month", page=1, page_size=25):
        """mode is 'week', 'month', or 'all'. Always paginated regardless of
        mode - even 'this week' could theoretically be large on a very
        actively-changed asset, and there's no upside to skipping the safety
        net just because the window is usually small."""
        import datetime as _dt
        from django.core.paginator import Paginator

        qs = asset.history.select_related("changed_by").order_by("-created_at")
        if mode == "week":
            qs = qs.filter(created_at__date__gte=_dt.date.today() - _dt.timedelta(days=7))
        elif mode == "month":
            qs = qs.filter(created_at__date__gte=_dt.date.today() - _dt.timedelta(days=30))
        return Paginator(qs, page_size).get_page(page)

    @staticmethod
    def record_scalar_changes(asset, before_values, changed_by, fields):
        """before_values is a dict of field -> raw value captured BEFORE the
        save was applied. Compares each against asset's current (post-save)
        value and logs one entry per field that actually changed."""
        for field in fields:
            old_raw = before_values.get(field)
            new_raw = getattr(asset, field)
            if old_raw == new_raw:
                continue
            AssetHistory.objects.create(
                asset=asset, changed_by=changed_by, field_changed=field,
                old_value=_history_display_value(field, old_raw)[:200],
                new_value=_history_display_value(field, new_raw)[:200],
            )

    @staticmethod
    def record_note(asset, changed_by, before_note, after_note):
        """Only logs when the note text actually changed - NOT every time
        it's non-blank, since the edit form pre-fills the previous note for
        context and would otherwise re-log the same text on every unrelated
        save (e.g. fixing a typo in make_model would also silently re-log
        an old 'Replaced fan, retested OK' note every time)."""
        after_note = (after_note or "").strip()
        if after_note and after_note != (before_note or ""):
            AssetHistory.objects.create(
                asset=asset, changed_by=changed_by, field_changed="note", note=after_note,
            )

    @staticmethod
    def record_component_changes(container, before_ids, after_ids, changed_by):
        """Logs direct components added/removed from this Engine/I-O Device.
        Deliberately not recursive - a change to what's nested inside a
        sub-container (e.g. an I/O Device nested in this Engine) gets logged
        against that sub-container when IT is edited, not duplicated here."""
        before_ids, after_ids = set(before_ids), set(after_ids)
        added, removed = after_ids - before_ids, before_ids - after_ids
        if not (added or removed):
            return
        labels = dict(Asset.objects.filter(id__in=added | removed).values_list("id", "asset_id"))
        for aid in added:
            AssetHistory.objects.create(
                asset=container, changed_by=changed_by, field_changed="component_added",
                new_value=labels.get(aid, str(aid)),
            )
        for aid in removed:
            AssetHistory.objects.create(
                asset=container, changed_by=changed_by, field_changed="component_removed",
                old_value=labels.get(aid, str(aid)),
            )

    @staticmethod
    def record_functionality_changes(license_asset, before_ids, changed_by):
        after_ids = set(license_asset.functionalities.values_list("id", flat=True))
        before_ids = set(before_ids)
        added, removed = after_ids - before_ids, before_ids - after_ids
        if not (added or removed):
            return
        names = dict(
            LicenseFunctionality.objects.filter(id__in=added | removed).values_list("id", "name")
        )
        for fid in added:
            AssetHistory.objects.create(
                asset=license_asset, changed_by=changed_by, field_changed="functionality_added",
                new_value=names.get(fid, str(fid)),
            )
        for fid in removed:
            AssetHistory.objects.create(
                asset=license_asset, changed_by=changed_by, field_changed="functionality_removed",
                old_value=names.get(fid, str(fid)),
            )


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
