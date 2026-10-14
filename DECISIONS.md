# DECISIONS.md

1. **Indexes.**

   I created four indexes at startup (all idempotent via `create_index`, safe to re-run on restart):

   - **`employees.emp_code` (unique)** — this is the primary business key, so I wanted uniqueness enforced at the DB level rather than in application code. Concurrent duplicate `POST /employees` calls get resolved by MongoDB itself — one wins with 201, the rest hit a `DuplicateKeyError` which I catch and turn into 409. I deliberately skipped the `find_one`-before-`insert_one` pattern because that race window is exactly what you don't want.
   - **`employees.department`** — speeds up the department filter on `GET /employees` and the `$lookup` joins in the analytics pipelines.
   - **`attendance_logs.(emp_code, date)` (unique)** — the natural compound key for this collection. One record per employee per day, enforced at the database layer. Also makes all single-record lookups (punch-in duplicate check, punch-out, regularize) a single index seek.
   - **`attendance_logs.(date DESC, emp_code ASC)`** — mirrors the sort on `GET /attendance` exactly, so the query is satisfied entirely from the index with no in-memory sort. This is what keeps it fast at ~100k records.

   One index I considered but didn't create: a standalone `attendance_logs.punch_in` index. The punch-out endpoint does query `punch_in: {$lte: ts}`, but it's always scoped to a single `emp_code` first, so the compound `(emp_code, date)` index already narrows the scan to just that employee's records. An extra index would be wasted space.

2. **Punch-in race condition.**

   Two identical punch-in requests arrive at the same instant. Both pass the 404 employee check. Both call `insert_one` with the same `emp_code` and resolved attendance `date`. MongoDB's unique index on `(emp_code, date)` ensures exactly one of those inserts commits — the other raises `DuplicateKeyError`. I catch that and return 409. There's no `find_one` check before the insert — the index is the only lock that matters here.

3. **Ties in the leaderboard.**

   I used MongoDB's `$rank` window function, which implements standard competition ranking (1, 2, 2, 4 — not 1, 2, 2, 3). After computing ranks, I filter with `{"rank": {"$lte": limit}}`. So if three employees are all tied at rank 10 and `limit` is 10, all three come back. That's intentional and exactly what the spec requires: "return every employee whose rank ≤ limit." Using the array index as a rank instead would silently cut off tied entries at the boundary.

4. **Headcount includes employees with zero logs.**

   I split this into two separate aggregations. First I query `employees` (filtered by `joined_on <= month_end`) to get headcount per department — that's the authoritative source. Then I separately query `attendance_logs` for those employees and join the two in Python. I avoided doing a single `$lookup` starting from the logs side because that would naturally miss employees who never punched in. Keeping headcount anchored to the employees collection means zero-log employees are included for free.

5. **One thing I'd change for 100× the data.**

   At 10M records, the department trend endpoint would hurt the most. Right now I do a Python-side loop to count `joined_on <= day` for each day in the range — that's fine for small departments over a few weeks, but degrades fast at scale. I'd push the headcount calculation into a MongoDB aggregation (pre-joining employees with the date range), and add a compound index on `(date, emp_code, late_minutes)` to support index-only scans for the late leaderboard. Long-term I'd consider a nightly materialized view for per-day department stats to make the trend query a straight lookup.
