"""Leave Tracker - staff holiday booking and balances."""

from __future__ import annotations

import calendar as pycal
import datetime as dt
import os
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, abort, flash, redirect, render_template, request, url_for
from flask_login import LoginManager, current_user, login_required, login_user, logout_user

import services
from models import Adjustment, Allowance, Employee, LeaveRequest, Settings, db

load_dotenv()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        return url
    # On Railway, mount a Volume at /data and the DB persists across deploys.
    if os.path.isdir("/data") and os.access("/data", os.W_OK):
        return "sqlite:////data/leave_tracker.db"
    return "sqlite:///leave_tracker.db"


def _secret_key() -> str:
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    # Persist a generated key so sessions survive restarts without configuration.
    folder = "/data" if os.path.isdir("/data") and os.access("/data", os.W_OK) else os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(folder, ".secret_key")
    try:
        with open(path) as fh:
            return fh.read().strip() or os.urandom(32).hex()
    except OSError:
        key = os.urandom(32).hex()
        try:
            with open(path, "w") as fh:
                fh.write(key)
        except OSError:
            pass
        return key


app = Flask(__name__)
app.config["SECRET_KEY"] = _secret_key()
app.config["SQLALCHEMY_DATABASE_URI"] = _database_url()
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

db.init_app(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Please sign in to continue."


@login_manager.user_loader
def load_user(user_id: str):
    return db.session.get(Employee, int(user_id))


with app.app_context():
    db.create_all()
    Settings.get()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def parse_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def parse_float(value: str | None, default: float = 0.0) -> float:
    try:
        return float((value or "").strip() or default)
    except ValueError:
        return default


def selected_year_start() -> dt.date:
    settings = Settings.get()
    chosen = parse_date(request.args.get("year"))
    if chosen:
        return services.leave_year_start(chosen, settings)
    return services.leave_year_start(dt.date.today(), settings)


def year_context(year_start: dt.date) -> dict:
    return {
        "year_start": year_start,
        "year_end": services.leave_year_end(year_start),
        "year_label": services.leave_year_label(year_start),
        "year_options": [
            (ys, services.leave_year_label(ys)) for ys in services.available_leave_years()
        ],
    }


@app.context_processor
def inject_globals():
    pending_badge = None
    if current_user.is_authenticated and current_user.is_admin:
        pending_badge = LeaveRequest.query.filter_by(status=LeaveRequest.STATUS_PENDING).count() or None
    return {
        "settings": Settings.get(),
        "today": dt.date.today(),
        "LeaveRequest": LeaveRequest,
        "pending_badge": pending_badge,
    }


@app.template_filter("hours")
def fmt_hours(value: float | None) -> str:
    if value is None:
        return "0h"
    value = float(value)
    if value == int(value):
        return f"{int(value)}h"
    return f"{value:.1f}h"


@app.template_filter("signed_hours")
def fmt_signed_hours(value: float) -> str:
    s = fmt_hours(abs(value))
    return f"+{s}" if value >= 0 else f"-{s}"


@app.template_filter("date")
def fmt_date(value: dt.date | None, fmt: str = "%a %d %b %Y") -> str:
    return value.strftime(fmt) if value else ""


@app.template_filter("daterange")
def fmt_daterange(r: LeaveRequest) -> str:
    if r.start_date == r.end_date:
        return r.start_date.strftime("%a %d %b %Y")
    if r.start_date.year == r.end_date.year:
        return f"{r.start_date.strftime('%a %d %b')} – {r.end_date.strftime('%a %d %b %Y')}"
    return f"{r.start_date.strftime('%d %b %Y')} – {r.end_date.strftime('%d %b %Y')}"


@app.template_filter("datetime")
def fmt_datetime(value: dt.datetime | None) -> str:
    return value.strftime("%d %b %Y %H:%M") if value else ""


@app.template_filter("colour")
def employee_colour(employee: Employee) -> int:
    return employee.id % 10


@app.before_request
def guard_setup_and_password():
    endpoint = request.endpoint or ""
    if endpoint.startswith("static"):
        return None
    if Employee.query.count() == 0 and endpoint != "setup":
        return redirect(url_for("setup"))
    if (
        current_user.is_authenticated
        and current_user.must_change_password
        and endpoint not in {"change_password", "logout"}
    ):
        flash("Please choose a new password before continuing.", "info")
        return redirect(url_for("change_password"))
    return None


# ---------------------------------------------------------------------------
# Setup, auth
# ---------------------------------------------------------------------------


@app.route("/setup", methods=["GET", "POST"])
def setup():
    if Employee.query.count() > 0:
        return redirect(url_for("login"))
    if request.method == "POST":
        first = request.form.get("first_name", "").strip()
        last = request.form.get("last_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        pw = request.form.get("password", "")
        company = request.form.get("company_name", "").strip()
        if not (first and email and pw):
            flash("First name, email and password are required.", "error")
        elif len(pw) < 6:
            flash("Password must be at least 6 characters.", "error")
        else:
            admin = Employee(first_name=first, last_name=last, email=email, role=Employee.ROLE_ADMIN)
            admin.set_password(pw)
            db.session.add(admin)
            s = Settings.get()
            if company:
                s.company_name = company
            db.session.commit()
            login_user(admin)
            flash("Welcome! Your admin account is ready. Add your staff next.", "success")
            return redirect(url_for("admin_employees"))
    return render_template("setup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("home"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        pw = request.form.get("password", "")
        user = Employee.query.filter_by(email=email).first()
        if user and user.check_password(pw):
            if not user.is_active_employee:
                flash("This account has been deactivated.", "error")
            else:
                login_user(user, remember=True)
                return redirect(request.args.get("next") or url_for("home"))
        else:
            flash("Email or password incorrect.", "error")
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def home():
    if current_user.is_admin:
        return redirect(url_for("admin_dashboard"))
    return redirect(url_for("my_leave"))


@app.route("/account/password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if not current_user.must_change_password and not current_user.check_password(current):
            flash("Current password is incorrect.", "error")
        elif len(new) < 6:
            flash("New password must be at least 6 characters.", "error")
        elif new != confirm:
            flash("Passwords do not match.", "error")
        else:
            current_user.set_password(new)
            current_user.must_change_password = False
            db.session.commit()
            flash("Password updated.", "success")
            return redirect(url_for("home"))
    return render_template("password.html")


# ---------------------------------------------------------------------------
# Staff views
# ---------------------------------------------------------------------------


@app.route("/me")
@login_required
def my_leave():
    ys = selected_year_start()
    bal = services.balance_for(current_user, ys)
    history = (
        LeaveRequest.query.filter(
            LeaveRequest.employee_id == current_user.id,
            LeaveRequest.start_date >= ys,
            LeaveRequest.start_date <= bal.year_end,
        )
        .order_by(LeaveRequest.start_date.desc())
        .all()
    )
    return render_template("my_leave.html", bal=bal, history=history, **year_context(ys))


@app.route("/me/request", methods=["GET", "POST"])
@login_required
def new_request():
    settings = Settings.get()
    if request.method == "POST":
        start = parse_date(request.form.get("start_date"))
        end = parse_date(request.form.get("end_date")) or start
        hours = parse_float(request.form.get("hours"))
        leave_type = request.form.get("leave_type", LeaveRequest.TYPE_HOLIDAY)
        note = request.form.get("note", "").strip()[:500]

        valid_types = {t for t, _ in LeaveRequest.TYPES}
        if not start:
            flash("Please choose a start date.", "error")
        elif end < start:
            flash("End date cannot be before the start date.", "error")
        elif hours <= 0:
            flash("Hours must be more than zero.", "error")
        elif leave_type not in valid_types:
            flash("Unknown leave type.", "error")
        else:
            ys = services.leave_year_start(start, settings)
            bal = services.balance_for(current_user, ys)
            if leave_type == LeaveRequest.TYPE_HOLIDAY and hours > bal.available_after_pending:
                flash(
                    f"You only have {fmt_hours(bal.available_after_pending)} left to book "
                    f"in the {services.leave_year_label(ys)} leave year (including requests awaiting "
                    f"approval). Reduce the hours or choose unpaid leave.",
                    "error",
                )
            else:
                req = LeaveRequest(
                    employee_id=current_user.id,
                    start_date=start,
                    end_date=end,
                    hours=hours,
                    leave_type=leave_type,
                    note=note,
                    created_by_id=current_user.id,
                )
                db.session.add(req)
                db.session.commit()
                flash("Request sent. You'll see it update here once it's been reviewed.", "success")
                return redirect(url_for("my_leave", year=ys.isoformat()))
    return render_template(
        "request_form.html",
        hours_per_day=current_user.hours_per_day or settings.default_hours_per_day,
        types=LeaveRequest.TYPES,
    )


@app.route("/me/request/<int:req_id>/cancel", methods=["POST"])
@login_required
def cancel_my_request(req_id: int):
    req = db.session.get(LeaveRequest, req_id)
    if not req or req.employee_id != current_user.id:
        abort(404)
    if req.status != LeaveRequest.STATUS_PENDING:
        flash("Only requests still awaiting approval can be withdrawn. Ask a manager to cancel approved leave.", "error")
    else:
        req.status = LeaveRequest.STATUS_CANCELLED
        req.decided_at = dt.datetime.utcnow()
        req.decided_by_id = current_user.id
        db.session.commit()
        flash("Request withdrawn.", "success")
    return redirect(url_for("my_leave", year=services.leave_year_start(req.start_date).isoformat()))


# ---------------------------------------------------------------------------
# Calendar (everyone)
# ---------------------------------------------------------------------------


@app.route("/calendar")
@login_required
def calendar_view():
    today = dt.date.today()
    try:
        year = int(request.args.get("y", today.year))
        month = int(request.args.get("m", today.month))
        dt.date(year, month, 1)
    except (TypeError, ValueError):
        year, month = today.year, today.month

    reqs = services.requests_in_month(year, month)
    first = dt.date(year, month, 1)
    weeks = pycal.Calendar(firstweekday=0).monthdatescalendar(year, month)

    by_day: dict[dt.date, list[LeaveRequest]] = {}
    for week in weeks:
        for day in week:
            by_day[day] = [r for r in reqs if r.covers(day)]

    prev_month = (first - dt.timedelta(days=1)).replace(day=1)
    next_month = (first + dt.timedelta(days=32)).replace(day=1)
    return render_template(
        "calendar.html",
        weeks=weeks,
        by_day=by_day,
        month_label=first.strftime("%B %Y"),
        month=month,
        year=year,
        prev_month=prev_month,
        next_month=next_month,
        employees=Employee.query.filter_by(is_active_employee=True).order_by(Employee.first_name).all(),
    )


# ---------------------------------------------------------------------------
# Admin: dashboard & approvals
# ---------------------------------------------------------------------------


@app.route("/admin")
@admin_required
def admin_dashboard():
    ys = selected_year_start()
    balances = services.balances_for_all(ys)
    pending_count = LeaveRequest.query.filter_by(status=LeaveRequest.STATUS_PENDING).count()
    upcoming = (
        LeaveRequest.query.filter(
            LeaveRequest.status == LeaveRequest.STATUS_APPROVED,
            LeaveRequest.end_date >= dt.date.today(),
        )
        .order_by(LeaveRequest.start_date)
        .limit(12)
        .all()
    )
    return render_template(
        "admin/dashboard.html",
        balances=balances,
        pending_count=pending_count,
        upcoming=upcoming,
        **year_context(ys),
    )


@app.route("/admin/approvals")
@admin_required
def admin_approvals():
    pending = (
        LeaveRequest.query.filter_by(status=LeaveRequest.STATUS_PENDING)
        .order_by(LeaveRequest.start_date)
        .all()
    )
    rows = []
    for r in pending:
        ys = services.leave_year_start(r.start_date)
        bal = services.balance_for(r.employee, ys)
        clashes = [
            c for c in services.requests_overlapping(r.start_date, r.end_date, exclude_id=r.id)
            if c.employee_id != r.employee_id
        ]
        rows.append({"req": r, "bal": bal, "clashes": clashes})
    return render_template("admin/approvals.html", rows=rows)


def _decide(req_id: int, status: str, ok_msg: str):
    req = db.session.get(LeaveRequest, req_id) or abort(404)
    req.status = status
    req.decided_by_id = current_user.id
    req.decided_at = dt.datetime.utcnow()
    req.decision_note = request.form.get("decision_note", "").strip()[:500] or None
    db.session.commit()
    flash(f"{ok_msg} for {req.employee.full_name}.", "success")
    return redirect(request.form.get("next") or url_for("admin_approvals"))


@app.route("/admin/requests/<int:req_id>/approve", methods=["POST"])
@admin_required
def approve_request(req_id: int):
    return _decide(req_id, LeaveRequest.STATUS_APPROVED, "Approved")


@app.route("/admin/requests/<int:req_id>/reject", methods=["POST"])
@admin_required
def reject_request(req_id: int):
    return _decide(req_id, LeaveRequest.STATUS_REJECTED, "Rejected")


@app.route("/admin/requests/<int:req_id>/cancel", methods=["POST"])
@admin_required
def admin_cancel_request(req_id: int):
    return _decide(req_id, LeaveRequest.STATUS_CANCELLED, "Cancelled leave")


@app.route("/admin/requests/<int:req_id>/delete", methods=["POST"])
@admin_required
def delete_request(req_id: int):
    req = db.session.get(LeaveRequest, req_id) or abort(404)
    emp_id = req.employee_id
    db.session.delete(req)
    db.session.commit()
    flash("Entry deleted.", "success")
    return redirect(request.form.get("next") or url_for("admin_employee", emp_id=emp_id))


# ---------------------------------------------------------------------------
# Admin: employees
# ---------------------------------------------------------------------------


@app.route("/admin/employees")
@admin_required
def admin_employees():
    employees = Employee.query.order_by(Employee.is_active_employee.desc(), Employee.first_name).all()
    return render_template("admin/employees.html", employees=employees)


@app.route("/admin/employees/new", methods=["GET", "POST"])
@admin_required
def admin_new_employee():
    settings = Settings.get()
    if request.method == "POST":
        first = request.form.get("first_name", "").strip()
        last = request.form.get("last_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        pw = request.form.get("password", "")
        role = request.form.get("role", Employee.ROLE_STAFF)
        hpd = parse_float(request.form.get("hours_per_day"), settings.default_hours_per_day)
        allowance_hours = parse_float(request.form.get("allowance_hours"))
        if not (first and email and pw):
            flash("First name, email and a starting password are required.", "error")
        elif len(pw) < 6:
            flash("Password must be at least 6 characters.", "error")
        elif Employee.query.filter_by(email=email).first():
            flash("An account with that email already exists.", "error")
        else:
            emp = Employee(
                first_name=first,
                last_name=last,
                email=email,
                role=role if role in (Employee.ROLE_ADMIN, Employee.ROLE_STAFF) else Employee.ROLE_STAFF,
                hours_per_day=hpd,
                job_title=request.form.get("job_title", "").strip() or None,
                start_date=parse_date(request.form.get("start_date")),
                must_change_password=True,
            )
            emp.set_password(pw)
            db.session.add(emp)
            db.session.flush()
            if allowance_hours > 0:
                ys = services.leave_year_start(dt.date.today(), settings)
                db.session.add(Allowance(employee_id=emp.id, year_start=ys, hours=allowance_hours))
            db.session.commit()
            flash(f"{emp.full_name} added. They can sign in with the password you set and will be asked to change it.", "success")
            return redirect(url_for("admin_employee", emp_id=emp.id))
    return render_template("admin/employee_form.html", emp=None)


@app.route("/admin/employees/<int:emp_id>")
@admin_required
def admin_employee(emp_id: int):
    emp = db.session.get(Employee, emp_id) or abort(404)
    ys = selected_year_start()
    bal = services.balance_for(emp, ys)
    allowance = Allowance.query.filter_by(employee_id=emp.id, year_start=ys).first()
    adjustments = (
        Adjustment.query.filter_by(employee_id=emp.id, year_start=ys)
        .order_by(Adjustment.created_at.desc())
        .all()
    )
    history = (
        LeaveRequest.query.filter(
            LeaveRequest.employee_id == emp.id,
            LeaveRequest.start_date >= ys,
            LeaveRequest.start_date <= bal.year_end,
        )
        .order_by(LeaveRequest.start_date.desc())
        .all()
    )
    return render_template(
        "admin/employee_detail.html",
        emp=emp,
        bal=bal,
        allowance=allowance,
        adjustments=adjustments,
        history=history,
        types=LeaveRequest.TYPES,
        **year_context(ys),
    )


@app.route("/admin/employees/<int:emp_id>/edit", methods=["POST"])
@admin_required
def admin_edit_employee(emp_id: int):
    emp = db.session.get(Employee, emp_id) or abort(404)
    email = request.form.get("email", "").strip().lower()
    other = Employee.query.filter(Employee.email == email, Employee.id != emp.id).first()
    if other:
        flash("Another account already uses that email.", "error")
        return redirect(url_for("admin_employee", emp_id=emp.id))
    emp.first_name = request.form.get("first_name", emp.first_name).strip() or emp.first_name
    emp.last_name = request.form.get("last_name", "").strip()
    emp.email = email or emp.email
    emp.job_title = request.form.get("job_title", "").strip() or None
    emp.hours_per_day = parse_float(request.form.get("hours_per_day"), emp.hours_per_day)
    emp.start_date = parse_date(request.form.get("start_date"))
    role = request.form.get("role")
    if role in (Employee.ROLE_ADMIN, Employee.ROLE_STAFF):
        if emp.id == current_user.id and role != Employee.ROLE_ADMIN:
            flash("You can't remove your own admin access.", "error")
        else:
            emp.role = role
    db.session.commit()
    flash("Details saved.", "success")
    return redirect(url_for("admin_employee", emp_id=emp.id))


@app.route("/admin/employees/<int:emp_id>/allowance", methods=["POST"])
@admin_required
def admin_set_allowance(emp_id: int):
    emp = db.session.get(Employee, emp_id) or abort(404)
    ys = services.leave_year_start(parse_date(request.form.get("year_start")) or dt.date.today())
    hours = parse_float(request.form.get("hours"))
    carry = parse_float(request.form.get("carry_over_hours"))
    row = Allowance.query.filter_by(employee_id=emp.id, year_start=ys).first()
    if row is None:
        row = Allowance(employee_id=emp.id, year_start=ys)
        db.session.add(row)
    row.hours = hours
    row.carry_over_hours = carry
    row.notes = request.form.get("notes", "").strip() or None
    db.session.commit()
    flash(f"Allowance for {services.leave_year_label(ys)} saved.", "success")
    return redirect(url_for("admin_employee", emp_id=emp.id, year=ys.isoformat()))


@app.route("/admin/employees/<int:emp_id>/adjustment", methods=["POST"])
@admin_required
def admin_add_adjustment(emp_id: int):
    emp = db.session.get(Employee, emp_id) or abort(404)
    ys = services.leave_year_start(parse_date(request.form.get("year_start")) or dt.date.today())
    hours = parse_float(request.form.get("hours"))
    reason = request.form.get("reason", "").strip()
    if hours == 0 or not reason:
        flash("Enter a non-zero number of hours and a reason.", "error")
    else:
        db.session.add(
            Adjustment(employee_id=emp.id, year_start=ys, hours=hours, reason=reason[:255], created_by_id=current_user.id)
        )
        db.session.commit()
        flash("Adjustment recorded.", "success")
    return redirect(url_for("admin_employee", emp_id=emp.id, year=ys.isoformat()))


@app.route("/admin/adjustments/<int:adj_id>/delete", methods=["POST"])
@admin_required
def admin_delete_adjustment(adj_id: int):
    adj = db.session.get(Adjustment, adj_id) or abort(404)
    emp_id, ys = adj.employee_id, adj.year_start
    db.session.delete(adj)
    db.session.commit()
    flash("Adjustment removed.", "success")
    return redirect(url_for("admin_employee", emp_id=emp_id, year=ys.isoformat()))


@app.route("/admin/employees/<int:emp_id>/leave", methods=["POST"])
@admin_required
def admin_add_leave(emp_id: int):
    """Record leave on behalf of an employee (already approved). Used for history."""
    emp = db.session.get(Employee, emp_id) or abort(404)
    start = parse_date(request.form.get("start_date"))
    end = parse_date(request.form.get("end_date")) or start
    hours = parse_float(request.form.get("hours"))
    leave_type = request.form.get("leave_type", LeaveRequest.TYPE_HOLIDAY)
    if not start or end < start or hours <= 0:
        flash("Check the dates and hours.", "error")
        return redirect(url_for("admin_employee", emp_id=emp.id))
    req = LeaveRequest(
        employee_id=emp.id,
        start_date=start,
        end_date=end,
        hours=hours,
        leave_type=leave_type if leave_type in dict(LeaveRequest.TYPES) else LeaveRequest.TYPE_HOLIDAY,
        status=LeaveRequest.STATUS_APPROVED,
        note=request.form.get("note", "").strip()[:500] or None,
        created_by_id=current_user.id,
        decided_by_id=current_user.id,
        decided_at=dt.datetime.utcnow(),
    )
    db.session.add(req)
    db.session.commit()
    flash(f"Leave recorded for {emp.full_name}.", "success")
    return redirect(url_for("admin_employee", emp_id=emp.id, year=services.leave_year_start(start).isoformat()))


@app.route("/admin/employees/<int:emp_id>/password", methods=["POST"])
@admin_required
def admin_reset_password(emp_id: int):
    emp = db.session.get(Employee, emp_id) or abort(404)
    pw = request.form.get("password", "")
    if len(pw) < 6:
        flash("Password must be at least 6 characters.", "error")
    else:
        emp.set_password(pw)
        emp.must_change_password = True
        db.session.commit()
        flash(f"Password reset for {emp.full_name}. They'll be asked to choose a new one at sign-in.", "success")
    return redirect(url_for("admin_employee", emp_id=emp.id))


@app.route("/admin/employees/<int:emp_id>/toggle-active", methods=["POST"])
@admin_required
def admin_toggle_active(emp_id: int):
    emp = db.session.get(Employee, emp_id) or abort(404)
    if emp.id == current_user.id:
        flash("You can't deactivate your own account.", "error")
    else:
        emp.is_active_employee = not emp.is_active_employee
        db.session.commit()
        flash(f"{emp.full_name} {'reactivated' if emp.is_active_employee else 'deactivated'}.", "success")
    return redirect(url_for("admin_employee", emp_id=emp.id))


# ---------------------------------------------------------------------------
# Admin: settings
# ---------------------------------------------------------------------------


@app.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def admin_settings():
    s = Settings.get()
    if request.method == "POST":
        s.company_name = request.form.get("company_name", s.company_name).strip() or s.company_name
        try:
            month = int(request.form.get("leave_year_start_month", s.leave_year_start_month))
            day = int(request.form.get("leave_year_start_day", s.leave_year_start_day))
            dt.date(2001, month, day)
            s.leave_year_start_month, s.leave_year_start_day = month, day
        except (TypeError, ValueError):
            flash("Leave year start date is not valid.", "error")
            return redirect(url_for("admin_settings"))
        s.default_hours_per_day = parse_float(request.form.get("default_hours_per_day"), s.default_hours_per_day)
        db.session.commit()
        flash("Settings saved.", "success")
        return redirect(url_for("admin_settings"))
    return render_template("admin/settings.html", months=list(enumerate(pycal.month_name))[1:])


@app.errorhandler(403)
def forbidden(_):
    return render_template("error.html", code=403, message="You don't have access to that page."), 403


@app.errorhandler(404)
def not_found(_):
    return render_template("error.html", code=404, message="That page doesn't exist."), 404


if __name__ == "__main__":
    app.run(debug=True)
