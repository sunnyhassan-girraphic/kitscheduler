"""
Models are grouped by domain instead of one flat file, but Django doesn't
care which module a model is physically defined in - the app_label is still
"inventory" and the database tables/migrations are unaffected. Everything is
re-exported here so existing code (`from inventory.models import Asset`,
`from . import models` + `models.Asset`, etc.) keeps working unchanged.

    staff.py     -> StaffMember
    assets.py    -> Asset, Tag, LicenseFunctionality, AssetHistory
    kits.py      -> Kit, KitAssetTag
    jobs.py      -> Job, CategoryColour, KitBooking, AssetBooking, StaffBooking
    tickets.py   -> Ticket, TicketHistory
    vans.py      -> Vehicle, VanLog, VAN_CHECKLIST_ITEMS
"""

from .staff import StaffMember
from .assets import Asset, Tag, LicenseFunctionality, AssetHistory
from .kits import Kit, KitAssetTag
from .jobs import Job, CategoryColour, KitBooking, AssetBooking, StaffBooking
from .tickets import Ticket, TicketHistory
from .vans import Vehicle, VanLog, VAN_CHECKLIST_ITEMS

__all__ = [
    "StaffMember",
    "Asset",
    "Tag",
    "LicenseFunctionality",
    "AssetHistory",
    "Kit",
    "KitAssetTag",
    "Job",
    "CategoryColour",
    "KitBooking",
    "AssetBooking",
    "StaffBooking",
    "Ticket",
    "TicketHistory",
    "Vehicle",
    "VanLog",
    "VAN_CHECKLIST_ITEMS",
]
