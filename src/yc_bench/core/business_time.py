from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, time
from decimal import Decimal

WORKDAY_START = time(9, 0)
WORKDAY_END = time(18, 0)
WORK_HOURS_PER_DAY = Decimal("9")


@dataclass(frozen=True)
class BusinessCalendar:
    workday_start: time = WORKDAY_START
    workday_end: time = WORKDAY_END


DEFAULT_CALENDAR = BusinessCalendar()


def is_weekday(ts):
    return ts.weekday() < 5


def is_business_time(ts, cal=DEFAULT_CALENDAR):
    if not is_weekday(ts):
        return False
    t = ts.timetz().replace(tzinfo=None) if ts.tzinfo else ts.time()
    return cal.workday_start <= t < cal.workday_end


def _day_start(ts, cal):
    return ts.replace(
        hour=cal.workday_start.hour,
        minute=cal.workday_start.minute,
        second=0,
        microsecond=0,
    )


def _day_end(ts, cal):
    return ts.replace(
        hour=cal.workday_end.hour,
        minute=cal.workday_end.minute,
        second=0,
        microsecond=0,
    )


def _next_weekday_start(ts, cal):
    cur = _day_start(ts, cal)
    while not is_weekday(cur):
        cur += timedelta(days=1)
        cur = _day_start(cur, cal)
    return cur


def next_business_time(ts, cal):
    if is_business_time(ts, cal):
        return ts
    if not is_weekday(ts):
        return _next_weekday_start(ts, cal)

    day_start = _day_start(ts, cal)
    day_end = _day_end(ts, cal)

    if ts < day_start:
        return day_start

    if ts >= day_end:
        return _next_weekday_start(ts + timedelta(days=1), cal)

    raise ValueError(f"No valid business time found after {ts}")


def add_business_hours(ts, hours, cal=DEFAULT_CALENDAR):
    hours = Decimal(str(hours))
    if hours < 0:
        raise ValueError(f"Cannot add negative business hours: {hours}")
    if hours == 0:
        return next_business_time(ts, cal)

    cur = next_business_time(ts, cal)
    remaining = hours

    while remaining > 0:
        day_end = _day_end(cur, cal)
        available = Decimal(str((day_end - cur).total_seconds())) / Decimal("3600")

        if remaining <= available:
            return cur + timedelta(seconds=float(remaining * Decimal("3600")))

        remaining -= available
        cur = next_business_time(day_end, cal)

    return cur


def _business_interval_same_day(start, end, cal):
    if end <= start:
        return Decimal("0")
    if not is_weekday(start):
        return Decimal("0")

    day_start = _day_start(start, cal)
    day_end = _day_end(end, cal)

    lo = max(start, day_start)
    hi = min(end, day_end)

    if hi <= lo:
        return Decimal("0")
    return Decimal(str((hi - lo).total_seconds())) / Decimal("3600")


def business_hours_between(t0, t1, cal=DEFAULT_CALENDAR):
    if t1 <= t0:
        return Decimal("0")

    cur = t0
    total = Decimal("0")
    while cur < t1:
        next_midnight = (cur + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        seg_end = min(next_midnight, t1)
        total += _business_interval_same_day(cur, seg_end, cal)
        cur = seg_end

    return total


def business_seconds_between(t0, t1, cal=DEFAULT_CALENDAR):
    return int(
        (business_hours_between(t0, t1, cal) * Decimal("3600")).to_integral_value()
    )


def first_business_of_month(dt, cal=DEFAULT_CALENDAR):
    first = dt.replace(
        day=1,
        hour=cal.workday_start.hour,
        minute=cal.workday_start.minute,
        second=0,
        microsecond=0,
    )
    while not is_weekday(first):
        first += timedelta(days=1)
        first = first.replace(
            hour=cal.workday_start.hour,
            minute=cal.workday_start.minute,
            second=0,
            microsecond=0,
        )
    return first


def iter_monthly_payroll_boundaries(start, end, cal=DEFAULT_CALENDAR):
    if end <= start:
        return []

    cursor = start.replace(
        day=1,
        hour=cal.workday_start.hour,
        minute=cal.workday_start.minute,
        second=0,
        microsecond=0,
    )
    out = []

    while cursor < end:
        boundary = first_business_of_month(cursor, cal)
        if start < boundary <= end:
            out.append(boundary)

        if cursor.month == 12:
            cursor = cursor.replace(year=cursor.year + 1, month=1, day=1)
        else:
            cursor = cursor.replace(month=cursor.month + 1, day=1)

    return out


__all__ = [
    "BusinessCalendar",
    "DEFAULT_CALENDAR",
    "is_weekday",
    "is_business_time",
    "next_business_time",
    "add_business_hours",
    "business_hours_between",
    "business_seconds_between",
    "first_business_of_month",
    "iter_monthly_payroll_boundaries",
]
