from django.db import models

from .assets import Asset, Tag


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
