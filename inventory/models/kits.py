from django.db import models

from .assets import Asset, Tag

KIT_HISTORY_FIELD_LABELS = {
    "name": "Kit name",
}


class KitHistory(models.Model):
    """Change log for the Kit edit form's right-hand History panel -
    mirrors AssetHistory's shape (used on Engine/License pages), with the
    same deliberate choice of changed_by being a StaffMember rather than
    the raw Django login, so someone can log an update on a colleague's
    behalf. Unlike Engine/License, Kit has no separate 'last updated by'
    picker on its form - changed_by is taken from whoever is logged in
    when the save happens."""
    kit = models.ForeignKey("Kit", on_delete=models.CASCADE, related_name="history")
    changed_by = models.ForeignKey(
        "StaffMember", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    field_changed = models.CharField(
        max_length=40,
        help_text="e.g. 'name', 'note', 'asset_added', 'asset_removed', 'quantity_changed', 'created'.",
    )
    old_value = models.CharField(max_length=200, blank=True)
    new_value = models.CharField(max_length=200, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "Kit history"

    def __str__(self):
        return f"{self.kit.name} - {self.field_changed}"

    @property
    def field_label(self):
        return KIT_HISTORY_FIELD_LABELS.get(self.field_changed, self.field_changed.replace("_", " ").title())

    @staticmethod
    def filtered_for(kit, mode="month", page=1, page_size=25):
        """mode is 'week', 'month', or 'all'. Always paginated regardless of
        mode, same reasoning as AssetHistory.filtered_for."""
        import datetime as _dt
        from django.core.paginator import Paginator

        qs = kit.history.select_related("changed_by").order_by("-created_at")
        if mode == "week":
            qs = qs.filter(created_at__date__gte=_dt.date.today() - _dt.timedelta(days=7))
        elif mode == "month":
            qs = qs.filter(created_at__date__gte=_dt.date.today() - _dt.timedelta(days=30))
        return Paginator(qs, page_size).get_page(page)

    @staticmethod
    def record_scalar_changes(kit, before_values, changed_by, fields):
        for field in fields:
            old_raw = before_values.get(field)
            new_raw = getattr(kit, field)
            if old_raw == new_raw:
                continue
            KitHistory.objects.create(
                kit=kit, changed_by=changed_by, field_changed=field,
                old_value=str(old_raw)[:200] if old_raw not in (None, "") else "(none)",
                new_value=str(new_raw)[:200] if new_raw not in (None, "") else "(none)",
            )

    @staticmethod
    def record_note(kit, changed_by, before_note, after_note):
        """Only logs when kit.notes actually changed, same as
        AssetHistory.record_note - the notes textarea is pre-filled on
        every edit, so this avoids re-logging unchanged text."""
        after_note = (after_note or "").strip()
        if after_note and after_note != (before_note or ""):
            KitHistory.objects.create(
                kit=kit, changed_by=changed_by, field_changed="note", note=after_note,
            )

    @staticmethod
    def record_asset_changes(kit, before_ids, after_ids, changed_by):
        """Logs direct assets added/removed from this kit. Not recursive -
        components nested inside a member Engine aren't logged here."""
        before_ids, after_ids = set(before_ids), set(after_ids)
        added, removed = after_ids - before_ids, before_ids - after_ids
        if not (added or removed):
            return
        labels = dict(Asset.objects.filter(id__in=added | removed).values_list("id", "asset_id"))
        for aid in added:
            KitHistory.objects.create(
                kit=kit, changed_by=changed_by, field_changed="asset_added",
                new_value=labels.get(aid, str(aid)),
            )
        for aid in removed:
            KitHistory.objects.create(
                kit=kit, changed_by=changed_by, field_changed="asset_removed",
                old_value=labels.get(aid, str(aid)),
            )

    @staticmethod
    def record_quantity_changes(kit, before_qty_by_asset_id, after_qty_by_asset_id, changed_by):
        """Logs quantity changes for bulk/stock assets (e.g. a Wired Mouse
        going from 2 to 3 in this kit) - only for assets present both
        before and after, since a fresh add/remove is already logged by
        record_asset_changes."""
        common_ids = set(before_qty_by_asset_id) & set(after_qty_by_asset_id)
        changed_ids = {
            aid for aid in common_ids
            if before_qty_by_asset_id[aid] != after_qty_by_asset_id[aid]
        }
        if not changed_ids:
            return
        labels = dict(Asset.objects.filter(id__in=changed_ids).values_list("id", "asset_id"))
        for aid in changed_ids:
            KitHistory.objects.create(
                kit=kit, changed_by=changed_by, field_changed="quantity_changed",
                old_value=f"{labels.get(aid, aid)}: {before_qty_by_asset_id[aid]}",
                new_value=f"{labels.get(aid, aid)}: {after_qty_by_asset_id[aid]}",
            )


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
            "(or an I/O Device nested in one) travel with it automatically and "
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
        # I/O Devices nested inside a member Engine also act as containers.
        nested_containers = set(
            Asset.objects.filter(
                parent_engine_id__in=container_ids, asset_type__in=Asset.NESTABLE_CONTAINER_TYPES
            ).values_list("id", flat=True)
        )
        all_container_ids = container_ids | nested_containers
        nested_ids = set(
            Asset.objects.filter(parent_engine_id__in=all_container_ids).values_list("id", flat=True)
        )
        return direct_ids | nested_containers | nested_ids


class KitAssetTag(models.Model):
    """Through model for Kit<->Asset membership, so a specific asset can be
    tagged (e.g. 'MAIN engine') within the context of one particular kit."""
    kit = models.ForeignKey(Kit, on_delete=models.CASCADE, related_name="kit_asset_tags")
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="kit_asset_tags")
    tag = models.ForeignKey(
        Tag, null=True, blank=True, on_delete=models.SET_NULL, related_name="kit_asset_tags",
        help_text="Optional label for what this asset is used for in this kit, e.g. MAIN.",
    )
    quantity = models.PositiveIntegerField(
        default=1,
        help_text=(
            "How many units of this asset this kit uses. Only meaningful for "
            "bulk/stock assets (Asset.qty > 1, e.g. a 'Wired Mouse' row "
            "representing several identical units) - individually-tracked "
            "assets (Engines, Components, etc.) are always 1."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["kit", "created_at"]
        constraints = [
            models.UniqueConstraint(fields=["kit", "asset"], name="unique_asset_per_kit"),
        ]

    def __str__(self):
        if self.tag_id:
            return f"{self.asset.asset_id} in {self.kit.name} ({self.tag.name})"
        return f"{self.asset.asset_id} in {self.kit.name}"
