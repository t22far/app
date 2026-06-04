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


def _entitlement_hours(employee: EmployeeCache) -> float | None:
    if employee.entitlement_hours is not None:
        return employee.entitlement_hours
    return None


def _build_employee_summary(employee: EmployeeCache) -> dict:
    timeoff_rows = TimeoffCache.query.filter_by(employee_id=employee.id).all()
    ent_hours = _entitlement_hours(employee)
    total_allocated = sum(
        ent_hours if (ent_hours is not None and r.category == "VACATION") else r.accrued_hours
        for r in timeoff_rows
    ) if timeoff_rows else (ent_hours or 0.0)
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
    # Add new columns if upgrading from an older schema
    with db.engine.connect() as conn:
        cols = [row[1] for row in conn.execute(
            db.text("PRAGMA table_info(employee_cache)")
        )]
        for col_def in [
            ("entitlement_hours", "REAL"),
            ("is_director", "INTEGER NOT NULL DEFAULT 0"),
        ]:
            if col_def[0] not in cols:
                conn.execute(db.text(
                    f"ALTER TABLE employee_cache ADD COLUMN {col_def[0]} {col_def[1]}"
                ))
        conn.commit()
    if EmployeeCache.query.count() == 0:
        _sync_from_qbo()


with app.app_context():
    create_tables_and_seed()


@app.route("/")
def dashboard() -> str:
    employees = EmployeeCache.query.filter_by(is_director=False).order_by(
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
    ent_hours = _entitlement_hours(employee)
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
        allocated = (ent_hours if ent_hours is not None and row.category == "VACATION"
                     else row.accrued_hours)
        avail = allocated - row.used_hours - row.scheduled_hours + manual_adj
        policy_rows.append({
            "policy_name": row.policy_name,
            "category": row.category,
            "allocated": allocated,
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


@app.route("/employee/<emp_id>/entitlement", methods=["POST"])
def set_entitlement(emp_id: str) -> str:
    employee = db.session.get(EmployeeCache, emp_id)
    if employee is None:
        flash("Employee not found.", "error")
        return redirect(url_for("dashboard"))
    hours_str = request.form.get("entitlement_hours", "").strip()
    if hours_str == "":
        employee.entitlement_hours = None
        db.session.commit()
        flash(f"Entitlement cleared for {employee.full_name()}.", "success")
    else:
        try:
            hours = float(hours_str)
            if hours < 0:
                raise ValueError
        except ValueError:
            flash("Entitlement must be a positive number of hours.", "error")
            return redirect(url_for("employee_detail", emp_id=emp_id))
        employee.entitlement_hours = hours
        db.session.commit()
        flash(f"Entitlement set to {hours:g} hrs for {employee.full_name()}.", "success")
    return redirect(url_for("employee_detail", emp_id=emp_id))


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

    emp_file      = request.files.get("employees_csv")
    timeoff_file  = request.files.get("timeoff_csv")
    requests_file = request.files.get("leave_requests_xlsx")

    has_employees = emp_file and emp_file.filename != ""
    has_requests  = requests_file and requests_file.filename != ""

    if not has_employees and not has_requests:
        flash("Please upload at least the employees CSV or the leave requests XLSX.", "error")
        return render_template("import.html")

    try:
        now = datetime.datetime.utcnow()
        employees: list[dict] = []
        skipped_directors = 0

        if has_employees:
            emp_bytes = emp_file.read()
            employees = qb_client.parse_employees_csv(emp_bytes)
            if not employees:
                flash("No employees found in the employees CSV — check the file format.", "error")
                return render_template("import.html")

            skipped_directors = 0
            for emp in employees:
                first, last = emp.get("firstName", ""), emp.get("lastName", "")
                if qb_client.is_director(first, last):
                    skipped_directors += 1
                    continue
                row = db.session.get(EmployeeCache, emp["id"]) or EmployeeCache(id=emp["id"])
                row.first_name  = first
                row.last_name   = last
                row.email       = emp.get("email", "")
                row.status      = emp.get("status", "ACTIVE")
                row.job_title   = emp.get("jobTitle", "")
                row.department  = emp.get("department", "")
                row.is_director = False
                known_hrs = qb_client.lookup_entitlement(first, last)
                if known_hrs is not None:
                    row.entitlement_hours = known_hrs
                row.last_synced = now
                db.session.merge(row)
            # Filter directors out of the employee list used for time-off matching
            employees = [
                e for e in employees
                if not qb_client.is_director(e.get("firstName", ""), e.get("lastName", ""))
            ]

        timeoff_count = 0

        if timeoff_file and timeoff_file.filename != "":
            to_bytes = timeoff_file.read()
            timeoff_records = qb_client.parse_timeoff_csv(to_bytes, employees)
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
                existing.policy_name     = balance.get("policyName", "")
                existing.category        = balance.get("category", "OTHER")
                existing.accrued_hours   = balance.get("accruedHours", 0.0)
                existing.used_hours      = balance.get("usedHours", 0.0)
                existing.scheduled_hours = balance.get("scheduledHours", 0.0)
                existing.last_synced     = now
                db.session.add(existing)
            timeoff_count = len(timeoff_records)

        requests_count = 0

        if has_requests:
            xlsx_bytes = requests_file.read()
            leave_totals = qb_client.parse_leave_requests_xlsx(xlsx_bytes)

            # If we got employee IDs directly from QB (numeric), try to map them
            # to employees already in the DB before upserting time-off rows.
            for emp_id, policies in leave_totals.items():
                # Ensure employee exists — create a placeholder if not in DB yet
                existing_emp = db.session.get(EmployeeCache, emp_id)
                if existing_emp is None:
                    # Try to find by matching the derived qb-first-last id format
                    pass  # will simply create time-off rows; employee import handles names

                for policy_id, totals in policies.items():
                    existing = TimeoffCache.query.filter_by(
                        employee_id=emp_id,
                        policy_id=policy_id,
                    ).first()
                    if existing is None:
                        existing = TimeoffCache(
                            employee_id=emp_id,
                            policy_id=policy_id,
                        )
                    existing.policy_name     = totals.get("policy_name", policy_id)
                    existing.category        = totals.get("category", "OTHER")
                    # Preserve accrued_hours — leave requests don't carry allowance data
                    existing.used_hours      = totals.get("used", 0.0)
                    existing.scheduled_hours = totals.get("scheduled", 0.0)
                    existing.last_synced     = now
                    db.session.add(existing)
                    requests_count += 1

        db.session.commit()

        parts = []
        if employees:
            parts.append(f"{len(employees)} employees")
        if timeoff_count:
            parts.append(f"{timeoff_count} time-off balance records")
        if requests_count:
            parts.append(f"{requests_count} leave request summaries")
        if skipped_directors:
            parts.append(f"{skipped_directors} director(s) skipped")
        flash("Imported " + ", ".join(parts) + ".", "success")

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


@app.route("/api/reseed")
def api_reseed() -> str:
    """Clear all employee/timeoff data and reseed from qb_client."""
    try:
        from sqlalchemy import text
        with db.engine.connect() as conn:
            conn.execute(text("DELETE FROM timeoff_cache"))
            conn.execute(text("DELETE FROM employee_cache"))
            conn.commit()
        _sync_from_qbo()
        # Apply entitlement hours from qb_client real data
        now = datetime.datetime.utcnow()
        for e in qb_client.get_employees():
            emp = db.session.get(EmployeeCache, e["id"])
            if emp:
                src = next((r for r in qb_client._REAL_EMPLOYEES if r["id"] == e["id"]), None)
                if src:
                    emp.entitlement_hours = src["entitlementHours"]
                emp.is_director = False
                emp.last_synced = now
        db.session.commit()
        flash("Database reseeded with real employee data.", "success")
    except Exception as exc:
        db.session.rollback()
        flash(f"Reseed failed: {exc}", "error")
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
