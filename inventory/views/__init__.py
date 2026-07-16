"""
Views are grouped by domain instead of one 2000+ line file. urls.py does
`from . import views` then calls things like `views.dashboard_view`, so
everything the URL config needs is re-exported here and nothing in urls.py
had to change.

    common.py     -> shared date/range/table-building helpers (no views)
    dashboard.py  -> dashboard_view
    timeline.py   -> timeline/calendar views + booking & job APIs
    assets.py     -> asset list + engine/sonnet/I-O device CRUD
    kits.py       -> kit list/create/edit/delete/PDF
    licenses.py   -> license list/create/edit/delete
    settings.py   -> settings page, tags, functionalities, CSV export
    tickets.py    -> public fault reporting + staff ticket views
    vans.py       -> van/vehicle CRUD + usage/maintenance/checklist logs
"""

from .dashboard import dashboard_view

from .timeline import (
    timeline_view,
    calendar_view,
    kit_detail_api,
    job_detail_api,
    delete_job,
    clone_job,
    job_create_view,
    create_booking,
    delete_booking,
    create_staff_booking,
    delete_staff_booking,
    create_license_booking,
    delete_license_booking,
)

from .assets import (
    asset_list_view,
    engine_create_view,
    engine_edit_view,
    engine_delete_view,
    engine_list_view,
    io_device_edit_view,
    io_device_delete_view,
)

from .kits import (
    kit_list_view,
    kit_create_view,
    kit_edit_view,
    kit_delete_view,
    kit_pdf_view,
)

from .licenses import (
    license_list_view,
    license_create_view,
    license_edit_view,
    license_delete_view,
)

from .settings import (
    settings_view,
    settings_tag_add,
    settings_tag_delete,
    settings_functionality_add,
    settings_functionality_delete,
    export_csv_view,
)

from .tickets import (
    ticket_report_view,
    ticket_list_view,
    ticket_detail_view,
    ticket_delete_view,
)

from .vans import (
    van_list_view,
    van_create_view,
    van_edit_view,
    van_delete_view,
    van_detail_view,
    van_usage_add,
    van_maintenance_add,
    van_checklist_add,
    van_usage_delete,
    van_maintenance_delete,
    van_checklist_delete,
)
