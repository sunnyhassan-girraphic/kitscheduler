from django.conf import settings
from django.db import models


class StaffMember(models.Model):
    name = models.CharField(max_length=120, unique=True)
    notes = models.TextField(blank=True)
    active = models.BooleanField(
        default=True,
        help_text="Uncheck instead of deleting once someone leaves, to keep their booking history.",
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="staff_profile",
        help_text="Links this person to their login, so 'Last updated by' fields can "
                   "automatically default to whoever is currently signed in.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Staff"

    def __str__(self):
        return self.name

    @classmethod
    def for_user(cls, user):
        """The StaffMember linked to this Django login, if any. Used to
        default 'Last updated by' dropdowns to whoever is currently signed
        in instead of leaving them blank / stuck on a stale value."""
        if not user or not user.is_authenticated:
            return None
        return getattr(user, "staff_profile", None)



class DashboardWidget(models.Model):
    """Per-user dashboard widget preferences - position and visibility."""

    WIDGET_CHOICES = [
        ("stats",           "Stats Strip"),
        ("timeline",        "Mini Timeline"),
        ("upcoming",        "Upcoming Jobs"),
        ("attention",       "Needs Attention"),
        ("active_kits",     "Active Kits"),
        ("stocktake",       "Stock Take Health"),
        ("license_expiry",  "License Expiry"),
        ("activity",        "Recent Activity"),
    ]

    COLUMN_FULL  = "full"
    COLUMN_LEFT  = "left"
    COLUMN_RIGHT = "right"
    COLUMN_CHOICES = [
        (COLUMN_FULL,  "Full width"),
        (COLUMN_LEFT,  "Left column"),
        (COLUMN_RIGHT, "Right column"),
    ]

    # (widget_id, position, visible, column)
    DEFAULTS = [
        ("stats",          0, True,  "full"),
        ("timeline",       1, True,  "left"),
        ("upcoming",       2, True,  "left"),
        ("attention",      3, True,  "left"),
        ("active_kits",    4, True,  "right"),
        ("stocktake",      5, False, "right"),
        ("license_expiry", 6, True,  "right"),
        ("activity",       7, False, "right"),
    ]

    user      = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="dashboard_widgets",
    )
    widget_id = models.CharField(max_length=40, choices=WIDGET_CHOICES)
    position  = models.PositiveSmallIntegerField(default=0)
    visible   = models.BooleanField(default=True)
    column    = models.CharField(max_length=10, choices=COLUMN_CHOICES, default=COLUMN_LEFT)

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(fields=["user", "widget_id"], name="unique_widget_per_user"),
        ]

    def __str__(self):
        return f"{self.user} - {self.widget_id} (pos {self.position})"

    @classmethod
    def for_user(cls, user):
        """Return ordered widget list for a user, creating defaults if first visit."""
        existing = {w.widget_id: w for w in cls.objects.filter(user=user)}
        if not existing:
            to_create = [
                cls(user=user, widget_id=wid, position=pos, visible=vis, column=col)
                for wid, pos, vis, col in cls.DEFAULTS
            ]
            cls.objects.bulk_create(to_create)
            return cls.objects.filter(user=user).order_by("position")
        # Fill in any new widgets added since user last visited
        known = set(existing.keys())
        all_ids = {wid for wid, _, _, _ in cls.DEFAULTS}
        missing = all_ids - known
        if missing:
            max_pos = max(w.position for w in existing.values())
            to_create = []
            for wid, _, vis, col in cls.DEFAULTS:
                if wid in missing:
                    max_pos += 1
                    to_create.append(cls(user=user, widget_id=wid, position=max_pos, visible=False, column=col))
            cls.objects.bulk_create(to_create)
        return cls.objects.filter(user=user).order_by("position")
