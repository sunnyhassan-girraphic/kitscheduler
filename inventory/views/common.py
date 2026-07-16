"""Shared date/range/table-building helpers used by both the dashboard and
the timeline views. Nothing in here is domain-specific (kits, staff, vans,
etc.) - it's generic "lay bookings out across a date grid" plumbing."""
import datetime

VIEW_DAYS = 14
STEP_DAYS = 7


def _is_weekend(d):
    return d.weekday() >= 5


def _date_range(anchor, days=VIEW_DAYS):
    return [anchor + datetime.timedelta(days=i) for i in range(days)]


def _month_range(anchor):
    first = anchor.replace(day=1)
    if first.month == 12:
        next_month = first.replace(year=first.year + 1, month=1)
    else:
        next_month = first.replace(month=first.month + 1)
    last = next_month - datetime.timedelta(days=1)
    return [first + datetime.timedelta(days=i) for i in range((last - first).days + 1)]


def _parse_anchor(request, range_mode="week"):
    raw = request.GET.get("start")
    try:
        anchor = datetime.date.fromisoformat(raw) if raw else datetime.date.today()
    except ValueError:
        anchor = datetime.date.today()
    if range_mode == "month":
        return anchor.replace(day=1)
    monday = anchor - datetime.timedelta(days=anchor.weekday())
    return monday


def _build_rows(items, bookings_by_item_id, days, overlap_ok=False):
    day_index = {d: i for i, d in enumerate(days)}
    rows = []
    for item in items:
        item_bookings = bookings_by_item_id.get(item.id, [])
        cells = []
        for d in days:
            hits = [b for b in item_bookings if b.start_date <= d <= b.end_date]
            cells.append({
                "date": d,
                "booking": hits[0] if hits else None,
                "is_start": bool(hits and hits[0].start_date == d),
                "is_overlap": overlap_ok and len(hits) > 1,
                "is_weekend": _is_weekend(d),
            })

        visible = [b for b in item_bookings if b.start_date <= days[-1] and b.end_date >= days[0]]
        visible.sort(key=lambda b: (b.start_date, b.end_date))

        lane_ends = []
        spans = []
        for b in visible:
            clipped_start = max(b.start_date, days[0])
            clipped_end = min(b.end_date, days[-1])
            col_start = day_index[clipped_start]
            col_end = day_index[clipped_end]

            lane = None
            for i, end_col in enumerate(lane_ends):
                if end_col < col_start:
                    lane = i
                    lane_ends[i] = col_end
                    break
            if lane is None:
                lane = len(lane_ends)
                lane_ends.append(col_end)

            spans.append({
                "booking": b,
                "color": b.job.resolve_color(),
                "grid_col_start": col_start + 1,
                "grid_col_end": col_end + 2,
                "grid_row": lane + 1,
                "continues_before": b.start_date < days[0],
                "continues_after": b.end_date > days[-1],
            })

        rows.append({
            "item": item,
            "cells": cells,
            "spans": spans,
            "lane_count": max(len(lane_ends), 1),
        })
    return rows


def _week_availability(items, bookings_by_item_id, week_days):
    fully_free = 0
    partially_free = 0
    for item in items:
        item_bookings = bookings_by_item_id.get(item.id, [])
        booked_days = sum(
            1 for d in week_days
            if any(b.start_date <= d <= b.end_date for b in item_bookings)
        )
        if booked_days == 0:
            fully_free += 1
        elif booked_days < len(week_days):
            partially_free += 1
    return fully_free, partially_free


