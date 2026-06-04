from __future__ import annotations
import datetime
from typing import Any

_EMPLOYEES: list[dict[str, Any]] = [
    {"id": "emp-001", "firstName": "Alice", "lastName": "Henderson", "email": "alice.henderson@company.com", "status": "ACTIVE", "jobTitle": "Senior Engineer", "department": "Engineering"},
    {"id": "emp-002", "firstName": "Ben", "lastName": "Okafor", "email": "ben.okafor@company.com", "status": "ACTIVE", "jobTitle": "Product Manager", "department": "Product"},
    {"id": "emp-003", "firstName": "Clara", "lastName": "Marchetti", "email": "clara.marchetti@company.com", "status": "ACTIVE", "jobTitle": "UX Designer", "department": "Design"},
    {"id": "emp-004", "firstName": "David", "lastName": "Nkrumah", "email": "david.nkrumah@company.com", "status": "ACTIVE", "jobTitle": "Data Analyst", "department": "Analytics"},
    {"id": "emp-005", "firstName": "Elena", "lastName": "Vasquez", "email": "elena.vasquez@company.com", "status": "ACTIVE", "jobTitle": "DevOps Engineer", "department": "Engineering"},
    {"id": "emp-006", "firstName": "Frank", "lastName": "Osei", "email": "frank.osei@company.com", "status": "ACTIVE", "jobTitle": "Marketing Lead", "department": "Marketing"},
    {"id": "emp-007", "firstName": "Grace", "lastName": "Thornton", "email": "grace.thornton@company.com", "status": "INACTIVE", "jobTitle": "HR Coordinator", "department": "HR"},
    {"id": "emp-008", "firstName": "Hassan", "lastName": "Al-Rashid", "email": "hassan.alrashid@company.com", "status": "ACTIVE", "jobTitle": "Finance Manager", "department": "Finance"},
]

_TIMEOFF_BALANCES: dict[str, list[dict[str, Any]]] = {
    "emp-001": [{"policyId": "pol-vacation", "policyName": "Annual Leave", "category": "VACATION", "accruedHours": 160.0, "usedHours": 64.0, "scheduledHours": 16.0}, {"policyId": "pol-sick", "policyName": "Sick Leave", "category": "SICK", "accruedHours": 80.0, "usedHours": 8.0, "scheduledHours": 0.0}],
    "emp-002": [{"policyId": "pol-vacation", "policyName": "Annual Leave", "category": "VACATION", "accruedHours": 160.0, "usedHours": 120.0, "scheduledHours": 24.0}, {"policyId": "pol-sick", "policyName": "Sick Leave", "category": "SICK", "accruedHours": 80.0, "usedHours": 24.0, "scheduledHours": 8.0}],
    "emp-003": [{"policyId": "pol-vacation", "policyName": "Annual Leave", "category": "VACATION", "accruedHours": 160.0, "usedHours": 32.0, "scheduledHours": 40.0}, {"policyId": "pol-sick", "policyName": "Sick Leave", "category": "SICK", "accruedHours": 80.0, "usedHours": 0.0, "scheduledHours": 0.0}],
    "emp-004": [{"policyId": "pol-vacation", "policyName": "Annual Leave", "category": "VACATION", "accruedHours": 160.0, "usedHours": 144.0, "scheduledHours": 16.0}, {"policyId": "pol-sick", "policyName": "Sick Leave", "category": "SICK", "accruedHours": 80.0, "usedHours": 40.0, "scheduledHours": 0.0}],
    "emp-005": [{"policyId": "pol-vacation", "policyName": "Annual Leave", "category": "VACATION", "accruedHours": 160.0, "usedHours": 8.0, "scheduledHours": 8.0}, {"policyId": "pol-sick", "policyName": "Sick Leave", "category": "SICK", "accruedHours": 80.0, "usedHours": 0.0, "scheduledHours": 0.0}],
    "emp-006": [{"policyId": "pol-vacation", "policyName": "Annual Leave", "category": "VACATION", "accruedHours": 160.0, "usedHours": 72.0, "scheduledHours": 32.0}, {"policyId": "pol-sick", "policyName": "Sick Leave", "category": "SICK", "accruedHours": 80.0, "usedHours": 16.0, "scheduledHours": 0.0}],
    "emp-007": [{"policyId": "pol-vacation", "policyName": "Annual Leave", "category": "VACATION", "accruedHours": 160.0, "usedHours": 160.0, "scheduledHours": 0.0}, {"policyId": "pol-sick", "policyName": "Sick Leave", "category": "SICK", "accruedHours": 80.0, "usedHours": 56.0, "scheduledHours": 0.0}],
    "emp-008": [{"policyId": "pol-vacation", "policyName": "Annual Leave", "category": "VACATION", "accruedHours": 160.0, "usedHours": 48.0, "scheduledHours": 0.0}, {"policyId": "pol-sick", "policyName": "Sick Leave", "category": "SICK", "accruedHours": 80.0, "usedHours": 8.0, "scheduledHours": 0.0}],
}

_today = datetime.date.today()
_PAYSLIPS: dict[str, list[dict[str, Any]]] = {
    emp["id"]: [{"payslipId": f"{emp['id']}-ps-{i}", "employeeId": emp["id"], "periodStart": (_today - datetime.timedelta(days=30*(i+1))).isoformat(), "periodEnd": (_today - datetime.timedelta(days=30*i+1)).isoformat(), "grossPay": round(4500.0 + abs(hash(emp["id"]) % 2000), 2), "netPay": round(3200.0 + abs(hash(emp["id"]) % 1500), 2)} for i in range(3)]
    for emp in _EMPLOYEES
}

def get_employees() -> list[dict[str, Any]]:
    return list(_EMPLOYEES)

def get_timeoff_details() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for emp_id, balances in _TIMEOFF_BALANCES.items():
        for balance in balances:
            rows.append({"employeeId": emp_id, **balance})
    return rows

def get_employee_payslips(employee_id: str) -> list[dict[str, Any]]:
    return list(_PAYSLIPS.get(employee_id, []))

def get_employee_timeoff(employee_id: str) -> list[dict[str, Any]]:
    balances = _TIMEOFF_BALANCES.get(employee_id, [])
    return [{"employeeId": employee_id, **b} for b in balances]
