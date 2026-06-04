"""QuickBooks import helpers — CSV and XLSX.

Parsers for QuickBooks Online UK payroll exports:

  Employee CSV      Payroll → Employees → export icon → Export to CSV
  Time Off CSV      Payroll → Reports → Time Off Summary → Export
  Leave Requests    Payroll → Time Off → Leave Requests → export (XLSX)
    Columns: Employee Id, Employee First Name, Employee Surname,
             Leave Category, Start Date, End Date, Units, Unit Type, Status
"""

from __future__ import annotations

import csv
import datetime
import io
import re
import unicodedata
from typing import Any


# ---------------------------------------------------------------------------
# Column-name normalisation
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    """Lowercase, strip accents, collapse whitespace/punctuation to underscore."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s


def _find_col(headers: list[str], *candidates: str) -> str | None:
    """Return the first header that matches any candidate (normalised)."""
    norm_headers = {_norm(h): h for h in headers}
    for c in candidates:
        hit = norm_headers.get(_norm(c))
        if hit is not None:
            return hit
    return None


# ---------------------------------------------------------------------------
# Employee CSV parser
# ---------------------------------------------------------------------------

def parse_employees_csv(file_bytes: bytes) -> list[dict[str, Any]]:
    """Parse a QB UK employee export and return a list of employee dicts."""
    text = file_bytes.decode("utf-8-sig")  # strip BOM if present
    reader = csv.DictReader(io.StringIO(text))
    headers = reader.fieldnames or []

    col_first = _find_col(headers, "First name", "First Name", "Firstname", "Given name")
    col_last  = _find_col(headers, "Surname", "Last name", "Last Name", "Lastname", "Family name")
    col_name  = _find_col(headers, "Employee", "Name", "Full name", "Display name")
    col_email = _find_col(headers, "Email address", "Email", "E-mail")
    col_status = _find_col(headers, "Employment status", "Status", "Active")
    col_title  = _find_col(headers, "Job title", "Job Title", "Title", "Role")
    col_dept   = _find_col(headers, "Department", "Team", "Division")

    employees: list[dict[str, Any]] = []
    for i, row in enumerate(reader):
        # Derive first/last name
        if col_first and col_last:
            first = row.get(col_first, "").strip()
            last  = row.get(col_last, "").strip()
        elif col_name:
            parts = row.get(col_name, "").strip().split(None, 1)
            first = parts[0] if parts else ""
            last  = parts[1] if len(parts) > 1 else ""
        else:
            continue  # can't identify the employee — skip row

        if not first and not last:
            continue  # blank row

        # Build a stable ID from name (QB UK employee exports lack a numeric ID)
        emp_id = f"qb-{_norm(first)}-{_norm(last)}" if first or last else f"qb-row-{i}"

        raw_status = (row.get(col_status, "Active") if col_status else "Active").strip()
        status = "ACTIVE" if raw_status.lower() in ("active", "yes", "true", "1", "") else "INACTIVE"

        employees.append({
            "id":         emp_id,
            "firstName":  first,
            "lastName":   last,
            "email":      row.get(col_email, "").strip() if col_email else "",
            "status":     status,
            "jobTitle":   row.get(col_title, "").strip() if col_title else "",
            "department": row.get(col_dept, "").strip()  if col_dept  else "",
        })

    return employees


# ---------------------------------------------------------------------------
# Time Off CSV parser
# ---------------------------------------------------------------------------

# Maps QB UK category strings → canonical category codes used by the app
_CATEGORY_MAP: dict[str, str] = {
    "holiday":          "VACATION",
    "annual leave":     "VACATION",
    "vacation":         "VACATION",
    "sick":             "SICK",
    "sick leave":       "SICK",
    "sickness":         "SICK",
    "maternity":        "OTHER",
    "paternity":        "OTHER",
    "unpaid":           "OTHER",
    "other":            "OTHER",
}


def _map_category(raw: str) -> str:
    return _CATEGORY_MAP.get(raw.lower().strip(), "OTHER")


def _parse_hours(value: str) -> float:
    """Parse hour strings like '40', '40.0', '40h', '1d 4h' → float hours."""
    value = value.strip()
    if not value or value == "-":
        return 0.0
    # 'Xd Yh' style (QB sometimes exports in days+hours)
    day_match = re.match(r"(\d+(?:\.\d+)?)\s*d(?:ays?)?\s*(?:(\d+(?:\.\d+)?)\s*h)?", value, re.I)
    if day_match:
        days  = float(day_match.group(1))
        hours = float(day_match.group(2) or 0)
        return days * 8 + hours
    # plain number, possibly with 'h' suffix
    cleaned = re.sub(r"[^\d.]", "", value)
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def parse_timeoff_csv(
    file_bytes: bytes,
    employees: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Parse a QB UK time-off summary export.

    ``employees`` is the list already returned by parse_employees_csv so we
    can match employee names to IDs.
    """
    text = file_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    headers = reader.fieldnames or []

    col_emp      = _find_col(headers, "Employee", "Name", "Full name")
    col_policy   = _find_col(headers, "Policy", "Leave type", "Type")
    col_category = _find_col(headers, "Category", "Type")
    col_accrued  = _find_col(headers, "Accrued (hours)", "Accrued", "Entitled", "Allowance (hours)", "Allowance")
    col_used     = _find_col(headers, "Used (hours)", "Used", "Taken", "Taken (hours)")
    col_sched    = _find_col(headers, "Scheduled (hours)", "Scheduled", "Booked", "Booked (hours)", "Remaining booked")

    # Build a name → id lookup from the employee list
    name_to_id: dict[str, str] = {}
    for e in employees:
        full = f"{e['firstName']} {e['lastName']}".strip()
        name_to_id[_norm(full)] = e["id"]
        name_to_id[_norm(e["lastName"] + " " + e["firstName"])] = e["id"]  # surname-first fallback

    balances: list[dict[str, Any]] = []
    for row in reader:
        if not col_emp:
            continue
        emp_name = row.get(col_emp, "").strip()
        if not emp_name:
            continue

        emp_id = name_to_id.get(_norm(emp_name))
        if emp_id is None:
            # Attempt partial match on surname
            norm_name = _norm(emp_name)
            for key, eid in name_to_id.items():
                if norm_name in key or key in norm_name:
                    emp_id = eid
                    break
        if emp_id is None:
            continue  # can't match employee — skip row

        policy_name = (row.get(col_policy, "Annual Leave") if col_policy else "Annual Leave").strip()
        raw_category = (row.get(col_category, policy_name) if col_category else policy_name).strip()
        category = _map_category(raw_category) if raw_category else _map_category(policy_name)

        policy_id = f"pol-{_norm(policy_name)}"

        accrued   = _parse_hours(row.get(col_accrued, "0") if col_accrued else "0")
        used      = _parse_hours(row.get(col_used,    "0") if col_used    else "0")
        scheduled = _parse_hours(row.get(col_sched,   "0") if col_sched   else "0")

        balances.append({
            "employeeId":     emp_id,
            "policyId":       policy_id,
            "policyName":     policy_name,
            "category":       category,
            "accruedHours":   accrued,
            "usedHours":      used,
            "scheduledHours": scheduled,
        })

    return balances


# ---------------------------------------------------------------------------
# Leave Requests XLSX parser  (QB UK: Payroll → Time Off → Leave Requests)
# ---------------------------------------------------------------------------

# Statuses QB considers "already taken"
_TAKEN_STATUSES = {"approved", "processed", "complete", "completed"}
# Statuses QB considers "booked but future"
_SCHEDULED_STATUSES = {"pending", "scheduled", "awaiting approval", "requested"}

# Working hours per day assumed when Unit Type is "Days"
_HOURS_PER_DAY = 8.0


def parse_leave_requests_xlsx(
    file_bytes: bytes,
) -> dict[str, dict[str, dict[str, float]]]:
    """Parse a QB UK Leave Requests XLSX export.

    Returns a nested dict:
        { employee_id: { policy_id: { "used": float, "scheduled": float } } }

    employee_id is built from QB's Employee Id column when present, otherwise
    derived from first+surname the same way parse_employees_csv does.
    """
    import openpyxl  # lazy import — not needed for CSV-only flows

    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)

    # Find the leave-requests sheet (first sheet whose name contains "leave" or is sheet 1)
    ws = None
    for name in wb.sheetnames:
        if "leave" in name.lower() or "request" in name.lower():
            ws = wb[name]
            break
    if ws is None:
        ws = wb.active

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return {}

    # Header row
    raw_headers = [str(c).strip() if c is not None else "" for c in rows[0]]
    norm_headers = {_norm(h): i for i, h in enumerate(raw_headers)}

    def col(name: str, *aliases: str) -> int | None:
        for candidate in (name, *aliases):
            idx = norm_headers.get(_norm(candidate))
            if idx is not None:
                return idx
        return None

    i_emp_id   = col("Employee Id", "Employee ID", "EmployeeId")
    i_first    = col("Employee First Name", "First Name", "First name")
    i_surname  = col("Employee Surname", "Surname", "Last Name", "Last name")
    i_category = col("Leave Category", "Category", "Leave Type", "Policy")
    i_start    = col("Start Date", "Start")
    i_end      = col("End Date", "End")
    i_units    = col("Units")
    i_unit_type = col("Unit Type", "Unit")
    i_status   = col("Status")

    totals: dict[str, dict[str, dict[str, float]]] = {}

    for row in rows[1:]:
        def cell(i: int | None) -> str:
            if i is None or i >= len(row):
                return ""
            v = row[i]
            return str(v).strip() if v is not None else ""

        # Derive employee id
        emp_id = cell(i_emp_id)
        if not emp_id:
            first   = cell(i_first)
            surname = cell(i_surname)
            if not first and not surname:
                continue
            emp_id = f"qb-{_norm(first)}-{_norm(surname)}"

        # Leave category → policy id + name
        category_raw = cell(i_category) or "Annual Leave"
        policy_id    = f"pol-{_norm(category_raw)}"
        category     = _map_category(category_raw)

        # Hours: prefer Units column, fall back to date range
        units_str  = cell(i_units)
        unit_type  = cell(i_unit_type).lower()
        hours = 0.0
        if units_str:
            try:
                raw_units = float(units_str)
                hours = raw_units * _HOURS_PER_DAY if "day" in unit_type else raw_units
            except ValueError:
                pass

        if hours == 0.0:
            # Calculate from date range
            start_val = row[i_start] if i_start is not None else None
            end_val   = row[i_end]   if i_end   is not None else None
            if isinstance(start_val, datetime.datetime):
                start_val = start_val.date()
            elif isinstance(start_val, str):
                for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%m/%d/%Y"):
                    try:
                        start_val = datetime.datetime.strptime(start_val, fmt).date()
                        break
                    except ValueError:
                        pass
            if isinstance(end_val, datetime.datetime):
                end_val = end_val.date()
            elif isinstance(end_val, str):
                for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%m/%d/%Y"):
                    try:
                        end_val = datetime.datetime.strptime(end_val, fmt).date()
                        break
                    except ValueError:
                        pass
            if isinstance(start_val, datetime.date) and isinstance(end_val, datetime.date):
                delta = (end_val - start_val).days + 1
                hours = delta * _HOURS_PER_DAY

        # Bucket into used vs scheduled by status
        status_raw = cell(i_status).lower().strip()
        bucket = (
            "used" if status_raw in _TAKEN_STATUSES
            else "scheduled" if status_raw in _SCHEDULED_STATUSES
            else "used"  # default: treat unknown as approved/taken
        )

        emp_totals = totals.setdefault(emp_id, {})
        policy_totals = emp_totals.setdefault(
            policy_id,
            {"policy_name": category_raw, "category": category, "used": 0.0, "scheduled": 0.0},
        )
        policy_totals[bucket] += hours

    return totals


# ---------------------------------------------------------------------------
# Legacy shim — keeps the mock data available for the initial DB seed so the
# app works out-of-the-box before any CSV has been imported.
# ---------------------------------------------------------------------------

import datetime as _dt

_MOCK_EMPLOYEES: list[dict[str, Any]] = [
    {"id": "emp-001", "firstName": "Alice",  "lastName": "Henderson", "email": "alice.henderson@company.com",  "status": "ACTIVE",   "jobTitle": "Senior Engineer",  "department": "Engineering"},
    {"id": "emp-002", "firstName": "Ben",    "lastName": "Okafor",    "email": "ben.okafor@company.com",       "status": "ACTIVE",   "jobTitle": "Product Manager",  "department": "Product"},
    {"id": "emp-003", "firstName": "Clara",  "lastName": "Marchetti", "email": "clara.marchetti@company.com", "status": "ACTIVE",   "jobTitle": "UX Designer",      "department": "Design"},
    {"id": "emp-004", "firstName": "David",  "lastName": "Nkrumah",   "email": "david.nkrumah@company.com",   "status": "ACTIVE",   "jobTitle": "Data Analyst",     "department": "Analytics"},
    {"id": "emp-005", "firstName": "Elena",  "lastName": "Vasquez",   "email": "elena.vasquez@company.com",   "status": "ACTIVE",   "jobTitle": "DevOps Engineer",  "department": "Engineering"},
    {"id": "emp-006", "firstName": "Frank",  "lastName": "Osei",      "email": "frank.osei@company.com",      "status": "ACTIVE",   "jobTitle": "Marketing Lead",   "department": "Marketing"},
    {"id": "emp-007", "firstName": "Grace",  "lastName": "Thornton",  "email": "grace.thornton@company.com",  "status": "INACTIVE", "jobTitle": "HR Coordinator",   "department": "HR"},
    {"id": "emp-008", "firstName": "Hassan", "lastName": "Al-Rashid", "email": "hassan.alrashid@company.com", "status": "ACTIVE",   "jobTitle": "Finance Manager",  "department": "Finance"},
]

_today = _dt.date.today()
_MOCK_PAYSLIPS: dict[str, list[dict[str, Any]]] = {
    emp["id"]: [
        {
            "payslipId":   f"{emp['id']}-ps-{i}",
            "employeeId":  emp["id"],
            "periodStart": (_today - _dt.timedelta(days=30 * (i + 1))).isoformat(),
            "periodEnd":   (_today - _dt.timedelta(days=30 * i + 1)).isoformat(),
            "grossPay":    round(4500.0 + abs(hash(emp["id"]) % 2000), 2),
            "netPay":      round(3200.0 + abs(hash(emp["id"]) % 1500), 2),
        }
        for i in range(3)
    ]
    for emp in _MOCK_EMPLOYEES
}


def get_employees() -> list[dict[str, Any]]:
    return list(_MOCK_EMPLOYEES)


def get_timeoff_details() -> list[dict[str, Any]]:
    _MOCK_TIMEOFF: dict[str, list[dict[str, Any]]] = {
        "emp-001": [{"policyId": "pol-vacation", "policyName": "Annual Leave", "category": "VACATION", "accruedHours": 160.0, "usedHours": 64.0,  "scheduledHours": 16.0}, {"policyId": "pol-sick", "policyName": "Sick Leave", "category": "SICK", "accruedHours": 80.0, "usedHours": 8.0,  "scheduledHours": 0.0}],
        "emp-002": [{"policyId": "pol-vacation", "policyName": "Annual Leave", "category": "VACATION", "accruedHours": 160.0, "usedHours": 120.0, "scheduledHours": 24.0}, {"policyId": "pol-sick", "policyName": "Sick Leave", "category": "SICK", "accruedHours": 80.0, "usedHours": 24.0, "scheduledHours": 8.0}],
        "emp-003": [{"policyId": "pol-vacation", "policyName": "Annual Leave", "category": "VACATION", "accruedHours": 160.0, "usedHours": 32.0,  "scheduledHours": 40.0}, {"policyId": "pol-sick", "policyName": "Sick Leave", "category": "SICK", "accruedHours": 80.0, "usedHours": 0.0,  "scheduledHours": 0.0}],
        "emp-004": [{"policyId": "pol-vacation", "policyName": "Annual Leave", "category": "VACATION", "accruedHours": 160.0, "usedHours": 144.0, "scheduledHours": 16.0}, {"policyId": "pol-sick", "policyName": "Sick Leave", "category": "SICK", "accruedHours": 80.0, "usedHours": 40.0, "scheduledHours": 0.0}],
        "emp-005": [{"policyId": "pol-vacation", "policyName": "Annual Leave", "category": "VACATION", "accruedHours": 160.0, "usedHours": 8.0,   "scheduledHours": 8.0},  {"policyId": "pol-sick", "policyName": "Sick Leave", "category": "SICK", "accruedHours": 80.0, "usedHours": 0.0,  "scheduledHours": 0.0}],
        "emp-006": [{"policyId": "pol-vacation", "policyName": "Annual Leave", "category": "VACATION", "accruedHours": 160.0, "usedHours": 72.0,  "scheduledHours": 32.0}, {"policyId": "pol-sick", "policyName": "Sick Leave", "category": "SICK", "accruedHours": 80.0, "usedHours": 16.0, "scheduledHours": 0.0}],
        "emp-007": [{"policyId": "pol-vacation", "policyName": "Annual Leave", "category": "VACATION", "accruedHours": 160.0, "usedHours": 160.0, "scheduledHours": 0.0},  {"policyId": "pol-sick", "policyName": "Sick Leave", "category": "SICK", "accruedHours": 80.0, "usedHours": 56.0, "scheduledHours": 0.0}],
        "emp-008": [{"policyId": "pol-vacation", "policyName": "Annual Leave", "category": "VACATION", "accruedHours": 160.0, "usedHours": 48.0,  "scheduledHours": 0.0},  {"policyId": "pol-sick", "policyName": "Sick Leave", "category": "SICK", "accruedHours": 80.0, "usedHours": 8.0,  "scheduledHours": 0.0}],
    }
    rows: list[dict[str, Any]] = []
    for emp_id, balances in _MOCK_TIMEOFF.items():
        for balance in balances:
            rows.append({"employeeId": emp_id, **balance})
    return rows


def get_employee_payslips(employee_id: str) -> list[dict[str, Any]]:
    return list(_MOCK_PAYSLIPS.get(employee_id, []))


def get_employee_timeoff(employee_id: str) -> list[dict[str, Any]]:
    return [r for r in get_timeoff_details() if r["employeeId"] == employee_id]
