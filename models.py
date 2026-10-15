"""Database models for the Leave Tracker."""

from __future__ import annotations

import datetime as dt

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()


class Settings(db.Model):
    """Single-row table holding company-wide configuration."""

    __tablename__ = "settings"

    id = db.Column(db.Integer, primary_key=True)
    company_name = db.Column(db.String(120), nullable=False, default="Leave Tracker")
    leave_year_start_month = db.Column(db.Integer, nullable=False, default=4)
    leave_year_start_day = db.Column(db.Integer, nullable=False, default=1)
    default_hours_per_day = db.Column(db.Float, nullable=False, default=8.0)

    @classmethod
    def get(cls) -> "Settings":
        row = db.session.get(cls, 1)
        if row is None:
            row = cls(id=1)
            db.session.add(row)
            db.session.commit()
        return row


class Employee(UserMixin, db.Model):
    """A member of staff. Every employee can log in."""

    __tablename__ = "employees"

    ROLE_ADMIN = "admin"
    ROLE_STAFF = "staff"

    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(200), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default=ROLE_STAFF)
    is_active_employee = db.Column(db.Boolean, nullable=False, default=True)
    hours_per_day = db.Column(db.Float, nullable=False, default=8.0)
    job_title = db.Column(db.String(120))
    start_date = db.Column(db.Date)
    qb_employee_id = db.Column(db.String(64))
    must_change_password = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=dt.datetime.utcnow)

    allowances = db.relationship("Allowance", backref="employee", cascade="all, delete-orphan")
    requests = db.relationship(
        "LeaveRequest",
        backref="employee",
        cascade="all, delete-orphan",
        foreign_keys="LeaveRequest.employee_id",
    )
    adjustments = db.relationship(
        "Adjustment",
        backref="employee",
        cascade="all, delete-orphan",
        foreign_keys="Adjustment.employee_id",
    )

    # --- auth helpers -------------------------------------------------
    def set_password(self, raw: str) -> None:
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)

    @property
    def is_admin(self) -> bool:
        return self.role == self.ROLE_ADMIN

    @property
    def is_active(self) -> bool:  # flask-login hook
        return self.is_active_employee

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def initials(self) -> str:
        return f"{self.first_name[:1]}{self.last_name[:1]}".upper()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Employee {self.id} {self.full_name}>"


class Allowance(db.Model):
    """Hours of holiday granted to one employee for one leave year."""

    __tablename__ = "allowances"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False, index=True)
    year_start = db.Column(db.Date, nullable=False, index=True)
    hours = db.Column(db.Float, nullable=False, default=0.0)
    carry_over_hours = db.Column(db.Float, nullable=False, default=0.0)
    notes = db.Column(db.String(255))

    __table_args__ = (db.UniqueConstraint("employee_id", "year_start", name="uq_allowance_year"),)

    @property
    def total(self) -> float:
        return (self.hours or 0.0) + (self.carry_over_hours or 0.0)


class LeaveRequest(db.Model):
    """A request for time off. Only type 'holiday' deducts from the allowance."""

    __tablename__ = "leave_requests"

    TYPE_HOLIDAY = "holiday"
    TYPE_SICK = "sick"
    TYPE_UNPAID = "unpaid"
    TYPE_OTHER = "other"
    TYPES = [
        (TYPE_HOLIDAY, "Holiday"),
        (TYPE_SICK, "Sick"),
        (TYPE_UNPAID, "Unpaid leave"),
        (TYPE_OTHER, "Other"),
    ]

    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_CANCELLED = "cancelled"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False, index=True)
    start_date = db.Column(db.Date, nullable=False, index=True)
    end_date = db.Column(db.Date, nullable=False, index=True)
    hours = db.Column(db.Float, nullable=False)
    leave_type = db.Column(db.String(20), nullable=False, default=TYPE_HOLIDAY)
    status = db.Column(db.String(20), nullable=False, default=STATUS_PENDING, index=True)
    note = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, nullable=False, default=dt.datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("employees.id"))
    decided_by_id = db.Column(db.Integer, db.ForeignKey("employees.id"))
    decided_at = db.Column(db.DateTime)
    decision_note = db.Column(db.String(500))

    created_by = db.relationship("Employee", foreign_keys=[created_by_id])
    decided_by = db.relationship("Employee", foreign_keys=[decided_by_id])

    @property
    def type_label(self) -> str:
        return dict(self.TYPES).get(self.leave_type, self.leave_type.title())

    @property
    def days(self) -> int:
        return (self.end_date - self.start_date).days + 1

    @property
    def is_single_day(self) -> bool:
        return self.start_date == self.end_date

    @property
    def counts_against_allowance(self) -> bool:
        return self.leave_type == self.TYPE_HOLIDAY

    def covers(self, day: dt.date) -> bool:
        return self.start_date <= day <= self.end_date


class Adjustment(db.Model):
    """Manual +/- hours applied to one employee's allowance for one leave year."""

    __tablename__ = "adjustments"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False, index=True)
    year_start = db.Column(db.Date, nullable=False, index=True)
    hours = db.Column(db.Float, nullable=False)
    reason = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=dt.datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("employees.id"))

    created_by = db.relationship("Employee", foreign_keys=[created_by_id])
