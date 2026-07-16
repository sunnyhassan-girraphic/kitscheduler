"""
Admin registrations grouped by domain (same split as inventory/models/).
Each submodule below calls @admin.register(...) at import time, so importing
them here is what actually wires everything into the Django admin site.

    staff.py    -> StaffMember
    assets.py   -> Asset, LicenseFunctionality
    kits.py     -> Kit (+ KitAssetTag inline), Tag
    jobs.py     -> Job, CategoryColour (bookings are inline on Job)
    tickets.py  -> Ticket (+ TicketHistory inline)
    vans.py     -> Vehicle, VanLog
    _site.py    -> groups the admin index/sidebar into sections (cosmetic only)
"""

from . import staff  # noqa: F401
from . import assets  # noqa: F401
from . import kits  # noqa: F401
from . import jobs  # noqa: F401
from . import tickets  # noqa: F401
from . import vans  # noqa: F401
from . import _site  # noqa: F401
