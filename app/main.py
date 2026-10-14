"""
Employee Attendance & Analytics API

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB  (a local .env is loaded for convenience)
"""
import os
import math
import json
from datetime import datetime, timedelta, timezone, date as date_type
from typing import Optional, Any

from bson import json_util
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator, model_validator, Field
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

load_dotenv()

# ---------------------------------------------------------------------------
# DB setup
# ---------------------------------------------------------------------------
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB  = os.getenv("MONGO_DB",  "attendance_db")

client = MongoClient(MONGO_URI)
db     = client[MONGO_DB]

IST         = timezone(timedelta(hours=5, minutes=30))
IST_OFFSET  = timedelta(hours=5, minutes=30)

# ---------------------------------------------------------------------------
# Startup – create indexes idempotently
# ---------------------------------------------------------------------------
app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")

# ---------------------------------------------------------------------------
# Frontend static files & web dashboard
# ---------------------------------------------------------------------------
frontend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))
if os.path.exists(frontend_dir):
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

    @app.get("/", include_in_schema=False)
    def serve_dashboard():
        return FileResponse(os.path.join(frontend_dir, "index.html"))


@app.on_event("startup")
def create_indexes():
    # employees: unique on emp_code (enforces uniqueness at DB level for concurrent creates)
    db.employees.create_index([("emp_code", ASCENDING)], unique=True, name="emp_code_unique")
    db.employees.create_index([("department", ASCENDING)], name="department_asc")

    # attendance_logs: unique compound (emp_code, date) – enforces one record per day per employee
    db.attendance_logs.create_index(
        [("emp_code", ASCENDING), ("date", ASCENDING)],
        unique=True,
        name="emp_date_unique",
    )
    # Support date-range + status filter on list_attendance
    db.attendance_logs.create_index(
        [("date", DESCENDING), ("emp_code", ASCENDING)],
        name="date_desc_emp_asc",
    )
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", DESCENDING)], name="emp_date_desc")


# ---------------------------------------------------------------------------
# Epoch-milliseconds helpers
# ---------------------------------------------------------------------------

EPOCH_MS_MIN = 100_000_000_000   # ~1973-03-03 – avoids seconds-looking values
EPOCH_MS_MAX = 4_102_444_800_000 # 2100-01-01


def ms_to_dt(ms: int) -> datetime:
    """Epoch milliseconds → UTC-aware datetime, truncated to whole seconds (R1)."""
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return dt.replace(microsecond=0)  # truncate sub-second (R1)


def dt_to_ms(dt: datetime) -> int:
    """datetime → epoch milliseconds."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        # PyMongo returns naive UTC datetimes; treat them as UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def dt_to_ist(dt: datetime) -> datetime:
    """Convert a UTC-aware (or naive-UTC) datetime to IST-aware."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)


# ---------------------------------------------------------------------------
# Business-rule helpers
# ---------------------------------------------------------------------------

def attendance_date(punch_in_ist: datetime, shift_start: str, shift_end: str) -> str:
    """
    R1: determine the attendance date string 'YYYY-MM-DD'.
    For overnight shifts (shift_end <= shift_start), a punch-in whose IST
    time is strictly before shift_end belongs to the previous day's shift.
    """
    sh, sm = map(int, shift_start.split(":"))
    eh, em = map(int, shift_end.split(":"))
    overnight = (eh * 60 + em) <= (sh * 60 + sm)

    cal_date = punch_in_ist.date()
    if overnight:
        ptime = punch_in_ist.hour * 60 + punch_in_ist.minute
        etime = eh * 60 + em
        if ptime < etime:
            # this punch-in is the tail of yesterday's overnight shift
            cal_date = cal_date - timedelta(days=1)
    return cal_date.isoformat()


def half_up_round(value: float, decimals: int) -> float:
    """Round half-up (not banker's rounding) to `decimals` decimal places."""
    factor = 10 ** decimals
    return math.floor(value * factor + 0.5) / factor


def compute_late_minutes(punch_in_ist: datetime, shift_start: str) -> int:
    """
    R2: Grace period 10 minutes (strictly more than 10 minutes → late).
    Returns whole minutes (floor) elapsed since shift_start.
    """
    sh, sm = map(int, shift_start.split(":"))
    # shift_start on the same IST calendar date as punch_in
    shift_start_dt = punch_in_ist.replace(hour=sh, minute=sm, second=0, microsecond=0)
    diff_seconds = (punch_in_ist - shift_start_dt).total_seconds()
    diff_minutes = int(diff_seconds // 60)   # floor
    # Strictly more than 10 minutes late
    return diff_minutes if diff_minutes > 10 else 0


def compute_overtime(punch_out_ist: datetime, punch_in_date: str, shift_end: str, shift_start: str) -> int:
    """
    R3: overtime_minutes = floor minutes between shift_end and punch_out, only when >= 30.
    For overnight shifts shift_end is on the next IST calendar date.
    """
    sh, sm = map(int, shift_start.split(":"))
    eh, em = map(int, shift_end.split(":"))
    overnight = (eh * 60 + em) <= (sh * 60 + sm)

    base_date = date_type.fromisoformat(punch_in_date)
    if overnight:
        end_date = base_date + timedelta(days=1)
    else:
        end_date = base_date

    shift_end_dt = datetime(
        end_date.year, end_date.month, end_date.day,
        eh, em, 0, tzinfo=IST
    )
    diff_seconds = (punch_out_ist - shift_end_dt).total_seconds()
    diff_minutes = int(diff_seconds // 60)
    return diff_minutes if diff_minutes >= 30 else 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    """R4: (punch_out - punch_in) / 3600, rounded to 2 decimals half-up."""
    if punch_in.tzinfo is None:
        punch_in = punch_in.replace(tzinfo=timezone.utc)
    if punch_out.tzinfo is None:
        punch_out = punch_out.replace(tzinfo=timezone.utc)
    seconds = (punch_out - punch_in).total_seconds()
    return half_up_round(seconds / 3600, 2)


def recompute_derived(punch_in: Optional[datetime],
                      punch_out: Optional[datetime],
                      status: str,
                      att_date: str,
                      shift_start: str,
                      shift_end: str) -> dict:
    """
    Recompute late_minutes, work_hours, overtime_minutes, half_day
    given final field values after a punch-in, punch-out, or correction.
    """
    if status in ("ABSENT", "LEAVE") or punch_in is None:
        return {
            "late_minutes": 0,
            "work_hours": None,
            "overtime_minutes": 0,
            "half_day": False,
        }

    pin_ist = dt_to_ist(punch_in)
    late    = compute_late_minutes(pin_ist, shift_start)

    if punch_out is None:
        return {
            "late_minutes": late,
            "work_hours": None,
            "overtime_minutes": 0,
            "half_day": False,
        }

    pout_ist  = dt_to_ist(punch_out)
    wh        = compute_work_hours(punch_in, punch_out)
    ot        = compute_overtime(pout_ist, att_date, shift_end, shift_start)
    half      = wh < 4.50   # R5: rounded work_hours < 4.50

    return {
        "late_minutes":     late,
        "work_hours":       wh,
        "overtime_minutes": ot,
        "half_day":         half,
    }


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _ser_punch(dt_val) -> Optional[int]:
    """Convert a stored punch datetime (or None) → epoch ms (or None)."""
    if dt_val is None:
        return None
    return dt_to_ms(dt_val)


def serialize_record(doc: dict) -> dict:
    """Convert a raw MongoDB attendance_log document to the API shape."""
    history = []
    for entry in doc.get("history") or []:
        changes = {}
        for field, change in (entry.get("changes") or {}).items():
            if field in ("punch_in", "punch_out"):
                changes[field] = {
                    "from": _ser_punch(change.get("from")),
                    "to":   _ser_punch(change.get("to")),
                }
            else:
                changes[field] = {"from": change.get("from"), "to": change.get("to")}
        history.append({
            "at":      dt_to_ms(entry["at"]),
            "by":      entry["by"],
            "reason":  entry["reason"],
            "changes": changes,
        })

    return {
        "emp_code":          doc["emp_code"],
        "date":              doc["date"],
        "status":            doc["status"],
        "punch_in":          _ser_punch(doc.get("punch_in")),
        "punch_out":         _ser_punch(doc.get("punch_out")),
        "work_hours":        doc.get("work_hours"),
        "late_minutes":      doc.get("late_minutes", 0),
        "overtime_minutes":  doc.get("overtime_minutes", 0),
        "half_day":          doc.get("half_day", False),
        "history":           history,
    }


def serialize_employee(doc: dict) -> dict:
    return {
        "emp_code":    doc["emp_code"],
        "name":        doc["name"],
        "email":       doc["email"],
        "department":  doc["department"],
        "shift_start": doc["shift_start"],
        "shift_end":   doc["shift_end"],
        "joined_on":   doc["joined_on"],
        "created_at":  dt_to_ms(doc["created_at"]),
    }


# ---------------------------------------------------------------------------
# Pydantic validators for epoch milliseconds
# ---------------------------------------------------------------------------

class EpochMs(int):
    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v):
        if not isinstance(v, int):
            raise ValueError("must be an integer (epoch milliseconds)")
        if v < EPOCH_MS_MIN or v > EPOCH_MS_MAX:
            raise ValueError(f"epoch milliseconds out of range [{EPOCH_MS_MIN}, {EPOCH_MS_MAX}]")
        return v


# ---------------------------------------------------------------------------
# Request/Response models
# ---------------------------------------------------------------------------

class EmployeeCreate(BaseModel):
    emp_code:    str = Field(..., pattern=r"^EMP\d{4,6}$")
    name:        str = Field(..., min_length=1, max_length=100)
    email:       str = Field(..., max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department:  str = Field(..., min_length=1, max_length=50)
    shift_start: str = Field("09:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end:   str = Field("18:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    joined_on:   str

    @model_validator(mode="after")
    def shift_times_differ(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start must differ from shift_end")
        return self


class PunchInRequest(BaseModel):
    emp_code:   str
    punched_at: Optional[int] = None
    status:     str = "PRESENT"

    @field_validator("punched_at")
    @classmethod
    def validate_punched_at(cls, v):
        if v is not None:
            if not isinstance(v, int) or isinstance(v, bool):
                raise ValueError("punched_at must be an integer epoch milliseconds")
            if v < EPOCH_MS_MIN or v > EPOCH_MS_MAX:
                raise ValueError(f"punched_at out of range")
        return v

    @field_validator("status")
    @classmethod
    def validate_status(cls, v):
        if v not in ("PRESENT", "WFH", "ON_DUTY"):
            raise ValueError("status must be one of PRESENT, WFH, ON_DUTY")
        return v


class PunchOutRequest(BaseModel):
    emp_code:   str
    punched_at: Optional[int] = None

    @field_validator("punched_at")
    @classmethod
    def validate_punched_at(cls, v):
        if v is not None:
            if not isinstance(v, int) or isinstance(v, bool):
                raise ValueError("punched_at must be an integer epoch milliseconds")
            if v < EPOCH_MS_MIN or v > EPOCH_MS_MAX:
                raise ValueError(f"punched_at out of range")
        return v


class RegularizeRequest(BaseModel):
    status:          Optional[str] = None
    punch_in:        Optional[int] = None
    punch_out:       Optional[int] = None
    reason:          str = Field(..., min_length=5, max_length=200)
    regularized_by:  str = Field(..., min_length=1, max_length=50)

    @field_validator("punch_in", "punch_out")
    @classmethod
    def validate_punch_ts(cls, v):
        if v is not None:
            if not isinstance(v, int) or isinstance(v, bool):
                raise ValueError("must be integer epoch milliseconds")
            if v < EPOCH_MS_MIN or v > EPOCH_MS_MAX:
                raise ValueError("epoch milliseconds out of range")
        return v

    @field_validator("status")
    @classmethod
    def validate_status(cls, v):
        if v is not None and v not in ("PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"):
            raise ValueError("invalid status")
        return v


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

# ── HEALTH ──────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except Exception:
        return JSONResponse(status_code=503, content={"detail": "MongoDB unavailable"})


# ── EMPLOYEES ───────────────────────────────────────────────────────────────

@app.post("/employees", status_code=201)
def create_employee(body: EmployeeCreate):
    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, detail="emp_code already exists")
    return serialize_employee(doc)


@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page:       int = Query(default=1, ge=1),
    page_size:  int = Query(default=20, ge=1, le=100),
):
    q = {}
    if department:
        q["department"] = department
    total = db.employees.count_documents(q)
    skip  = (page - 1) * page_size
    items = list(
        db.employees.find(q, {"_id": 0})
        .sort("emp_code", ASCENDING)
        .skip(skip)
        .limit(page_size)
    )
    return {
        "items":     [serialize_employee(e) for e in items],
        "total":     total,
        "page":      page,
        "page_size": page_size,
    }


# ── PUNCH-IN ────────────────────────────────────────────────────────────────

@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInRequest):
    # 404 if employee unknown
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, detail="Employee not found")

    # Resolve timestamp; truncate to whole seconds (R1)
    if body.punched_at is not None:
        ts = ms_to_dt(body.punched_at)
    else:
        ts = datetime.now(timezone.utc).replace(microsecond=0)

    ts_ist   = dt_to_ist(ts)
    att_date = attendance_date(ts_ist, emp["shift_start"], emp["shift_end"])

    derived = recompute_derived(ts, None, body.status, att_date, emp["shift_start"], emp["shift_end"])

    doc = {
        "emp_code":         body.emp_code,
        "date":             att_date,
        "status":           body.status,
        "punch_in":         ts,
        "punch_out":        None,
        "work_hours":       None,
        "late_minutes":     derived["late_minutes"],
        "overtime_minutes": 0,
        "half_day":         False,
        "history":          [],
    }

    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, detail="Already punched in for this attendance date")

    return serialize_record(doc)


# ── PUNCH-OUT ───────────────────────────────────────────────────────────────

@app.post("/attendance/punch-out", status_code=200)
def punch_out(body: PunchOutRequest):
    # 404 if employee unknown
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, detail="Employee not found")

    if body.punched_at is not None:
        ts_out = ms_to_dt(body.punched_at)
    else:
        ts_out = datetime.now(timezone.utc).replace(microsecond=0)

    # Find most recent punch-in at or before punch-out (for overnight shifts)
    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$lte": ts_out},
            "punch_out": None,
        },
        sort=[("punch_in", DESCENDING)],
    )
    if not record:
        raise HTTPException(404, detail="No open punch-in record found for this employee")

    punch_in_dt = record["punch_in"]
    if punch_in_dt.tzinfo is None:
        punch_in_dt = punch_in_dt.replace(tzinfo=timezone.utc)

    # Validate: punch_out must be after punch_in
    if ts_out <= punch_in_dt:
        raise HTTPException(422, detail="punched_at must be after punch_in")

    # Validate: punch_out must be within 24 hours of punch_in (R per spec)
    if (ts_out - punch_in_dt).total_seconds() > 86400:
        raise HTTPException(422, detail="punch_out is more than 24 hours after punch_in")

    derived = recompute_derived(
        punch_in_dt, ts_out, record["status"],
        record["date"], emp["shift_start"], emp["shift_end"]
    )

    # Atomic update – only update if punch_out is still None (concurrency guard)
    result = db.attendance_logs.find_one_and_update(
        {"_id": record["_id"], "punch_out": None},
        {"$set": {
            "punch_out":        ts_out,
            "work_hours":       derived["work_hours"],
            "overtime_minutes": derived["overtime_minutes"],
            "half_day":         derived["half_day"],
        }},
        return_document=True,
    )
    if result is None:
        raise HTTPException(409, detail="Record already has a punch_out (concurrent request)")

    return serialize_record(result)


# ── LIST ATTENDANCE ──────────────────────────────────────────────────────────

@app.get("/attendance")
def list_attendance(
    emp_code:  Optional[str] = None,
    date_from: Optional[str] = None,
    date_to:   Optional[str] = None,
    status:    Optional[str] = None,
    page:      int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, detail="date_from must not be after date_to")

    q: dict = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status

    total = db.attendance_logs.count_documents(q)
    skip  = (page - 1) * page_size
    docs  = list(
        db.attendance_logs.find(q, {"_id": 0})
        .sort([("date", DESCENDING), ("emp_code", ASCENDING)])
        .skip(skip)
        .limit(page_size)
    )
    return {
        "items":     [serialize_record(d) for d in docs],
        "total":     total,
        "page":      page,
        "page_size": page_size,
    }


# ── REGULARIZE (PATCH) ───────────────────────────────────────────────────────

@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(emp_code: str, date: str, body: RegularizeRequest):
    # 404 if employee unknown
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, detail="Employee not found")

    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not record:
        raise HTTPException(404, detail="Attendance record not found")

    # Determine new values (apply patch over stored)
    new_status    = body.status    if body.status    is not None else record["status"]
    new_punch_in  = ms_to_dt(body.punch_in)  if body.punch_in  is not None else record.get("punch_in")
    new_punch_out = ms_to_dt(body.punch_out) if body.punch_out is not None else record.get("punch_out")

    # Validation: ABSENT/LEAVE must not have punch times supplied
    if new_status in ("ABSENT", "LEAVE"):
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(422, detail="Cannot supply punch_in or punch_out with ABSENT/LEAVE status")
        new_punch_in  = None
        new_punch_out = None

    # Presence status must have a punch_in
    if new_status in ("PRESENT", "WFH", "ON_DUTY") and new_punch_in is None:
        raise HTTPException(422, detail="A presence status requires punch_in")

    # punch_in must remain on the record's attendance date (R1 check)
    if new_punch_in is not None:
        pin_ist = dt_to_ist(new_punch_in)
        computed_date = attendance_date(pin_ist, emp["shift_start"], emp["shift_end"])
        if computed_date != date:
            raise HTTPException(422, detail="punch_in must remain on the record's attendance date")

    # punch_out must be after punch_in and within 24 hours
    if new_punch_out is not None and new_punch_in is not None:
        pin_utc  = new_punch_in.replace(tzinfo=timezone.utc)  if new_punch_in.tzinfo  is None else new_punch_in
        pout_utc = new_punch_out.replace(tzinfo=timezone.utc) if new_punch_out.tzinfo is None else new_punch_out
        if pout_utc <= pin_utc:
            raise HTTPException(422, detail="punch_out must be after punch_in")
        if (pout_utc - pin_utc).total_seconds() > 86400:
            raise HTTPException(422, detail="punch_out must be within 24 hours of punch_in")

    # Recompute derived fields
    derived = recompute_derived(
        new_punch_in, new_punch_out, new_status, date,
        emp["shift_start"], emp["shift_end"]
    )

    # Build the update fields and check what actually changed
    old = {
        "status":           record["status"],
        "punch_in":         record.get("punch_in"),
        "punch_out":        record.get("punch_out"),
        "work_hours":       record.get("work_hours"),
        "late_minutes":     record.get("late_minutes", 0),
        "overtime_minutes": record.get("overtime_minutes", 0),
        "half_day":         record.get("half_day", False),
    }
    new_vals = {
        "status":           new_status,
        "punch_in":         new_punch_in,
        "punch_out":        new_punch_out,
        "work_hours":       derived["work_hours"],
        "late_minutes":     derived["late_minutes"],
        "overtime_minutes": derived["overtime_minutes"],
        "half_day":         derived["half_day"],
    }

    # Detect changes
    changes: dict[str, Any] = {}
    for field in ("status", "punch_in", "punch_out", "work_hours", "late_minutes", "overtime_minutes", "half_day"):
        old_v = old[field]
        new_v = new_vals[field]

        # Normalise datetimes for comparison (strip tz info from naive PyMongo datetimes)
        def _norm(v):
            if isinstance(v, datetime):
                if v.tzinfo is None:
                    return v.replace(tzinfo=timezone.utc)
                return v.astimezone(timezone.utc)
            return v

        if _norm(old_v) != _norm(new_v):
            if field in ("punch_in", "punch_out"):
                changes[field] = {
                    "from": _ser_punch(old_v),
                    "to":   _ser_punch(new_v),
                }
            else:
                changes[field] = {"from": old_v, "to": new_v}

    if not changes:
        raise HTTPException(422, detail="Request changes nothing")

    now = datetime.now(timezone.utc)
    history_entry = {
        "at":      now,
        "by":      body.regularized_by,
        "reason":  body.reason,
        "changes": {
            k: {
                "from": (v["from"] if k not in ("punch_in", "punch_out")
                         else (ms_to_dt(v["from"]) if v["from"] is not None else None)),
                "to":   (v["to"]   if k not in ("punch_in", "punch_out")
                         else (ms_to_dt(v["to"])   if v["to"]   is not None else None)),
            }
            for k, v in changes.items()
        },
    }

    # Atomic update with optimistic concurrency: only update if history length is unchanged
    history_len = len(record.get("history") or [])
    result = db.attendance_logs.find_one_and_update(
        {
            "_id": record["_id"],
            "$expr": {"$eq": [{"$size": {"$ifNull": ["$history", []]}}, history_len]},
        },
        {
            "$set": {
                "status":           new_status,
                "punch_in":         new_punch_in,
                "punch_out":        new_punch_out,
                "work_hours":       derived["work_hours"],
                "late_minutes":     derived["late_minutes"],
                "overtime_minutes": derived["overtime_minutes"],
                "half_day":         derived["half_day"],
            },
            "$push": {"history": history_entry},
        },
        return_document=True,
    )
    if result is None:
        raise HTTPException(409, detail="Concurrent modification detected; please retry")

    return serialize_record(result)


# ---------------------------------------------------------------------------
# Analytics helpers
# ---------------------------------------------------------------------------

def _month_date_range(month: str):
    """Return (start_date_str, end_date_str) for a 'YYYY-MM' month string."""
    year, mon = int(month[:4]), int(month[5:])
    start = date_type(year, mon, 1)
    # Last day of month
    if mon == 12:
        end = date_type(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date_type(year, mon + 1, 1) - timedelta(days=1)
    return start.isoformat(), end.isoformat()


def _working_days_in_range(start: str, end: str, joined_on: str) -> int:
    """Count Mon-Fri days in [start, end] on or after joined_on (R7)."""
    effective_start = max(start, joined_on)
    s = date_type.fromisoformat(effective_start)
    e = date_type.fromisoformat(end)
    count = 0
    cur = s
    while cur <= e:
        if cur.weekday() < 5:  # Mon=0 … Fri=4
            count += 1
        cur += timedelta(days=1)
    return count


# ── EMPLOYEE MONTHLY ─────────────────────────────────────────────────────────

@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(
    emp_code: str,
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, detail="Employee not found")

    start, end = _month_date_range(month)
    working_days = _working_days_in_range(start, end, emp["joined_on"])

    pipeline = [
        {"$match": {
            "emp_code": emp_code,
            "date": {"$gte": start, "$lte": end},
        }},
        {"$addFields": {
            "is_weekday": {
                "$let": {
                    "vars": {"dow": {"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}},
                    "in": {"$and": [{"$gte": ["$$dow", 2]}, {"$lte": ["$$dow", 6]}]}
                }
            },
            # present_days contribution: presence + not half_day = 1, half_day = 0.5, else 0
            # Only weekdays count for present_days (R7)
            "is_present": {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
        }},
        {"$group": {
            "_id": None,
            "present_days": {"$sum": {
                "$cond": [
                    {"$and": [
                        {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                        "$is_weekday",
                    ]},
                    {"$cond": [{"$eq": [{"$ifNull": ["$half_day", False]}, True]}, 0.5, 1]},
                    0,
                ]
            }},
            "leave_days": {"$sum": {
                "$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]
            }},
            "late_count": {"$sum": {
                "$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]
            }},
            "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
            "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}},
        }},
    ]

    results = list(db.attendance_logs.aggregate(pipeline))
    if results:
        r = results[0]
        present_days     = r["present_days"]
        leave_days       = r["leave_days"]
        late_count       = r["late_count"]
        total_late       = r["total_late_minutes"]
        total_overtime   = r["total_overtime_minutes"]
    else:
        present_days = leave_days = late_count = total_late = total_overtime = 0

    attendance_pct = (
        half_up_round(present_days / working_days * 100, 2)
        if working_days > 0 else None
    )

    return {
        "emp_code":              emp_code,
        "month":                 month,
        "working_days":          working_days,
        "present_days":          present_days,
        "leave_days":            leave_days,
        "late_count":            late_count,
        "total_late_minutes":    total_late,
        "total_overtime_minutes": total_overtime,
        "attendance_pct":        attendance_pct,
    }


# ── DEPARTMENT SUMMARY ───────────────────────────────────────────────────────

@app.get("/analytics/departments/summary")
def department_summary(
    month:      str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = None,
):
    start, end = _month_date_range(month)

    # Step 1: get all employees matching department filter (for headcount R9)
    emp_filter: dict = {}
    if department:
        emp_filter["department"] = department

    # Headcount: employees whose joined_on <= end of month
    emp_filter_hc = dict(emp_filter)
    emp_filter_hc["joined_on"] = {"$lte": end}

    # Aggregate employees by department → headcount
    emp_pipeline = [
        {"$match": emp_filter_hc},
        {"$group": {
            "_id": "$department",
            "headcount": {"$sum": 1},
            "emp_codes": {"$push": "$emp_code"},
        }},
    ]
    emp_by_dept = {r["_id"]: r for r in db.employees.aggregate(emp_pipeline)}

    if not emp_by_dept:
        return {"month": month, "items": []}

    # Step 2: aggregate attendance_logs for those employees in the month
    log_match: dict = {"date": {"$gte": start, "$lte": end}}
    if department:
        # Get emp_codes for this department
        dept_emp_codes = emp_by_dept.get(department, {}).get("emp_codes", [])
        if not dept_emp_codes:
            return {"month": month, "items": []}
        log_match["emp_code"] = {"$in": dept_emp_codes}

    log_pipeline = [
        {"$match": log_match},
        # Join to get department
        {"$lookup": {
            "from": "employees",
            "localField": "emp_code",
            "foreignField": "emp_code",
            "as": "emp",
        }},
        {"$unwind": {"path": "$emp", "preserveNullAndEmptyArrays": False}},
        {"$addFields": {
            "dept": "$emp.department",
            "is_weekday": {
                "$let": {
                    "vars": {"dow": {"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}},
                    "in": {"$and": [{"$gte": ["$$dow", 2]}, {"$lte": ["$$dow", 6]}]}
                }
            },
        }},
        {"$group": {
            "_id": "$dept",
            "present_days": {"$sum": {
                "$cond": [
                    {"$and": [
                        {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                        "$is_weekday",
                    ]},
                    {"$cond": [{"$eq": [{"$ifNull": ["$half_day", False]}, True]}, 0.5, 1]},
                    0,
                ]
            }},
            "wh_sum": {"$sum": {
                "$cond": [
                    {"$and": [
                        {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                        {"$ne": [{"$type": "$work_hours"}, "null"]},
                        {"$ne": ["$work_hours", None]},
                    ]},
                    "$work_hours",
                    0,
                ]
            }},
            "wh_count": {"$sum": {
                "$cond": [
                    {"$and": [
                        {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                        {"$ne": [{"$type": "$work_hours"}, "null"]},
                        {"$ne": ["$work_hours", None]},
                    ]},
                    1,
                    0,
                ]
            }},
            "late_count": {"$sum": {
                "$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]
            }},
            "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
            "leave_count":    {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
            "on_duty_count":  {"$sum": {"$cond": [{"$eq": ["$status", "ON_DUTY"]}, 1, 0]}},
        }},
    ]
    log_by_dept = {r["_id"]: r for r in db.attendance_logs.aggregate(log_pipeline)}

    items = []
    for dept, emp_row in sorted(emp_by_dept.items()):
        lr = log_by_dept.get(dept, {})
        wh_count = lr.get("wh_count", 0)
        avg_wh   = (
            half_up_round(lr["wh_sum"] / wh_count, 2) if wh_count > 0 else None
        )
        items.append({
            "department":        dept,
            "headcount":         emp_row["headcount"],
            "present_days":      lr.get("present_days", 0),
            "avg_work_hours":    avg_wh,
            "late_count":        lr.get("late_count", 0),
            "total_late_minutes": lr.get("total_late_minutes", 0),
            "leave_count":       lr.get("leave_count", 0),
            "on_duty_count":     lr.get("on_duty_count", 0),
        })

    return {"month": month, "items": items}


# ── LATE LEADERBOARD ─────────────────────────────────────────────────────────

@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month:      str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit:      int = Query(default=10, ge=1, le=50),
    department: Optional[str] = None,
):
    start, end = _month_date_range(month)

    match_stage: dict = {
        "date": {"$gte": start, "$lte": end},
        "late_minutes": {"$gt": 0},
    }

    pipeline = [
        {"$match": match_stage},
        # Join employee to get name, department, filter by dept
        {"$lookup": {
            "from": "employees",
            "localField": "emp_code",
            "foreignField": "emp_code",
            "as": "emp",
        }},
        {"$unwind": {"path": "$emp", "preserveNullAndEmptyArrays": False}},
    ]
    if department:
        pipeline.append({"$match": {"emp.department": department}})

    pipeline += [
        {"$group": {
            "_id": "$emp_code",
            "name":               {"$first": "$emp.name"},
            "department":         {"$first": "$emp.department"},
            "total_late_minutes": {"$sum": "$late_minutes"},
            "late_count":         {"$sum": 1},
        }},
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
        # Dense rank via $setWindowFields (MongoDB 5.0+)
        {"$setWindowFields": {
            "sortBy": {"total_late_minutes": -1},
            "output": {
                "rank": {
                    "$rank": {}
                }
            }
        }},
        # Return everyone whose rank <= limit
        {"$match": {"rank": {"$lte": limit}}},
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
        {"$project": {
            "_id": 0,
            "rank":               1,
            "emp_code":           "$_id",
            "name":               1,
            "department":         1,
            "total_late_minutes": 1,
            "late_count":         1,
        }},
    ]

    items = list(db.attendance_logs.aggregate(pipeline))
    return {"month": month, "items": items}


# ── DEPARTMENT TREND ─────────────────────────────────────────────────────────

@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date:  str = Query(..., alias="from"),
    to_date:    str = Query(..., alias="to"),
):
    # Validate: to >= from, range <= 92 days
    try:
        d_from = date_type.fromisoformat(from_date)
        d_to   = date_type.fromisoformat(to_date)
    except ValueError:
        raise HTTPException(422, detail="Invalid date format")

    if d_to < d_from:
        raise HTTPException(422, detail="'to' must be >= 'from'")
    if (d_to - d_from).days > 91:  # 92 days means max 91 day difference
        raise HTTPException(422, detail="Date range exceeds 92 days")

    # 404 if no employees in department
    dept_emps = list(db.employees.find({"department": department}, {"emp_code": 1, "joined_on": 1}))
    if not dept_emps:
        raise HTTPException(404, detail="Department not found")

    start = from_date
    end   = to_date
    d_start_dt = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    d_end_dt   = (datetime.fromisoformat(end) + timedelta(days=1)).replace(tzinfo=timezone.utc)

    # Aggregate attendance logs for this department in the range
    emp_codes = [e["emp_code"] for e in dept_emps]

    log_pipeline = [
        {"$match": {
            "emp_code": {"$in": emp_codes},
            "date":     {"$gte": start, "$lte": end},
        }},
        {"$addFields": {
            "present_contrib": {
                "$cond": [
                    {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                    {"$cond": [{"$eq": [{"$ifNull": ["$half_day", False]}, True]}, 0.5, 1]},
                    0,
                ]
            },
            "is_late": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]},
        }},
        {"$group": {
            "_id":            "$date",
            "present_count":  {"$sum": "$present_contrib"},
            "late_count":     {"$sum": "$is_late"},
        }},
        # Gap-fill with $densify over the date string range
        # We use $dateFromString → densify with BSON datetimes → $dateToString
        {"$addFields": {"date_obj": {"$dateFromString": {"dateString": "$_id"}}}},
        {"$densify": {
            "field": "date_obj",
            "range": {
                "step": 1,
                "unit": "day",
                "bounds": [d_start_dt, d_end_dt]
            }
        }},
        {"$addFields": {
            "date_str":      {"$dateToString": {"format": "%Y-%m-%d", "date": "$date_obj"}},
            "present_count": {"$ifNull": ["$present_count", 0]},
            "late_count":    {"$ifNull": ["$late_count", 0]},
        }},
        {"$sort": {"date_str": 1}},
    ]

    raw_rows = list(db.attendance_logs.aggregate(log_pipeline))

    # Build final items in Python (headcount per day, is_working_day, rates, moving avg)
    items = []
    for row in raw_rows:
        day_str = row["date_str"]
        d       = date_type.fromisoformat(day_str)
        is_wd   = d.weekday() < 5

        # Headcount: employees joined_on <= day (R9)
        headcount = sum(1 for e in dept_emps if e["joined_on"] <= day_str)

        present_count = row["present_count"]
        late_count    = row["late_count"]

        if is_wd and headcount > 0:
            att_rate = half_up_round(present_count / headcount, 4)
        else:
            att_rate = None

        items.append({
            "date":            day_str,
            "is_working_day":  is_wd,
            "headcount":       headcount,
            "present_count":   present_count,
            "late_count":      late_count,
            "attendance_rate": att_rate,
            "_idx":            len(items),
        })

    # Compute moving_avg_7d: mean of non-null attendance_rate in current + up to 6 preceding rows
    for i, item in enumerate(items):
        window_rates = [
            items[j]["attendance_rate"]
            for j in range(max(0, i - 6), i + 1)
            if items[j]["attendance_rate"] is not None
        ]
        if window_rates:
            item["moving_avg_7d"] = half_up_round(sum(window_rates) / len(window_rates), 4)
        else:
            item["moving_avg_7d"] = None
        del item["_idx"]

    return {"department": department, "items": items}


# ── ADMIN EXPLAIN ─────────────────────────────────────────────────────────────

def _sanitize_explain(doc: Any) -> Any:
    return json.loads(json_util.dumps(doc))


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint:  str,
    emp_code:  Optional[str] = None,
    month:     Optional[str] = Query(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = None,
    limit:     int = Query(default=10, ge=1, le=50),
    date_from: Optional[str] = None,
    date_to:   Optional[str] = None,
    status:    Optional[str] = None,
    from_date: Optional[str] = Query(default=None, alias="from"),
    to_date:   Optional[str] = Query(default=None, alias="to"),
    page:      int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    VALID_ENDPOINTS = {
        "attendance_list", "employee_monthly", "department_summary",
        "late_leaderboard", "department_trend",
    }
    if endpoint not in VALID_ENDPOINTS:
        raise HTTPException(422, detail=f"Unknown endpoint. Choose from {sorted(VALID_ENDPOINTS)}")

    if endpoint == "attendance_list":
        if date_from and date_to and date_from > date_to:
            raise HTTPException(422, detail="date_from must not be after date_to")
        q: dict = {}
        if emp_code:
            q["emp_code"] = emp_code
        if date_from or date_to:
            q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
        if status:
            q["status"] = status
        skip = (page - 1) * page_size
        explained = db.attendance_logs.find(q).sort(
            [("date", DESCENDING), ("emp_code", ASCENDING)]
        ).skip(skip).limit(page_size).explain()
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": _sanitize_explain(explained)}

    if endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(422, detail="emp_code and month are required")
        start, end = _month_date_range(month)
        pipeline = [
            {"$match": {"emp_code": emp_code, "date": {"$gte": start, "$lte": end}}},
            {"$group": {"_id": None, "count": {"$sum": 1}}},
        ]
        # For pymongo 4.x aggregate explain returns a dict directly via command
        explained_doc = db.command(
            "aggregate", "attendance_logs",
            pipeline=pipeline,
            explain=True,
        )
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": _sanitize_explain(explained_doc)}

    if endpoint == "department_summary":
        if not month:
            raise HTTPException(422, detail="month is required")
        start, end = _month_date_range(month)
        emp_filter_hc: dict = {"joined_on": {"$lte": end}}
        if department:
            emp_filter_hc["department"] = department
        explained_doc = db.command(
            "aggregate", "employees",
            pipeline=[
                {"$match": emp_filter_hc},
                {"$group": {"_id": "$department", "headcount": {"$sum": 1}}},
            ],
            explain=True,
        )
        return {"endpoint": endpoint, "collection": "employees", "explain": _sanitize_explain(explained_doc)}

    if endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(422, detail="month is required")
        start, end = _month_date_range(month)
        match_s: dict = {"date": {"$gte": start, "$lte": end}, "late_minutes": {"$gt": 0}}
        pipeline = [
            {"$match": match_s},
            {"$lookup": {"from": "employees", "localField": "emp_code", "foreignField": "emp_code", "as": "emp"}},
            {"$unwind": "$emp"},
        ]
        if department:
            pipeline.append({"$match": {"emp.department": department}})
        pipeline += [
            {"$group": {"_id": "$emp_code", "total_late_minutes": {"$sum": "$late_minutes"}}},
            {"$sort": {"total_late_minutes": -1}},
        ]
        explained_doc = db.command("aggregate", "attendance_logs", pipeline=pipeline, explain=True)
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": _sanitize_explain(explained_doc)}

    if endpoint == "department_trend":
        if not department or not from_date or not to_date:
            raise HTTPException(422, detail="department, from, and to are required")
        try:
            d_from = date_type.fromisoformat(from_date)
            d_to   = date_type.fromisoformat(to_date)
        except ValueError:
            raise HTTPException(422, detail="Invalid date format")
        if d_to < d_from:
            raise HTTPException(422, detail="'to' must be >= 'from'")
        if (d_to - d_from).days > 91:
            raise HTTPException(422, detail="Date range exceeds 92 days")
        dept_emps = list(db.employees.find({"department": department}, {"emp_code": 1}))
        if not dept_emps:
            raise HTTPException(404, detail="Department not found")
        emp_codes = [e["emp_code"] for e in dept_emps]
        pipeline = [
            {"$match": {"emp_code": {"$in": emp_codes}, "date": {"$gte": from_date, "$lte": to_date}}},
            {"$group": {"_id": "$date", "present_count": {"$sum": 1}}},
        ]
        explained_doc = db.command("aggregate", "attendance_logs", pipeline=pipeline, explain=True)
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": _sanitize_explain(explained_doc)}
