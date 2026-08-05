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
from reportlab.graphics.shapes import Drawing, Line
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer,
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


def _checkmark_drawing(size=8):
    """A checkmark drawn as two line segments, not a font glyph - Helvetica
    has no checkmark character, and ZapfDingbats' checkmark code turned out
    to render as a missing-glyph box in practice rather than an actual
    checkmark, so this sidesteps font/encoding guesswork entirely."""
    d = Drawing(size, size)
    d.add(Line(size * 0.08, size * 0.45, size * 0.38, size * 0.12,
                strokeColor=colors.black, strokeWidth=1.3, strokeLineCap=1))
    d.add(Line(size * 0.38, size * 0.12, size * 0.92, size * 0.85,
                strokeColor=colors.black, strokeWidth=1.3, strokeLineCap=1))
    return d


def _checkbox_flowable(checked=False):
    """A small square drawn as a 1x1 table. When checked, a vector
    checkmark (see _checkmark_drawing) is placed inside."""
    content = _checkmark_drawing(size=8) if checked else ""
    box = Table([[content]], colWidths=[10], rowHeights=[10])
    box.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 1, BORDER_BLACK),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
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


def get_kit_checklist_rows(kit):
    """Direct kit members plus, for Engines and I/O Devices, their nested
    components as sub-rows (including components nested inside an I/O Device
    that is itself nested inside an Engine). Each row includes the real
    Asset id so the edit-before-export UI (and the override lookup below)
    can key off something stable."""
    rows = []
    kit_quantities = {kat.asset_id: kat.quantity for kat in kit.kit_asset_tags.all()}

    def add_nested(container, depth=1):
        for comp in container.nested_assets.all().order_by("asset_id"):
            rows.append({
                "id": comp.id,
                "item": comp.asset_id,
                "details": comp.make_model,
                "qty": comp.qty,
                "nested": True,
            })
            if comp.asset_type == "IO_DEVICE":
                add_nested(comp, depth=depth + 1)

    for asset in kit.assets.all().order_by("asset_type", "asset_id"):
        rows.append({
            "id": asset.id,
            "item": asset.asset_id,
            "details": asset.make_model,
            "qty": kit_quantities.get(asset.id, asset.qty),
            "nested": False,
        })
        if asset.asset_type in ("ENGINE", "IO_DEVICE"):
            add_nested(asset)
    return rows


def _items_table(kit, item_overrides=None):
    """`item_overrides` maps asset id (int or str) -> {"case": str, "checked": bool}."""
    item_overrides = item_overrides or {}

    header = [
        Paragraph("ITEM", header_cell_style),
        Paragraph("DETAILS", header_cell_style),
        Paragraph("QUANTITY", header_cell_style),
        Paragraph("CASE NO.", header_cell_style),
        Paragraph("CHECKED", header_cell_style),
    ]
    data = [header]
    rows = get_kit_checklist_rows(kit)
    for row in rows:
        override = item_overrides.get(row["id"]) or item_overrides.get(str(row["id"])) or {}
        item_para = Paragraph(
            ("- " if row["nested"] else "") + row["item"],
            nested_item_style if row["nested"] else item_style,
        )
        details_para = Paragraph(row["details"] or "", detail_style)
        qty_para = Paragraph(str(override.get("qty", row["qty"])), qty_style)
        case_para = Paragraph(override.get("case", "") or "", detail_style)
        checkbox = _checkbox_flowable(checked=bool(override.get("checked")))
        data.append([item_para, details_para, qty_para, case_para, checkbox])

    col_widths = [
        CONTENT_WIDTH * 0.26, CONTENT_WIDTH * 0.32,
        CONTENT_WIDTH * 0.14, CONTENT_WIDTH * 0.12, CONTENT_WIDTH * 0.16,
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


def build_kit_checklist_pdf(kit, meta=None, item_overrides=None):
    """Returns PDF bytes for a single kit's printable checklist.
    `meta` may include packed_by, event_date, gps_tag, carnet, cases -
    any left out (or blank) render as blank hand-fill space, as before.
    `item_overrides` maps asset id -> {"case": str, "checked": bool}."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=MARGIN,
        title=f"{kit.name} - Kit Checklist",
    )

    items_table, item_count = _items_table(kit, item_overrides)

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



# ---------------------------------------------------------------------------
# Stock Take session PDF
# Same visual language as the kit checklist: white page, gold header band,
# black text, grey rules. No dark background.
# ---------------------------------------------------------------------------

ST_GOLD = colors.HexColor("#F1C232")
ST_GREY = colors.HexColor("#9E9E9E")
ST_GREEN = colors.HexColor("#2E7D32")
ST_AMBER = colors.HexColor("#E65100")
ST_BLACK = colors.black
ST_DIM = colors.HexColor("#555555")
ST_ROW_ALT = colors.HexColor("#F9F9F9")

st_title_style = ParagraphStyle(
    "StTitle", fontName="Helvetica-Bold", fontSize=15, leading=18,
    textColor=ST_BLACK, alignment=1,
)
st_sub_style = ParagraphStyle(
    "StSub", fontName="Helvetica", fontSize=8.5, leading=11,
    textColor=ST_DIM, alignment=1,
)
st_stat_num_style = ParagraphStyle(
    "StStatNum", fontName="Helvetica-Bold", fontSize=16, leading=20,
    textColor=ST_BLACK, alignment=1,
)
st_stat_label_style = ParagraphStyle(
    "StStatLbl", fontName="Helvetica", fontSize=7.5, leading=10,
    textColor=ST_DIM, alignment=1,
)
st_col_header_style = ParagraphStyle(
    "StColHdr", fontName="Helvetica-Bold", fontSize=8.5, leading=11,
    textColor=ST_BLACK,
)
st_asset_id_style = ParagraphStyle(
    "StAssetId", fontName="Helvetica-Bold", fontSize=9, leading=11,
    textColor=ST_BLACK,
)
st_cell_style = ParagraphStyle(
    "StCell", fontName="Helvetica", fontSize=8.5, leading=11,
    textColor=ST_DIM,
)
st_outcome_confirmed = ParagraphStyle(
    "StConfirmed", fontName="Helvetica-Bold", fontSize=8.5, leading=11,
    textColor=ST_GREEN, alignment=1,
)
st_outcome_flagged = ParagraphStyle(
    "StFlagged", fontName="Helvetica-Bold", fontSize=8.5, leading=11,
    textColor=ST_AMBER, alignment=1,
)
st_outcome_other = ParagraphStyle(
    "StOther", fontName="Helvetica", fontSize=8.5, leading=11,
    textColor=ST_DIM, alignment=1,
)


def _st_title_band(session):
    label = session.display_label
    started = session.started_at.strftime("%d %b %Y")
    by = f"Run by {session.created_by.name}  -  " if session.created_by else ""
    scope = session.asset_types_display()
    threshold = session.staleness_display()
    status = "Complete" if session.closed_at else "In progress"
    closed = f"  -  Closed {session.closed_at.strftime('%d %b %Y')}" if session.closed_at else ""

    title_para = Paragraph(f"STOCK TAKE - {label.upper()}", st_title_style)
    sub_para = Paragraph(
        f"{by}Started {started}{closed}  |  Scope: {scope}  |  Threshold: {threshold}  |  {status}",
        st_sub_style,
    )

    tbl = Table([[title_para], [sub_para]], colWidths=[CONTENT_WIDTH])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), ST_GOLD),
        ("BOX", (0, 0), (-1, -1), 1, ST_BLACK),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#BFA020")),
        ("TOPPADDING", (0, 0), (-1, 0), 10),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 1), (-1, 1), 4),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
    ]))
    return tbl


def _st_summary_bar(session):
    def cell(value, label):
        return [
            Paragraph(str(value), st_stat_num_style),
            Paragraph(label, st_stat_label_style),
        ]

    col_w = CONTENT_WIDTH / 5
    data = [[
        cell(session.total_count, "TOTAL"),
        cell(session.confirmed_count, "CONFIRMED"),
        cell(session.flagged_count, "FLAGGED"),
        cell(session.skipped_count, "SKIPPED"),
        cell(session.pending_count, "NOT REVIEWED"),
    ]]
    tbl = Table(data, colWidths=[col_w] * 5, rowHeights=[36])
    tbl.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.75, ST_BLACK),
        ("LINEBEFORE", (1, 0), (-1, -1), 0.5, ST_GREY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return tbl


def _st_items_table(entries_qs):
    header = [
        Paragraph("ASSET ID", st_col_header_style),
        Paragraph("TYPE", st_col_header_style),
        Paragraph("MAKE / MODEL", st_col_header_style),
        Paragraph("LAST UPDATED", st_col_header_style),
        Paragraph("OUTCOME", st_col_header_style),
        Paragraph("QTY", st_col_header_style),
        Paragraph("NOTES", st_col_header_style),
    ]
    data = [header]

    col_widths = [
        CONTENT_WIDTH * 0.18,
        CONTENT_WIDTH * 0.10,
        CONTENT_WIDTH * 0.22,
        CONTENT_WIDTH * 0.12,
        CONTENT_WIDTH * 0.11,
        CONTENT_WIDTH * 0.06,
        CONTENT_WIDTH * 0.21,
    ]

    row_styles = []

    for i, entry in enumerate(entries_qs, start=1):
        asset = entry.asset
        outcome = entry.outcome

        last_upd = asset.last_updated_date.strftime("%d %b %Y") if asset.last_updated_date else "Never"
        qty_text = str(asset.qty) if asset.qty > 1 else ""
        outcome_label = entry.get_outcome_display()

        if outcome == "CONFIRMED":
            outcome_para = Paragraph(outcome_label, st_outcome_confirmed)
        elif outcome == "FLAGGED":
            outcome_para = Paragraph(outcome_label, st_outcome_flagged)
        else:
            outcome_para = Paragraph(outcome_label, st_outcome_other)

        row = [
            Paragraph(asset.asset_id, st_asset_id_style),
            Paragraph(asset.get_asset_type_display(), st_cell_style),
            Paragraph(asset.make_model or "-", st_cell_style),
            Paragraph(last_upd, st_cell_style),
            outcome_para,
            Paragraph(qty_text, st_cell_style),
            Paragraph(entry.notes or "", st_cell_style),
        ]
        data.append(row)

        if i % 2 == 0:
            row_styles.append(("BACKGROUND", (0, i), (-1, i), ST_ROW_ALT))

        # Gold left rule on flagged rows
        if outcome == "FLAGGED":
            row_styles.append(("LINEAFTER", (0, i), (0, i), 3, ST_GOLD))

    tbl = Table(data, colWidths=col_widths, repeatRows=1)
    style = [
        ("LINEBELOW", (0, 0), (-1, 0), 1.2, ST_BLACK),
        ("LINEBELOW", (0, 1), (-1, -1), 0.5, ST_GREY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("ALIGN", (4, 0), (5, -1), "CENTER"),
    ] + row_styles
    tbl.setStyle(TableStyle(style))
    return tbl


def build_stocktake_pdf(session):
    """Returns PDF bytes for a stock take session. White page, gold header,
    matching the kit checklist visual style."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=MARGIN,
        title=f"{session.display_label} - Stock Take",
    )

    entries = session.entries.select_related("asset", "reviewed_by").order_by(
        "asset__asset_type", "asset__asset_id"
    )

    story = [
        _st_title_band(session),
        Spacer(1, 8),
        _st_summary_bar(session),
        Spacer(1, 12),
        _st_items_table(entries),
    ]

    doc.build(story)
    return buf.getvalue()
