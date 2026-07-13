"""Generates a printable Kit checklist PDF, matching the team's existing
Google Sheets kit-list template: a gold title band, a hand-fill metadata
grid (Packed By / Event Date / GPS Tag / Carnet / No. of Cases), and an
item table (Item / Details / Quantity / Case No. / checkbox).
"""
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak,
)

GOLD = colors.HexColor("#F1C232")
RULE_GREY = colors.HexColor("#9E9E9E")
BORDER_BLACK = colors.black

PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN = 14 * mm
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN

title_style = ParagraphStyle(
    "KitTitle", fontName="Helvetica-Bold", fontSize=15, leading=18,
    alignment=1, textColor=colors.black,
)
label_style = ParagraphStyle(
    "MetaLabel", fontName="Helvetica-Bold", fontSize=8, leading=10,
    textColor=colors.black,
)
value_style = ParagraphStyle(
    "MetaValue", fontName="Helvetica", fontSize=9, leading=12,
    textColor=colors.black,
)
header_cell_style = ParagraphStyle(
    "TableHeader", fontName="Helvetica-Bold", fontSize=9, leading=11,
    textColor=colors.black,
)
item_style = ParagraphStyle(
    "ItemCell", fontName="Helvetica-Bold", fontSize=9.5, leading=12,
)
nested_item_style = ParagraphStyle(
    "NestedItemCell", fontName="Helvetica", fontSize=9, leading=11,
    textColor=colors.HexColor("#444444"), leftIndent=10,
)
detail_style = ParagraphStyle("DetailCell", fontName="Helvetica", fontSize=9, leading=11)
qty_style = ParagraphStyle("QtyCell", fontName="Helvetica", fontSize=9, leading=11, alignment=1)


def _checkbox_flowable():
    """A small empty square drawn as a 1x1 table, since Helvetica's base
    WinAnsi encoding has no checkbox/checkmark glyph to rely on."""
    box = Table([[""]], colWidths=[10], rowHeights=[10])
    box.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 1, BORDER_BLACK),
    ]))
    return box


def _title_band(kit_name):
    tbl = Table(
        [[Paragraph(f"{kit_name} - KIT CHECKLIST", title_style)]],
        colWidths=[CONTENT_WIDTH], rowHeights=[26 * mm / 2],
    )
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), GOLD),
        ("BOX", (0, 0), (-1, -1), 1, BORDER_BLACK),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    return tbl


def _meta_grid(meta=None):
    meta = meta or {}

    def field(label, key, extra_space=14):
        value = meta.get(key, "").strip()
        cell = [Paragraph(f"<b>{label}</b>", label_style)]
        if value:
            cell.append(Paragraph(value, value_style))
            cell.append(Spacer(1, max(extra_space - 12, 2)))
        else:
            cell.append(Spacer(1, extra_space))
        return cell

    packed_by_cell = field("PACKED BY", "packed_by", extra_space=30)
    event_date_cell = field("EVENT DATE", "event_date")
    gps_tag_cell = field("GPS TAG", "gps_tag")
    carnet_cell = field("CARNET", "carnet")
    cases_cell = field("No. of CASES", "cases")
    blank_cell = ""

    col1 = CONTENT_WIDTH * 0.34
    col2 = CONTENT_WIDTH * 0.33
    col3 = CONTENT_WIDTH - col1 - col2

    data = [
        [packed_by_cell, event_date_cell, gps_tag_cell],
        [blank_cell, carnet_cell, cases_cell],
    ]
    tbl = Table(data, colWidths=[col1, col2, col3], rowHeights=[15 * mm, 15 * mm])
    tbl.setStyle(TableStyle([
        ("SPAN", (0, 0), (0, 1)),
        ("GRID", (0, 0), (-1, -1), 0.75, BORDER_BLACK),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return tbl


def _item_rows(kit):
    """Direct kit members plus, for Engines, their nested components as sub-rows."""
    rows = []
    for asset in kit.assets.all().order_by("asset_type", "asset_id"):
        rows.append({
            "item": asset.asset_id,
            "details": asset.make_model,
            "qty": asset.qty,
            "nested": False,
        })
        if asset.asset_type == "ENGINE":
            for comp in asset.nested_assets.all().order_by("asset_id"):
                rows.append({
                    "item": comp.asset_id,
                    "details": comp.make_model,
                    "qty": comp.qty,
                    "nested": True,
                })
    return rows


def _items_table(kit):
    header = [
        Paragraph("ITEM", header_cell_style),
        Paragraph("DETAILS", header_cell_style),
        Paragraph("QUANTITY", header_cell_style),
        Paragraph("CASE NO.", header_cell_style),
        Paragraph("CHECK", header_cell_style),
    ]
    data = [header]
    rows = _item_rows(kit)
    for row in rows:
        item_para = Paragraph(
            ("- " if row["nested"] else "") + row["item"],
            nested_item_style if row["nested"] else item_style,
        )
        details_para = Paragraph(row["details"] or "", detail_style)
        qty_para = Paragraph(str(row["qty"]), qty_style)
        data.append([item_para, details_para, qty_para, "", _checkbox_flowable()])

    col_widths = [
        CONTENT_WIDTH * 0.28, CONTENT_WIDTH * 0.36,
        CONTENT_WIDTH * 0.14, CONTENT_WIDTH * 0.12, CONTENT_WIDTH * 0.10,
    ]
    tbl = Table(data, colWidths=col_widths, repeatRows=1)
    style = [
        ("LINEBELOW", (0, 0), (-1, 0), 1.2, BORDER_BLACK),
        ("LINEBELOW", (0, 1), (-1, -1), 0.6, RULE_GREY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (2, 0), (2, -1), "CENTER"),
        ("ALIGN", (4, 0), (4, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]
    tbl.setStyle(TableStyle(style))
    return tbl, len(rows)


def build_kit_checklist_pdf(kit, meta=None):
    """Returns PDF bytes for a single kit's printable checklist.
    `meta` may include packed_by, event_date, gps_tag, carnet, cases -
    any left out (or blank) render as blank hand-fill space, as before."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=MARGIN,
        title=f"{kit.name} - Kit Checklist",
    )

    items_table, item_count = _items_table(kit)

    story = [
        _title_band(kit.name),
        Spacer(1, 6),
        _meta_grid(meta),
        Spacer(1, 10),
        items_table,
    ]
    if item_count == 0:
        story.append(Spacer(1, 10))
        story.append(Paragraph("No assets currently assigned to this kit.", detail_style))

    doc.build(story)
    return buf.getvalue()
