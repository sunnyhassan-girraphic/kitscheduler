from django import template
register = template.Library()

@register.filter
def get_item(dictionary, key):
    return dictionary.get(key, 0)


@register.simple_tag
def kit_member_summary(members):
    """Returns a short summary string like '2 Engines · 1 GPU · 3 Cables'
    counting top-level members by display type, plus nested components
    by their display type (so a GPU inside an I/O Device inside an Engine
    still shows up as '1 GPU')."""
    from collections import Counter
    counts = Counter()
    for m in members:
        counts[m.get_asset_type_display()] += getattr(m, 'kit_qty', 1)
        # Walk nested assets for engines and I/O devices
        for comp in m.nested_assets.all():
            counts[comp.get_asset_type_display()] += 1
            for sub in comp.nested_assets.all():
                counts[sub.get_asset_type_display()] += 1

    # Preferred display order
    order = ["Engine", "I/O Device", "Component", "Laptop", "Peripheral", "Cable", "License"]
    parts = []
    for label in order:
        if counts[label]:
            n = counts[label]
            # Pluralise simply
            plural = label + "s" if not label.endswith("e") else label + "s"
            if label == "I/O Device":
                plural = "I/O Devices"
            elif label == "Peripheral":
                plural = "Peripherals"
            elif label == "Cable":
                plural = "Cables"
            elif label == "License":
                plural = "Licenses"
            elif label == "Laptop":
                plural = "Laptops"
            elif label == "Engine":
                plural = "Engines"
            elif label == "Component":
                plural = "Components"
            parts.append(f"{n} {label if n == 1 else plural}")
    # Any types not in order list
    for label, n in counts.items():
        if label not in order and n:
            parts.append(f"{n} {label}")
    return " · ".join(parts) if parts else "Empty"
