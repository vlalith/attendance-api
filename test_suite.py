import pytest
from datetime import datetime, timezone, timedelta
from starlette.testclient import TestClient
from app.main import (
    app, db, dt_to_ms, ms_to_dt, dt_to_ist, attendance_date,
    compute_late_minutes, compute_overtime, compute_work_hours, half_up_round, IST
)
import sample_seed

def setup_module(module):
    # Re-seed test database with initial sample data
    opts = sample_seed.json_util.JSONOptions(tz_aware=True)
    here = sample_seed.here
    for name in ("employees", "attendance_logs"):
        docs = sample_seed.json_util.loads((here / f"{name}.json").read_text(), json_options=opts)
        db[name].delete_many({})
        db[name].insert_many(docs)

def test_health():
    with TestClient(app) as client:
        res = client.get("/health")
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}

def test_list_employees():
    with TestClient(app) as client:
        res = client.get("/employees")
        assert res.status_code == 200
        data = res.json()
        assert "items" in data
        assert data["total"] == 6
        assert len(data["items"]) == 6
        res_sales = client.get("/employees?department=Sales")
        assert res_sales.status_code == 200
        assert res_sales.json()["total"] == 2

def test_create_employee_and_duplicate():
    with TestClient(app) as client:
        emp_payload = {
            "emp_code": "EMP0099",
            "name": "Integration Test User",
            "email": "test99@example.com",
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-07-01"
        }
        res1 = client.post("/employees", json=emp_payload)
        assert res1.status_code == 201
        data = res1.json()
        assert data["emp_code"] == "EMP0099"
        assert isinstance(data["created_at"], int)

        # Duplicate key should return 409
        res2 = client.post("/employees", json=emp_payload)
        assert res2.status_code == 409

def test_punch_in_and_duplicate_punch_in():
    with TestClient(app) as client:
        # Punch in for EMP0002 on 2026-07-15 at 09:35 IST (04:05 UTC)
        # Shift is 09:30 - 18:30
        pin_dt = datetime(2026, 7, 15, 4, 5, 0, tzinfo=timezone.utc)
        pin_ms = dt_to_ms(pin_dt)

        res = client.post("/attendance/punch-in", json={
            "emp_code": "EMP0002",
            "punched_at": pin_ms,
            "status": "PRESENT"
        })
        assert res.status_code == 201
        data = res.json()
        assert data["emp_code"] == "EMP0002"
        assert data["date"] == "2026-07-15"
        assert data["late_minutes"] == 0  # 5 min late <= 10 min grace
        assert data["punch_out"] is None

        # Duplicate punch-in on same attendance date gives 409
        res_dup = client.post("/attendance/punch-in", json={
            "emp_code": "EMP0002",
            "punched_at": pin_ms + 60000,
            "status": "PRESENT"
        })
        assert res_dup.status_code == 409

def test_punch_out_and_rules():
    with TestClient(app) as client:
        # Punch out for EMP0002 on 2026-07-15 at 19:15 IST (13:45 UTC)
        # Shift end is 18:30 IST. Overtime = 45 min (>= 30 min)
        # Work hours = from 09:35 to 19:15 = 9h 40m = 9.67h
        pout_dt = datetime(2026, 7, 15, 13, 45, 0, tzinfo=timezone.utc)
        pout_ms = dt_to_ms(pout_dt)

        res = client.post("/attendance/punch-out", json={
            "emp_code": "EMP0002",
            "punched_at": pout_ms
        })
        assert res.status_code == 200
        data = res.json()
        assert data["punch_out"] == pout_ms
        assert data["work_hours"] == 9.67
        assert data["overtime_minutes"] == 45
        assert data["half_day"] is False

        # Repeating punch-out when already punched out -> 404 (no open record)
        res_repeat = client.post("/attendance/punch-out", json={
            "emp_code": "EMP0002",
            "punched_at": pout_ms + 1000
        })
        assert res_repeat.status_code == 404

def test_overnight_shift_punch_in():
    # EMP0005: shift 22:00 to 06:00
    # Punch in at 2026-07-16 05:30 IST (which is before 06:00) belongs to 2026-07-15
    with TestClient(app) as client:
        pin_dt = datetime(2026, 7, 16, 0, 0, 0, tzinfo=timezone.utc) # 05:30 IST
        pin_ms = dt_to_ms(pin_dt)
        res = client.post("/attendance/punch-in", json={
            "emp_code": "EMP0005",
            "punched_at": pin_ms,
            "status": "PRESENT"
        })
        assert res.status_code == 201
        data = res.json()
        assert data["date"] == "2026-07-15"

def test_regularize_patch_with_audit_trail():
    with TestClient(app) as client:
        # Regularize EMP0006 on 2026-07-07 (opened punch in without punch out in sample data)
        # Add punch_out at 18:30 IST (13:00 UTC)
        pout_dt = datetime(2026, 7, 7, 13, 0, 0, tzinfo=timezone.utc)
        pout_ms = dt_to_ms(pout_dt)
        patch_payload = {
            "punch_out": pout_ms,
            "reason": "Forgot to punch out at end of shift",
            "regularized_by": "manager.alice"
        }
        res = client.patch("/attendance/EMP0006/2026-07-07", json=patch_payload)
        assert res.status_code == 200
        data = res.json()
        assert data["punch_out"] == pout_ms
        assert len(data["history"]) >= 1
        last_hist = data["history"][-1]
        assert last_hist["by"] == "manager.alice"
        assert last_hist["reason"] == "Forgot to punch out at end of shift"
        assert "punch_out" in last_hist["changes"]

def test_employee_monthly_analytics():
    with TestClient(app) as client:
        res = client.get("/analytics/employees/EMP0001/monthly?month=2026-07")
        assert res.status_code == 200
        data = res.json()
        assert data["emp_code"] == "EMP0001"
        assert data["month"] == "2026-07"
        assert data["working_days"] == 23
        assert data["present_days"] >= 2
        assert "attendance_pct" in data

def test_department_summary_analytics():
    with TestClient(app) as client:
        res = client.get("/analytics/departments/summary?month=2026-07")
        assert res.status_code == 200
        data = res.json()
        assert len(data["items"]) >= 3
        depts = [it["department"] for it in data["items"]]
        assert "Engineering" in depts
        assert "Sales" in depts
        assert "Support" in depts

def test_late_leaderboard_analytics():
    with TestClient(app) as client:
        res = client.get("/analytics/leaderboard/late?month=2026-07")
        assert res.status_code == 200
        data = res.json()
        assert "items" in data
        for it in data["items"]:
            assert "rank" in it
            assert "emp_code" in it
            assert "total_late_minutes" in it
            assert it["total_late_minutes"] > 0

def test_department_trend_gap_filled():
    with TestClient(app) as client:
        res = client.get("/analytics/departments/Engineering/trend?from=2026-07-01&to=2026-07-15")
        assert res.status_code == 200
        data = res.json()
        assert data["department"] == "Engineering"
        # 15 calendar days must all be present
        assert len(data["items"]) == 15
        for item in data["items"]:
            assert "date" in item
            assert "is_working_day" in item
            assert "headcount" in item
            assert "attendance_rate" in item
            assert "moving_avg_7d" in item

def test_admin_explain_all():
    with TestClient(app) as client:
        for ep in ["attendance_list", "employee_monthly", "department_summary", "late_leaderboard", "department_trend"]:
            params = {}
            if ep == "employee_monthly":
                params = {"emp_code": "EMP0001", "month": "2026-07"}
            elif ep in ("department_summary", "late_leaderboard"):
                params = {"month": "2026-07"}
            elif ep == "department_trend":
                params = {"department": "Engineering", "from": "2026-07-01", "to": "2026-07-10"}
            res = client.get(f"/admin/explain/{ep}", params=params)
            assert res.status_code == 200, f"Explain failed for {ep}: {res.text}"
            data = res.json()
            assert "explain" in data
            assert data["endpoint"] == ep
