"""
Everything in this project lives in one real Django app ("inventory") on
purpose - one database, one set of migrations. This overrides just the
admin's grouping (cosmetic only - no models, no tables, no migrations
involved) so both the left sidebar and the "Site administration" index
page show a simple two-tier structure instead of one long alphabetical
list of all 12 models under "Inventory":

    Assets
    Jobs
    Kits
        Kit tags
    Staff
    Tickets
    Vans
        Van logs
    Settings
        Category colours
        License functionalities

Each entry below is (slug, label, primary_model_or_None, [child_model, ...]).
The top-level row links straight to the primary model (e.g. "Kits" links
to the Kit changelist) - there's no separate "Kits > Kits" duplication.
Groups with no primary model of their own (Settings) render as plain,
non-clickable text instead. Children render as a single indented line
underneath, with no heading bar anywhere - see templates/admin/app_list.html
for the actual rendering.
"""

import types

from django.contrib import admin

GROUPS = [
    ("assets", "Assets", "Asset", []),
    ("jobs", "Jobs", "Job", []),
    ("kits", "Kits", "Kit", ["Tag"]),
    ("staff", "Staff", "StaffMember", []),
    ("tickets", "Tickets", "Ticket", []),
    ("vans", "Vans", "Vehicle", ["VanLog"]),
    ("settings", "Settings", None, ["CategoryColour", "LicenseFunctionality"]),
]
_GROUP_ORDER = [slug for slug, _, _, _ in GROUPS]


def _grouped_get_app_list(self, request, app_label=None):
    app_dict = self._build_app_dict(request, app_label)
    if app_label:
        return sorted(app_dict.values(), key=lambda a: a["name"].lower()) if isinstance(app_dict, dict) else (app_dict or [])

    inventory_app = app_dict.pop("inventory", None)
    grouped = []
    if inventory_app:
        models_by_name = {m["object_name"]: m for m in inventory_app["models"]}
        for slug, label, primary_name, child_names in GROUPS:
            ordered_models = []
            has_primary = False

            if primary_name and primary_name in models_by_name:
                primary = models_by_name.pop(primary_name)
                primary["is_child"] = False
                primary["use_section_name"] = True
                ordered_models.append(primary)
                has_primary = True

            for child_name in child_names:
                if child_name in models_by_name:
                    child = models_by_name.pop(child_name)
                    child["is_child"] = True
                    child["use_section_name"] = False
                    ordered_models.append(child)

            if not ordered_models:
                continue

            grouped.append({
                "name": label,
                "app_label": slug,
                "app_url": ordered_models[0]["admin_url"],
                "has_module_perms": True,
                "has_primary": has_primary,
                "models": ordered_models,
            })

        # Safety net: anything left over (e.g. a new model nobody added to
        # GROUPS yet) still shows up instead of vanishing from the admin.
        if models_by_name:
            leftover = sorted(models_by_name.values(), key=lambda m: m["name"])
            for m in leftover:
                m["is_child"] = False
                m["use_section_name"] = False
            grouped.append({
                "name": "Inventory",
                "app_label": "inventory",
                "app_url": leftover[0]["admin_url"],
                "has_module_perms": True,
                "has_primary": True,
                "models": leftover,
            })

    other_apps = sorted(app_dict.values(), key=lambda a: a["name"].lower())
    for a in other_apps:
        a["has_primary"] = True
        for m in a["models"]:
            m["is_child"] = False
            m["use_section_name"] = False
    grouped.sort(key=lambda a: _GROUP_ORDER.index(a["app_label"]) if a["app_label"] in _GROUP_ORDER else 999)
    return grouped + other_apps


admin.site.get_app_list = types.MethodType(_grouped_get_app_list, admin.site)
