"""Flask application – Leave Tracker."""

from __future__ import annotations

import datetime
import os

from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename
from sqlalchemy import func

import qb_client
from models import EmployeeCache, LeaveAdjustment, TimeoffCache, db

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", "sqlite:///leave_tracker.db"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)


def _sync_from_qbo() -> None:
    now = datetime.datetime.utcnow()
    for emp in qb_client.get_employees():
        row = db.session.get(EmployeeCache, emp["id"]) or EmployeeCache(id=emp["id"])
        row.first_name = emp.get("firstName", "")
        row.last_name = emp.get("lastName", "")
        row.email = emp.get("email", "")
        row.status = emp.get("status", "ACTIVE")
        row.job_title = emp.get("jobTitle", "")
        row.department = emp.get("department", "")
        row.last_synced = now
        db.session.merge(row)
    for balance in qb_client.get_timeoff_details():
        existing = TimeoffCache.query.filter_by(
            employee_id=balance["employeeId"],
            policy_id=balance["policyId"],
        ).first()
        if existing is None:
            existing = TimeoffCache(
                employee_id=balance["employeeId"],
                policy_id=balance["policyId"],
            )
        existing.policy_name = balance.get("policyName", "")
        existing.category = balance.get("category", "OTHER")
        existing.accrued_hours = balance.get("accruedHours", 0.0)
        existing.used_hours = balance.get("usedHours", 0.0)
        existing.scheduled_hours = balance.get("scheduledHours", 0.0)
        existing.last_synced = now
        db.session.add(existing)
    db.session.commit()


def _balance_colour(available: float) -> str:
    if available > 40:
        return "balance-green"
    if available >= 10:
        return "balance-amber"
    return "balance-red"


def _build_employee_summary(employee: EmployeeCache) -> dict:
    timeoff_rows = TimeoffCache.query.filter_by(employee_id=employee.id).all()
    total_allocated = sum(r.accrued_hours for r in timeoff_rows)
    total_taken = sum(r.used_hours for r in timeoff_rows)
    total_future = sum(r.scheduled_hours for r in timeoff_rows)
    manual_adj = (
        db.session.query(func.coalesce(func.sum(LeaveAdjustment.hours), 0.0))
        .filter(LeaveAdjustment.employee_id == employee.id)
        .scalar()
    )
    available = total_allocated - total_taken - total_future + manual_adj
    return {
        "employee": employee,
        "allocated": total_allocated,
        "taken": total_taken,
        "future": total_future,
        "manual_adj": manual_adj,
        "available": available,
        "colour": _balance_colour(available),
    }


def create_tables_and_seed() -> None:
    db.create_all()
    if EmployeeCache.query.count() == 0:
        _sync_from_qbo()


with app.app_context():
    create_tables_and_seed()


@app.route("/")
def dashboard() -> str:
    employees = EmployeeCache.query.order_by(
        EmployeeCache.status.desc(), EmployeeCache.last_name
    ).all()
    summaries = [_build_employee_summary(emp) for emp in employees]
    last_sync = db.session.query(func.max(EmployeeCache.last_synced)).scalar()
    return render_template("dashboard.html", summaries=summaries, last_sync=last_sync)


@app.route("/employee/<emp_id>")
def employee_detail(emp_id: str) -> str:
    employee = db.session.get(EmployeeCache, emp_id)
    if employee is None:
        flash("Employee not found.", "error")
        return redirect(url_for("dashboard"))
    timeoff_rows = TimeoffCache.query.filter_by(employee_id=emp_id).all()
    policy_rows = []
    for row in timeoff_rows:
        manual_adj = (
            db.session.query(func.coalesce(func.sum(LeaveAdjustment.hours), 0.0))
            .filter(
                LeaveAdjustment.employee_id == emp_id,
                LeaveAdjustment.policy_id == row.policy_id,
            )
            .scalar()
        )
        avail = row.accrued_hours - row.used_hours - row.scheduled_hours + manual_adj
        policy_rows.append({
            "policy_name": row.policy_name,
            "category": row.category,
            "allocated": row.accrued_hours,
            "taken": row.used_hours,
            "future": row.scheduled_hours,
            "manual_adj": manual_adj,
            "available": avail,
            "colour": _balance_colour(avail),
        })
    adjustments = (
        LeaveAdjustment.query.filter_by(employee_id=emp_id)
        .order_by(LeaveAdjustment.created_at.desc())
        .all()
    )
    payslips = qb_client.get_employee_payslips(emp_id)
    policies_for_form = [{"id": row.policy_id, "name": row.policy_name} for row in timeoff_rows]
    return render_template(
        "employee_detail.html",
        employee=employee,
        policy_rows=policy_rows,
        adjustments=adjustments,
        payslips=payslips,
        policies=policies_for_form,
    )


@app.route("/employee/<emp_id>/adjust", methods=["POST"])
def add_adjustment(emp_id: str) -> str:
    employee = db.session.get(EmployeeCache, emp_id)
    if employee is None:
        flash("Employee not found.", "error")
        return redirect(url_for("dashboard"))
    policy_id = request.form.get("policy_id", "").strip()
    hours_str = request.form.get("hours", "0").strip()
    reason = request.form.get("reason", "").strip()
    try:
        hours = float(hours_str)
    except ValueError:
        flash("Invalid hours value.", "error")
        return redirect(url_for("employee_detail", emp_id=emp_id))
    if not reason:
        flash("A reason is required.", "error")
        return redirect(url_for("employee_detail", emp_id=emp_id))
    adj = LeaveAdjustment(employee_id=emp_id, policy_id=policy_id, hours=hours, reason=reason)
    db.session.add(adj)
    db.session.commit()
    flash(f"Adjustment of {hours:+.1f} hrs recorded for {employee.full_name()}.", "success")
    return redirect(url_for("employee_detail", emp_id=emp_id))


@app.route("/import", methods=["GET", "POST"])
def import_csv() -> str:
    if request.method == "GET":
        return render_template("import.html")

    emp_file = request.files.get("employees_csv")
    timeoff_file = request.files.get("timeoff_csv")

    if not emp_file or emp_file.filename == "":
        flash("Please upload the employees CSV.", "error")
        return render_template("import.html")

    try:
        emp_bytes = emp_file.read()
        employees = qb_client.parse_employees_csv(emp_bytes)
        if not employees:
            flash("No employees found in CSV — check the file format.", "error")
            return render_template("import.html")

        timeoff_records: list[dict] = []
        if timeoff_file and timeoff_file.filename != "":
            to_bytes = timeoff_file.read()
            timeoff_records = qb_client.parse_timeoff_csv(to_bytes, employees)

        now = datetime.datetime.utcnow()

        for emp in employees:
            row = db.session.get(EmployeeCache, emp["id"]) or EmployeeCache(id=emp["id"])
            row.first_name  = emp.get("firstName", "")
            row.last_name   = emp.get("lastName", "")
            row.email       = emp.get("email", "")
            row.status      = emp.get("status", "ACTIVE")
            row.job_title   = emp.get("jobTitle", "")
            row.department  = emp.get("department", "")
            row.last_synced = now
            db.session.merge(row)

        for balance in timeoff_records:
            existing = TimeoffCache.query.filter_by(
                employee_id=balance["employeeId"],
                policy_id=balance["policyId"],
            ).first()
            if existing is None:
                existing = TimeoffCache(
                    employee_id=balance["employeeId"],
                    policy_id=balance["policyId"],
                )
            existing.policy_name    = balance.get("policyName", "")
            existing.category       = balance.get("category", "OTHER")
            existing.accrued_hours  = balance.get("accruedHours", 0.0)
            existing.used_hours     = balance.get("usedHours", 0.0)
            existing.scheduled_hours = balance.get("scheduledHours", 0.0)
            existing.last_synced    = now
            db.session.add(existing)

        db.session.commit()

        msg = f"Imported {len(employees)} employees"
        if timeoff_records:
            msg += f" and {len(timeoff_records)} time-off records"
        flash(msg + ".", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Import failed: {exc}", "error")

    return redirect(url_for("dashboard"))


@app.route("/api/refresh")
def api_refresh() -> str:
    try:
        _sync_from_qbo()
        flash("Data refreshed from QuickBooks successfully.", "success")
    except Exception as exc:
        flash(f"Refresh failed: {exc}", "error")
    return redirect(url_for("dashboard"))


@app.template_filter("fmt_hours")
def fmt_hours(value: float) -> str:
    return f"{value:.1f} hrs"


@app.template_filter("fmt_datetime")
def fmt_datetime(value: datetime.datetime | None) -> str:
    if value is None:
        return "never"
    return value.strftime("%d %b %Y %H:%M")


if __name__ == "__main__":
    app.run(debug=True)
