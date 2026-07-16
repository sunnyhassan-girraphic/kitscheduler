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
        verbose_name_plural = "Staff"

    def __str__(self):
        return self.name
