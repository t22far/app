"""One-time seed script — imports real employee and leave data."""
import datetime
from app import app, db
from models import EmployeeCache, TimeoffCache

EMPLOYEES = [
    {"id": "qb-alfie-richards",    "firstName": "Alfie",    "lastName": "Richards",  "entitlement": 42.0},
    {"id": "qb-caroline-jempson",  "firstName": "Caroline", "lastName": "Jempson",   "entitlement": 78.4},
    {"id": "qb-helen-powell",      "firstName": "Helen",    "lastName": "Powell",    "entitlement": 78.4},
    {"id": "qb-kady-richards",     "firstName": "Kady",     "lastName": "Richards",  "entitlement": 78.4},
    {"id": "qb-karen-wadsworth",   "firstName": "Karen",    "lastName": "Wadsworth", "entitlement": 100.8},
    {"id": "qb-laynie-tunstall",   "firstName": "Laynie",   "lastName": "Tunstall",  "entitlement": 36.4},
    {"id": "qb-linda-pascoe",      "firstName": "Linda",    "lastName": "Pascoe",    "entitlement": 81.2},
    {"id": "qb-tina-davidson",     "firstName": "Tina",     "lastName": "Davidson",  "entitlement": 117.6},
    {"id": "qb-victoria-southern", "firstName": "Victoria", "lastName": "Southern",  "entitlement": 89.6},
]

# Approved leave hours per employee (declined entries excluded)
LEAVE = {
    "qb-alfie-richards":    7.5,
    "qb-caroline-jempson":  12.5,
    "qb-helen-powell":      42.0,
    "qb-kady-richards":     40.5,
    "qb-karen-wadsworth":   69.5,
    "qb-laynie-tunstall":   7.5,
    "qb-linda-pascoe":      36.0,
    "qb-tina-davidson":     86.5,
    "qb-victoria-southern": 34.0,
}

with app.app_context():
    now = datetime.datetime.utcnow()

    # Clear out mock seed employees (emp-001 … emp-008)
    for mock_id in [f"emp-{i:03d}" for i in range(1, 9)]:
        emp = db.session.get(EmployeeCache, mock_id)
        if emp:
            TimeoffCache.query.filter_by(employee_id=mock_id).delete()
            db.session.delete(emp)

    for e in EMPLOYEES:
        emp = db.session.get(EmployeeCache, e["id"]) or EmployeeCache(id=e["id"])
        emp.first_name        = e["firstName"]
        emp.last_name         = e["lastName"]
        emp.email             = ""
        emp.status            = "ACTIVE"
        emp.job_title         = ""
        emp.department        = ""
        emp.is_director       = False
        emp.entitlement_hours = e["entitlement"]
        emp.last_synced       = now
        db.session.merge(emp)

        used = LEAVE.get(e["id"], 0.0)
        existing = TimeoffCache.query.filter_by(
            employee_id=e["id"], policy_id="pol-annual-leave"
        ).first()
        if existing is None:
            existing = TimeoffCache(employee_id=e["id"], policy_id="pol-annual-leave")
        existing.policy_name     = "Annual Leave"
        existing.category        = "VACATION"
        existing.accrued_hours   = e["entitlement"]
        existing.used_hours      = used
        existing.scheduled_hours = 0.0
        existing.last_synced     = now
        db.session.add(existing)

    db.session.commit()
    print("Done.")
    for e in EMPLOYEES:
        left = e["entitlement"] - LEAVE.get(e["id"], 0.0)
        print(f"  {e['firstName']} {e['lastName']}: {left:.1f}h remaining")
