"""Leave-year maths and balance calculations."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import and_

from models import Adjustment, Allowance, Employee, LeaveRequest, Settings, db


# ---------------------------------------------------------------------------
# Leave year helpers
# ---------------------------------------------------------------------------


def leave_year_start(for_date: dt.date, settings: Settings | None = None) -> dt.date:
    """Return the first day of the leave year containing `for_date`."""
    settings = settings or Settings.get()
    month, day = settings.leave_year_start_month, settings.leave_year_start_day
    candidate = dt.date(for_date.year, month, day)
    if for_date < candidate:
        candidate = dt.date(for_date.year - 1, month, day)
    return candidate


def leave_year_end(year_start: dt.date) -> dt.date:
    """Last day of the leave year that starts on `year_start`."""
    next_start = dt.date(year_start.year + 1, year_start.month, year_start.day)
    return next_start - dt.timedelta(days=1)


def leave_year_label(year_start: dt.date) -> str:
    end = leave_year_end(year_start)
    if year_start.year == end.year:
        return str(year_start.year)
    return f"{year_start.year}/{str(end.year)[-2:]}"


def available_leave_years(settings: Settings | None = None, today: dt.date | None = None) -> list[dt.date]:
    """Leave years worth showing in dropdowns: any with data, plus current and next."""
    settings = settings or Settings.get()
    today = today or dt.date.today()
    current = leave_year_start(today, settings)
    years = {current, dt.date(current.year + 1, current.month, current.day)}
    for (ys,) in db.session.query(Allowance.year_start).distinct():
        years.add(ys)
    for (sd,) in db.session.query(LeaveRequest.start_date).distinct():
        years.add(leave_year_start(sd, settings))
    return sorted(years, reverse=True)


# ---------------------------------------------------------------------------
# Balances
# ---------------------------------------------------------------------------


@dataclass
class Balance:
    employee: Employee
    year_start: dt.date
    year_end: dt.date
    allowance_hours: float = 0.0
    carry_over_hours: float = 0.0
    adjustment_hours: float = 0.0
    taken_hours: float = 0.0
    booked_hours: float = 0.0
    pending_hours: float = 0.0
    sick_hours: float = 0.0
    unpaid_hours: float = 0.0
    taken: list[LeaveRequest] = field(default_factory=list)
    booked: list[LeaveRequest] = field(default_factory=list)
    pending: list[LeaveRequest] = field(default_factory=list)
    has_allowance: bool = False

    @property
    def allocated(self) -> float:
        return self.allowance_hours + self.carry_over_hours + self.adjustment_hours

    @property
    def used(self) -> float:
        return self.taken_hours + self.booked_hours

    @property
    def available(self) -> float:
        return self.allocated - self.used

    @property
    def available_after_pending(self) -> float:
        return self.available - self.pending_hours

    @property
    def percent_used(self) -> int:
        if self.allocated <= 0:
            return 0
        return max(0, min(100, round(self.used / self.allocated * 100)))

    @property
    def status(self) -> str:
        """Traffic-light class for the available figure."""
        if self.available < 0:
            return "danger"
        if self.allocated <= 0:
            return "muted"
        ratio = self.available / self.allocated
        if ratio > 0.25:
            return "good"
        if ratio > 0.10:
            return "warn"
        return "danger"


def balance_for(employee: Employee, year_start: dt.date, today: dt.date | None = None) -> Balance:
    today = today or dt.date.today()
    year_end = leave_year_end(year_start)
    bal = Balance(employee=employee, year_start=year_start, year_end=year_end)

    allowance = Allowance.query.filter_by(employee_id=employee.id, year_start=year_start).first()
    if allowance:
        bal.has_allowance = True
        bal.allowance_hours = allowance.hours or 0.0
        bal.carry_over_hours = allowance.carry_over_hours or 0.0

    bal.adjustment_hours = sum(
        a.hours
        for a in Adjustment.query.filter_by(employee_id=employee.id, year_start=year_start).all()
    )

    reqs = (
        LeaveRequest.query.filter(
            LeaveRequest.employee_id == employee.id,
            LeaveRequest.start_date >= year_start,
            LeaveRequest.start_date <= year_end,
            LeaveRequest.status.in_([LeaveRequest.STATUS_APPROVED, LeaveRequest.STATUS_PENDING]),
        )
        .order_by(LeaveRequest.start_date)
        .all()
    )

    for r in reqs:
        if r.status == LeaveRequest.STATUS_PENDING:
            if r.counts_against_allowance:
                bal.pending.append(r)
                bal.pending_hours += r.hours
            continue
        # approved
        if r.leave_type == LeaveRequest.TYPE_SICK:
            bal.sick_hours += r.hours
            continue
        if r.leave_type == LeaveRequest.TYPE_UNPAID:
            bal.unpaid_hours += r.hours
            continue
        if not r.counts_against_allowance:
            continue
        if r.start_date <= today:
            bal.taken.append(r)
            bal.taken_hours += r.hours
        else:
            bal.booked.append(r)
            bal.booked_hours += r.hours

    return bal


def balances_for_all(year_start: dt.date, include_inactive: bool = False) -> list[Balance]:
    q = Employee.query.order_by(Employee.first_name, Employee.last_name)
    if not include_inactive:
        q = q.filter_by(is_active_employee=True)
    return [balance_for(e, year_start) for e in q.all()]


# ---------------------------------------------------------------------------
# Calendar / clash helpers
# ---------------------------------------------------------------------------


def requests_overlapping(start: dt.date, end: dt.date, exclude_id: int | None = None) -> list[LeaveRequest]:
    """Approved or pending requests from anyone that overlap [start, end]."""
    q = LeaveRequest.query.filter(
        and_(LeaveRequest.start_date <= end, LeaveRequest.end_date >= start),
        LeaveRequest.status.in_([LeaveRequest.STATUS_APPROVED, LeaveRequest.STATUS_PENDING]),
    )
    if exclude_id:
        q = q.filter(LeaveRequest.id != exclude_id)
    return q.order_by(LeaveRequest.start_date).all()


def requests_in_month(year: int, month: int) -> list[LeaveRequest]:
    first = dt.date(year, month, 1)
    last = dt.date(year + (month == 12), (month % 12) + 1, 1) - dt.timedelta(days=1)
    return requests_overlapping(first, last)


def working_days(start: dt.date, end: dt.date) -> int:
    """Mon-Fri days between two dates inclusive."""
    n = 0
    d = start
    while d <= end:
        if d.weekday() < 5:
            n += 1
        d += dt.timedelta(days=1)
    return n
