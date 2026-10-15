# REVIEW.md

Defects found in the starter `app/main.py`:

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `health()` (lines 73–74) | MongoDB is never actually pinged; the handler just returns a hardcoded 200 OK. | Stopped my local MongoDB service; `GET /health` still returned `{"status": "ok"}` instead of failing. | Wrapped `client.admin.command("ping")` in a try/except block, returning 503 if unreachable. |
| 2 | `create_employee()` (lines 79–85) | Classic check-then-act race between `find_one` and `insert_one`. | Sending rapid concurrent POST requests with the same `emp_code` created duplicate records in the database. | Removed the manual check, relied on MongoDB's unique index on `emp_code`, and caught `DuplicateKeyError` to return 409. |
| 3 | `create_employee()` (line 82) | Using naive `datetime.now()` stores local machine time instead of explicit UTC. | Running on a machine set to IST stores timestamps offset by +5:30 compared to actual UTC. | Changed to `datetime.now(timezone.utc)` for consistent UTC timestamps. |
| 4 | `create_employee()` (lines 84–85) | Returned `created_at` as a Python datetime object instead of epoch milliseconds. | Calling `POST /employees` returned an ISO string (`"2026-07-06T09:40:00"`) instead of an integer. | Converted with `dt_to_ms()` before sending the response. |
| 5 | `list_employees()` (line 93) | Off-by-one pagination bug (`skip = page * page_size`). | Requesting `page=1` skipped the first batch and returned page 2 records. | Fixed formula to `skip = (page - 1) * page_size`. |
| 6 | `list_employees()` (line 94) | `total` count used `{}` instead of the filtered query. | Filtered by department (e.g. Sales); `total` still showed the count for the whole company. | Passed the active query filter `q` into `count_documents(q)`. |
| 7 | `list_employees()` | Each employee's `created_at` was returned as raw datetime in list responses. | Inspecting `GET /employees` response items showed datetime strings instead of epoch ms. | Mapped all list records through `serialize_employee()`. |
| 8 | `punch_in()` (line 102) | `fromtimestamp()` defaulted to local OS timezone. | Tested with epoch ms in IST; stored BSON date was skewed by 5.5 hours. | Explicitly passed UTC timezone: `datetime.fromtimestamp(ms / 1000, tz=timezone.utc)`. |
| 9 | `punch_in()` (line 103) | Attendance date was taken from UTC date string and ignored overnight shifts. | Tested EMP0005 (night shift 22:00–06:00); an early morning punch at 05:30 IST got attributed to today instead of yesterday's shift. | Implemented `attendance_date()` helper to evaluate IST time and shift boundaries (R1). |
| 10 | `punch_in()` (line 119) | Attached stringified `_id` (`doc["id"] = ...`) to the response payload. | Response payload contained an unexpected `"id"` field not in the API contract. | Removed the assignment and sanitized response shape via `serialize_record()`. |
| 11 | `punch_in()` | Missing 404 check when an unknown employee code is provided. | Sent punch-in with non-existent `emp_code`; server crashed with a 500 error on `emp["shift_start"]`. | Added check: `if not emp: raise HTTPException(404, "Employee not found")`. |
| 12 | `compute_late_minutes()` (line 35) | Naive `replace()` on aware datetime raises `TypeError` when subtracting. | Passing a timezone-aware datetime threw a crash during timestamp math. | Converted to IST-aware datetime before building shift start. |
| 13 | `compute_late_minutes()` (line 37) | Grace period check used float division and truncation rather than floor division. | Punches right on grace boundaries (e.g. 10.5 min) had inconsistent rounding behavior. | Used integer floor division `int(diff_seconds // 60)` and compared strictly `> 10` (R2). |
| 14 | `compute_overtime()` (line 46) | `shift_end` was always constructed on the punch-in date. | For EMP0005 (22:00–06:00), punch-out at 06:40 next morning calculated 24+ hours of overtime. | Detected overnight shifts and added one day to the target end date before building `shift_end_dt`. |
| 15 | `list_attendance()` (lines 144–146) | In-memory sorting and pagination using `list(db.attendance_logs.find(q))`. | Fetching all records into Python memory would degrade response times as data grows. | Pushed `.sort().skip().limit()` directly down to MongoDB cursor. |
| 16 | `department_trend()` | MongoDB `$densify` `bounds` was passed expression objects (`$dateFromString`). | Calling trend endpoint crashed MongoDB with `OperationFailure: A bounding array must be an ascending array of either two dates or two numbers`. | Passed native BSON UTC datetime objects directly into the bounds array. |
| 17 | `explain_endpoint()` | Deprecated `aggregate(..., explain=True)` and unhandled BSON Binary UUIDs. | PyMongo 4.x threw `ConfigurationError`, and FastAPI's JSON encoder crashed on Binary UUID bytes. | Switched to `db.command("aggregate", ..., explain=True)` and sanitized with `bson.json_util`. |

### Items reviewed and decided NOT a defect

- **`compute_work_hours` using `round()`**: Python's built-in `round()` uses banker's rounding (round half to even). While not yet called from a complete flow in the starter skeleton, I replaced it with `half_up_round()` to prevent payroll calculation discrepancies.
- **`IST = timezone(timedelta(hours=5, minutes=30))`**: Offset is correctly defined (+05:30); kept as-is.
