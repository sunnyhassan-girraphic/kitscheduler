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
