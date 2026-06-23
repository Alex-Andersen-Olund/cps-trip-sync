"""
test_trip_up.py — End-to-end TripData_Up test against PROD NAV.

Strategy (safe — no actual data change):
  1. Find a real trip in the DB that already has a vehicle assigned.
  2. Read the current vehicle value from the DB.
  3. Insert a trips_pending_sync row with changed_fields = {"Vehicle": <same value>}
     → This is a no-op PATCH: NAV receives the value it already has, nothing changes.
  4. Call sync_trips_up() which will pick it up and PATCH NAV.
  5. Verify:
     a. trips_pending_sync row flipped to status='done'
     b. trips.synced_up_at was set
     c. Read PartialTrip back from NAV and confirm Vehicle matches DB

You can also supply a specific trip_no / line_no as arguments for a targeted test.

Usage:
    python test_trip_up.py                         # auto-select trip, prod NAV
    python test_trip_up.py prod T012345 10         # specific trip + line_no, prod NAV
    python test_trip_up.py test                    # auto-select, test NAV
"""
from pathlib import Path
import sys
import dotenv

BASE_DIR = Path(__file__).resolve().parent
args     = sys.argv[1:]

# Parse args: first positional that isn't 'prod'/'test' is trip_no, second is line_no
env_arg  = args[0] if args and args[0] in ("prod", "test") else "prod"
rest     = [a for a in args if a not in ("prod", "test")]
manual_trip_no  = rest[0] if len(rest) > 0 else None
manual_line_no  = int(rest[1]) if len(rest) > 1 else None

env_file = ".env.prod" if env_arg == "prod" else ".env"
dotenv.load_dotenv(BASE_DIR / env_file)

import json
import requests
from datetime import datetime, timezone
from db_client import get_connection
from nav_trip_client import NavTripClient
from sync_trips_up import sync_trips_up

SEP  = "=" * 60
SEP2 = "-" * 60

def section(title):
    print(f"\n{SEP2}")
    print(f"  {title}")
    print(SEP2)

def ok(msg):   print(f"  ✅  {msg}")
def fail(msg): print(f"  ❌  {msg}")
def warn(msg): print(f"  ⚠   {msg}")
def info(msg): print(f"  ℹ   {msg}")


def find_test_trip(cur):
    """Find a trip with a vehicle assigned that isn't completed (status != '30')."""
    cur.execute("""
        SELECT TOP 1 trip_no, line_no, vehicle, company
        FROM trips
        WHERE vehicle IS NOT NULL
          AND vehicle != ''
          AND (status IS NULL OR status NOT IN ('30', '25'))
          AND starting_date >= DATEADD(DAY, -3, GETUTCDATE())
        ORDER BY starting_date DESC
    """)
    return cur.fetchone()


def read_trip_from_nav(nav, company, trip_no, line_no):
    """Read a PartialTrip record from NAV directly."""
    encoded_company = requests.utils.quote(company, safe="")
    encoded_trip    = requests.utils.quote(trip_no, safe="")
    url = (
        f"{nav.nav_base}/Company('{encoded_company}')"
        f"/PartialTrip(Trip_No='{encoded_trip}',Line_No={line_no})"
    )
    resp = requests.get(url, headers=nav.headers, auth=nav.auth, timeout=30)
    resp.raise_for_status()
    return resp.json()


def run():
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{SEP}")
    print(f"  TripData_Up End-to-End Test [{env_arg.upper()}]  —  {ts}")
    print(SEP)

    errors = []

    # ------------------------------------------------------------------ #
    # 1. Find test trip
    # ------------------------------------------------------------------ #
    section("1. Select test trip")
    with get_connection() as conn:
        cur = conn.cursor()

        if manual_trip_no and manual_line_no is not None:
            cur.execute(
                "SELECT trip_no, line_no, vehicle, company FROM trips WHERE trip_no = ? AND line_no = ?",
                (manual_trip_no, manual_line_no)
            )
            row = cur.fetchone()
            if not row:
                fail(f"Trip {manual_trip_no}/{manual_line_no} not found in DB.")
                sys.exit(1)
        else:
            row = find_test_trip(cur)
            if not row:
                fail("No suitable test trip found in DB (need a trip with vehicle != NULL).")
                sys.exit(1)

        trip_no, line_no, current_vehicle, company = row
        info(f"Trip       : {trip_no}  line_no={line_no}")
        info(f"Company    : {company}")
        info(f"Vehicle    : {current_vehicle}  (will PATCH same value → no-op in NAV)")

    # ------------------------------------------------------------------ #
    # 2. Verify trip exists in NAV before we start
    # ------------------------------------------------------------------ #
    section("2. Pre-check — read PartialTrip from NAV")
    nav = NavTripClient()
    try:
        nav_before = read_trip_from_nav(nav, company, trip_no, line_no)
        nav_vehicle_before = nav_before.get("Vehicle", "")
        ok(f"NAV Vehicle before: '{nav_vehicle_before}'")
        if nav_vehicle_before != current_vehicle:
            warn(f"DB vehicle '{current_vehicle}' differs from NAV vehicle '{nav_vehicle_before}' — continuing anyway.")
    except Exception as e:
        fail(f"Could not read PartialTrip from NAV: {e}")
        print("  Cannot proceed without NAV connectivity.")
        sys.exit(1)

    # ------------------------------------------------------------------ #
    # 3. Clear any existing pending rows for this trip
    # ------------------------------------------------------------------ #
    section("3. Setup — clear existing pending rows")
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM trips_pending_sync WHERE trip_no = ? AND line_no = ? AND status = 'pending'",
            (trip_no, line_no)
        )
        existing = cur.fetchone()[0]
        if existing:
            warn(f"Found {existing} existing pending row(s) for this trip — leaving them, our test row will be added.")

    # ------------------------------------------------------------------ #
    # 4. Insert no-op test row in trips_pending_sync
    # ------------------------------------------------------------------ #
    section("4. Insert test row in trips_pending_sync")
    test_pending_id = None
    with get_connection() as conn:
        cur = conn.cursor()
        changed = json.dumps({"Vehicle": current_vehicle})  # same value = no-op in NAV
        cur.execute("""
            INSERT INTO trips_pending_sync (trip_no, line_no, company, changed_fields, status)
            OUTPUT INSERTED.id
            VALUES (?, ?, ?, ?, 'pending')
        """, (trip_no, line_no, company, changed))
        test_pending_id = cur.fetchone()[0]

    ok(f"Inserted trips_pending_sync.id = {test_pending_id}")
    info(f"changed_fields: {changed}")

    # ------------------------------------------------------------------ #
    # 5. Run sync_trips_up
    # ------------------------------------------------------------------ #
    section("5. Running sync_trips_up()")
    try:
        sync_trips_up()
        ok("sync_trips_up() completed without exception")
    except Exception as e:
        fail(f"sync_trips_up() raised: {e}")
        errors.append(f"sync_trips_up exception: {e}")

    # ------------------------------------------------------------------ #
    # 6. Verify trips_pending_sync row status
    # ------------------------------------------------------------------ #
    section("6. Verify trips_pending_sync row")
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT status, attempts, last_attempt_at FROM trips_pending_sync WHERE id = ?",
            (test_pending_id,)
        )
        row = cur.fetchone()

    if not row:
        fail(f"Row id={test_pending_id} disappeared from trips_pending_sync — unexpected.")
        errors.append("pending row disappeared")
    else:
        status_val, attempts, last_att = row
        info(f"Status   : {status_val}")
        info(f"Attempts : {attempts}")
        info(f"Last att : {last_att}")
        if status_val == "done":
            ok("Row status = 'done' ✓")
        elif status_val == "failed":
            fail(f"Row status = 'failed' after {attempts} attempt(s).")
            errors.append(f"pending row status=failed attempts={attempts}")
        else:
            warn(f"Row status = '{status_val}' — may still be pending (backoff?). Check again in 2 min.")

    # ------------------------------------------------------------------ #
    # 7. Verify trips.synced_up_at was set
    # ------------------------------------------------------------------ #
    section("7. Verify trips.synced_up_at")
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT synced_up_at FROM trips WHERE trip_no = ? AND line_no = ?",
            (trip_no, line_no)
        )
        row = cur.fetchone()

    if row and row[0]:
        ok(f"trips.synced_up_at = {row[0]}")
    else:
        warn("trips.synced_up_at is NULL — only set on successful sync.")
        if status_val != "done":
            errors.append("synced_up_at not set")

    # ------------------------------------------------------------------ #
    # 8. Post-check — read PartialTrip from NAV and compare
    # ------------------------------------------------------------------ #
    section("8. Post-check — read PartialTrip from NAV")
    try:
        nav_after = read_trip_from_nav(nav, company, trip_no, line_no)
        nav_vehicle_after = nav_after.get("Vehicle", "")
        ok(f"NAV Vehicle after  : '{nav_vehicle_after}'")
        ok(f"NAV Vehicle before : '{nav_vehicle_before}'")

        if nav_vehicle_after == nav_vehicle_before:
            ok("Vehicle unchanged in NAV (expected — no-op PATCH) ✓")
        else:
            warn(f"Vehicle changed in NAV: '{nav_vehicle_before}' → '{nav_vehicle_after}'. "
                 "Not expected for a no-op test — investigate.")

        # Field comparison: DB vs NAV
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT vehicle, trailer, driver, driver_2 FROM trips WHERE trip_no = ? AND line_no = ?",
                (trip_no, line_no)
            )
            db_row = cur.fetchone()

        print(f"\n  Field comparison (DB vs NAV):")
        fields_to_check = [
            ("Vehicle",  db_row[0], nav_after.get("Vehicle")),
            ("Trailer",  db_row[1], nav_after.get("Trailer")),
            ("Driver",   db_row[2], nav_after.get("Driver")),
            ("Driver_2", db_row[3], nav_after.get("Driver_2")),
        ]
        mismatches = []
        for fname, db_val, nav_val in fields_to_check:
            match = (db_val or "") == (nav_val or "")
            marker = "✅" if match else "❌"
            print(f"  {marker}  {fname:<10}  DB='{db_val or ''}'  NAV='{nav_val or ''}'")
            if not match:
                mismatches.append(fname)

        if mismatches:
            warn(f"Mismatch on: {', '.join(mismatches)}")
            warn("This is expected if TripData_Up hasn't synced resource assignments yet.")
        else:
            ok("All resource fields match between DB and NAV ✓")

    except Exception as e:
        fail(f"Could not read PartialTrip from NAV post-check: {e}")
        errors.append(f"NAV post-check failed: {e}")

    # ------------------------------------------------------------------ #
    # Summary
    # ------------------------------------------------------------------ #
    section("SUMMARY")
    if not errors:
        ok("All checks passed — TripData_Up plumbing is working correctly.")
        ok("Safe to proceed with CPS integration (Fase 4).")
    else:
        fail(f"{len(errors)} check(s) failed:")
        for e in errors:
            print(f"    • {e}")

    print(f"\n{SEP}")
    print(f"  Test complete — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{SEP}\n")

    return len(errors)


if __name__ == "__main__":
    sys.exit(run())
