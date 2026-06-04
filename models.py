from __future__ import annotations
import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class LeaveAdjustment(db.Model):
    __tablename__ = "leave_adjustments"
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.String(64), nullable=False, index=True)
    policy_id = db.Column(db.String(64), nullable=False)
    hours = db.Column(db.Float, nullable=False)
    reason = db.Column(db.String(256), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.datetime.utcnow)

class EmployeeCache(db.Model):
    __tablename__ = "employee_cache"
    id = db.Column(db.String(64), primary_key=True)
    first_name = db.Column(db.String(128), nullable=False)
    last_name = db.Column(db.String(128), nullable=False)
    email = db.Column(db.String(256))
    status = db.Column(db.String(32), nullable=False, default="ACTIVE")
    job_title = db.Column(db.String(128))
    department = db.Column(db.String(128))
    last_synced = db.Column(db.DateTime, nullable=False, default=datetime.datetime.utcnow)

    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

class TimeoffCache(db.Model):
    __tablename__ = "timeoff_cache"
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.String(64), nullable=False, index=True)
    policy_id = db.Column(db.String(64), nullable=False)
    policy_name = db.Column(db.String(128), nullable=False)
    category = db.Column(db.String(32), nullable=False)
    accrued_hours = db.Column(db.Float, nullable=False, default=0.0)
    used_hours = db.Column(db.Float, nullable=False, default=0.0)
    scheduled_hours = db.Column(db.Float, nullable=False, default=0.0)
    last_synced = db.Column(db.DateTime, nullable=False, default=datetime.datetime.utcnow)
    __table_args__ = (db.UniqueConstraint("employee_id", "policy_id", name="uq_emp_policy"),)
