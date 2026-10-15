# Employee Attendance & Analytics API

A FastAPI + MongoDB REST API for employee attendance tracking, punch-in/out, manual corrections with an audit trail, and department analytics.

## Requirements

- Python 3.11+
- MongoDB 6.0+ 

## Local Setup

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux:
source .venv/bin/activate

pip install -r requirements.txt

cp .env.example .env
# Edit .env and set MONGO_URI / MONGO_DB as needed
# For a local Docker MongoDB:
# docker run -d -p 27017:27017 mongo:7

```bash
python sample_seed.py
uvicorn app.main:app --port 8000 --reload
```


- **Chronos Web Portal & Analytics Dashboard**: `http://localhost:8000/`

## Chronos Web Dashboard (Frontend)

A sleek, responsive dark-mode cyber-glass web application is included under `frontend/` and served automatically by FastAPI at `/`:
- **Virtual Biometric Terminal**: Punch in & punch out with live IST clock, status selection, and real-time validation of business rules R1–R5.
- **Attendance Records Table**: Filter by employee, date range, and presence status, with inline expandable audit history diffs.
- **Regularization Modal**: Patch attendance records with optimistic concurrency checks and mandatory reason/audit trail.
- **Analytics Dashboard**: Department summaries (headcount anchored to employees), interactive SVG 7-day moving average chart with gap-filled dates (`$densify`), and late-comers leaderboard (`$rank` with competition ties).
- **MongoDB Query Plan Inspector**: Inspect winning execution plans and indexes in real time via `/admin/explain/{endpoint}`.

## Running Tests

Run the comprehensive pytest suite verifying business rules R1–R10, edge cases, and endpoints:

```bash
pytest test_suite.py -v
```

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/` | Chronos Web UI Dashboard |
| GET | `/health` | Readiness probe (MongoDB ping) |
| POST | `/employees` | Create an employee |
| GET | `/employees` | List employees (paginated, filterable by department) |
| POST | `/attendance/punch-in` | Record punch-in (R1, R2) |
| POST | `/attendance/punch-out` | Record punch-out (R3, R4, R5) |
| GET | `/attendance` | List attendance records (paginated, filterable) |
| PATCH | `/attendance/{emp_code}/{date}` | Regularize a record with audit trail |
| GET | `/analytics/employees/{emp_code}/monthly` | Monthly summary for one employee |
| GET | `/analytics/departments/summary` | Department summary for a month |
| GET | `/analytics/leaderboard/late` | Late-comers leaderboard |
| GET | `/analytics/departments/{department}/trend` | Daily attendance trend (gap-filled) |
| GET | `/admin/explain/{endpoint}` | MongoDB explain for an endpoint's main query |

## Configuration

| Variable | Default | Description |
|---|---|---|
| `MONGO_URI` | `mongodb://localhost:27017` | MongoDB connection string |
| `MONGO_DB`  | `attendance_db`             | Database name |

## What is implemented

All endpoints from the API contract are implemented including:
- Full business rules R1–R10 (IST time zone, late minutes, overtime, half day, working days, headcount, pagination)
- Concurrency-safe punch-in and punch-out using `insert_one` with unique index and `find_one_and_update` with `punch_out: null` guard
- Audit trail (history) with optimistic concurrency for regularize
- MongoDB aggregation pipelines for all analytics
- `$densify` gap-filling for department trend with BSON UTC datetime bounds
- Standard competition ranking (`$rank`) for late leaderboard
- Indexes created at startup (idempotent)
- Epoch-milliseconds ↔ BSON datetime conversion at the API boundary
- Half-up rounding (not banker's rounding) via `math.floor`
- Full interactive frontend web dashboard and complete automated test suite (`test_suite.py`)
