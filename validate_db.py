"""
validate_db.py — Data quality validation for cps-trip-sync Azure SQL tables.

Checks:
  - Row counts per table
  - Company distribution in trips
  - Date window coverage
  - NULL rates on critical fields (vehicle, driver, trailer)
  - Stuck / failed rows in trips_pending_sync
  - Conflict guard stats (cps_updated_at > nav_updated_at)
  - Route and trip_list join integrity

Usage:
    python validate_db.py          # test DB (.env)
    python validate_db.py prod     # prod DB (.env.prod)
"""
from pathlib import Path
import sys
import dotenv

BASE_DIR = Path(__file__).resolve().parent
env      = sys.argv[1] if len(sys.argv) > 1 else "test"
env_file = ".env.prod" if env == "prod" else ".env"
dotenv.load_dotenv(BASE_DIR / env_file)

from db_client import get_connection
from datetime import datetime

SEP  = "=" * 60
SEP2 = "-" * 60


def pct(n, total):
    return f"{n / total * 100:.1f}%" if total else "n/a"


def section(title):
    print(f"\n{SEP2}")
    print(f"  {title}")
    print(SEP2)


def run():
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{SEP}")
    print(f"  cps-trip-sync DB Validation [{env.upper()}]  —  {ts}")
    print(SEP)

    with get_connection() as conn:
        cur = conn.cursor()

        # ------------------------------------------------------------------ #
        # 1. Row counts
        # ------------------------------------------------------------------ #
        section("1. Row counts")
        tables = ["trips", "routes", "trip_list", "trip_additional_resources",
                  "trips_pending_sync"]
        for t in tables:
            cur.execute(f"SELECT COUNT(*) FROM {t}")
            print(f"  {t:<35} {cur.fetchone()[0]:>8,} rows")

        # ------------------------------------------------------------------ #
        # 2. Company distribution
        # ------------------------------------------------------------------ #
        section("2. Company distribution (trips)")
        cur.execute("""
            SELECT company, COUNT(*) AS cnt
            FROM trips
            GROUP BY company
            ORDER BY cnt DESC
        """)
        rows = cur.fetchall()
        total_trips = sum(r[1] for r in rows)
        for company, cnt in rows:
            bar = "█" * min(40, int(cnt / max(total_trips, 1) * 40))
            print(f"  {(company or 'NULL'):<35} {cnt:>6,}  {bar}")
        print(f"  {'TOTAL':<35} {total_trips:>6,}")

        # ------------------------------------------------------------------ #
        # 3. Date window coverage
        # ------------------------------------------------------------------ #
        section("3. Date window coverage (trips.starting_date)")
        cur.execute("""
            SELECT
                MIN(starting_date) AS min_date,
                MAX(starting_date) AS max_date,
                SUM(CASE WHEN starting_date IS NULL THEN 1 ELSE 0 END) AS null_dates
            FROM trips
        """)
        row = cur.fetchone()
        print(f"  Min starting_date : {row[0]}")
        print(f"  Max starting_date : {row[1]}")
        print(f"  NULL starting_date: {row[2]}")

        cur.execute("""
            SELECT
                SUM(CASE WHEN CAST(starting_date AS DATE) < CAST(GETUTCDATE() AS DATE) THEN 1 ELSE 0 END) AS past,
                SUM(CASE WHEN CAST(starting_date AS DATE) = CAST(GETUTCDATE() AS DATE) THEN 1 ELSE 0 END) AS today,
                SUM(CASE WHEN CAST(starting_date AS DATE) > CAST(GETUTCDATE() AS DATE) THEN 1 ELSE 0 END) AS future
            FROM trips
            WHERE starting_date IS NOT NULL
        """)
        p, t2, f = cur.fetchone()
        print(f"  Past / Today / Future: {p:,} / {t2:,} / {f:,}")

        # ------------------------------------------------------------------ #
        # 4. Resource field coverage
        # ------------------------------------------------------------------ #
        section("4. Resource field coverage (trips)")
        cur.execute("""
            SELECT
                COUNT(*)                                               AS total,
                SUM(CASE WHEN vehicle IS NOT NULL AND vehicle != '' THEN 1 ELSE 0 END) AS has_vehicle,
                SUM(CASE WHEN trailer IS NOT NULL AND trailer != '' THEN 1 ELSE 0 END) AS has_trailer,
                SUM(CASE WHEN driver  IS NOT NULL AND driver  != '' THEN 1 ELSE 0 END) AS has_driver,
                SUM(CASE WHEN driver_2 IS NOT NULL AND driver_2 != '' THEN 1 ELSE 0 END) AS has_driver2,
                SUM(CASE WHEN status IS NOT NULL THEN 1 ELSE 0 END)   AS has_status
            FROM trips
        """)
        tot, v, tr, d, d2, st = cur.fetchone()
        print(f"  Total trips    : {tot:>8,}")
        print(f"  Has vehicle    : {v:>8,}  ({pct(v, tot)})")
        print(f"  Has trailer    : {tr:>8,}  ({pct(tr, tot)})")
        print(f"  Has driver     : {d:>8,}  ({pct(d, tot)})")
        print(f"  Has driver_2   : {d2:>8,}  ({pct(d2, tot)})")
        print(f"  Has status     : {st:>8,}  ({pct(st, tot)})")

        # Status breakdown
        cur.execute("""
            SELECT status, COUNT(*) AS cnt
            FROM trips
            GROUP BY status
            ORDER BY cnt DESC
        """)
        print(f"\n  Status breakdown:")
        for s_val, cnt in cur.fetchall():
            print(f"    {(s_val or 'NULL'):<10} {cnt:>6,}")

        # ------------------------------------------------------------------ #
        # 5. Sync metadata freshness
        # ------------------------------------------------------------------ #
        section("5. Sync metadata freshness")
        cur.execute("""
            SELECT
                MIN(nav_updated_at)  AS min_nav_upd,
                MAX(nav_updated_at)  AS max_nav_upd,
                SUM(CASE WHEN nav_updated_at IS NULL THEN 1 ELSE 0 END) AS null_nav,
                SUM(CASE WHEN cps_updated_at IS NOT NULL THEN 1 ELSE 0 END) AS has_cps_upd,
                SUM(CASE WHEN synced_up_at IS NOT NULL THEN 1 ELSE 0 END)   AS has_synced_up
            FROM trips
        """)
        min_n, max_n, null_n, cps_upd, synced = cur.fetchone()
        print(f"  nav_updated_at  min: {min_n}")
        print(f"  nav_updated_at  max: {max_n}")
        print(f"  nav_updated_at NULL: {null_n}")
        print(f"  cps_updated_at set : {cps_upd}")
        print(f"  synced_up_at   set : {synced}")

        # ------------------------------------------------------------------ #
        # 6. Conflict guard — CPS overrides pending NAV sync
        # ------------------------------------------------------------------ #
        section("6. Conflict guard (cps_updated_at > nav_updated_at)")
        cur.execute("""
            SELECT COUNT(*) FROM trips
            WHERE cps_updated_at IS NOT NULL
              AND nav_updated_at IS NOT NULL
              AND cps_updated_at > nav_updated_at
        """)
        conflicts = cur.fetchone()[0]
        print(f"  Trips where CPS is newer than last NAV sync: {conflicts}")
        if conflicts > 0:
            print("  ⚠  These rows are protected from Down-sync overwrite (expected if TripData_Up hasn't run yet).")

        # ------------------------------------------------------------------ #
        # 7. trips_pending_sync queue status
        # ------------------------------------------------------------------ #
        section("7. trips_pending_sync queue")
        cur.execute("""
            SELECT status, COUNT(*) AS cnt, MAX(attempts) AS max_att
            FROM trips_pending_sync
            GROUP BY status
            ORDER BY cnt DESC
        """)
        rows = cur.fetchall()
        if not rows:
            print("  Queue is empty.")
        else:
            print(f"  {'Status':<12} {'Count':>8}  {'Max attempts':>14}")
            for s_val, cnt, max_att in rows:
                flag = " ⚠" if s_val == "failed" else ""
                print(f"  {(s_val or 'NULL'):<12} {cnt:>8,}  {(max_att or 0):>14}{flag}")

        cur.execute("""
            SELECT id, trip_no, line_no, attempts, created_at, last_attempt_at, status
            FROM trips_pending_sync
            WHERE status = 'failed'
            ORDER BY created_at DESC
        """)
        failed = cur.fetchall()
        if failed:
            print(f"\n  Failed rows (top 10):")
            for row in failed[:10]:
                print(f"    id={row[0]}  trip={row[1]}/{row[2]}  attempts={row[3]}  created={row[4]}  status={row[6]}")

        # Stuck pending rows (>15 min old)
        cur.execute("""
            SELECT COUNT(*) FROM trips_pending_sync
            WHERE status = 'pending'
              AND created_at < DATEADD(MINUTE, -15, GETUTCDATE())
        """)
        stuck = cur.fetchone()[0]
        if stuck:
            print(f"\n  ⚠  Stuck pending rows (>15 min old): {stuck}")
        else:
            print(f"\n  No stuck pending rows.")

        # ------------------------------------------------------------------ #
        # 8. Route / trip_list join integrity
        # ------------------------------------------------------------------ #
        section("8. Join integrity")
        cur.execute("""
            SELECT COUNT(DISTINCT trip_no) FROM trips
        """)
        distinct_trips = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(DISTINCT trip_no) FROM routes
        """)
        trips_with_routes = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(DISTINCT trip_no) FROM trip_list
        """)
        trips_with_triplist = cur.fetchone()[0]

        # Trips in DB but no routes
        cur.execute("""
            SELECT COUNT(DISTINCT t.trip_no)
            FROM trips t
            LEFT JOIN routes r ON r.trip_no = t.trip_no
            WHERE r.trip_no IS NULL
        """)
        trips_no_routes = cur.fetchone()[0]

        print(f"  Distinct trip_no in trips  : {distinct_trips:>8,}")
        print(f"  Distinct trip_no in routes : {trips_with_routes:>8,}  ({pct(trips_with_routes, distinct_trips)} coverage)")
        print(f"  Distinct trip_no in trip_list: {trips_with_triplist:>6,}  ({pct(trips_with_triplist, distinct_trips)} coverage)")
        print(f"  Trips with NO routes       : {trips_no_routes:>8,}")
        if trips_no_routes > 0:
            print("  ℹ  Some trips may legitimately have no route stops.")

        # ------------------------------------------------------------------ #
        # 9. Sample — next 3 days (CPS operational window)
        # ------------------------------------------------------------------ #
        section("9. Sample — today's trips (top 10)")
        cur.execute("""
            SELECT TOP 10
                t.trip_no, t.line_no, t.company,
                CAST(t.starting_date AS DATE) AS start,
                t.vehicle, t.driver, t.status,
                (SELECT COUNT(*) FROM routes r WHERE r.trip_no = t.trip_no) AS route_stops,
                (SELECT COUNT(*) FROM trip_list tl WHERE tl.trip_no = t.trip_no) AS tl_rows
            FROM trips t
            WHERE CAST(t.starting_date AS DATE) = CAST(GETUTCDATE() AS DATE)
            ORDER BY t.starting_date, t.trip_no
        """)
        sample = cur.fetchall()
        if not sample:
            print("  No trips today.")
        else:
            print(f"  {'Trip_No':<15} {'Line':>5}  {'Company':<30}  {'Start':<12}  "
                  f"{'Vehicle':<12}  {'Driver':<10}  {'Status':<6}  {'Routes':>6}  {'TripList':>8}")
            for r in sample:
                print(f"  {(r[0] or ''):<15} {r[1]:>5}  {(r[2] or ''):<30}  {str(r[3]):<12}  "
                      f"{(r[4] or ''):<12}  {(r[5] or ''):<10}  {(r[6] or ''):<6}  {r[7]:>6}  {r[8]:>8}")

    print(f"\n{SEP}")
    print(f"  Validation complete — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{SEP}\n")


if __name__ == "__main__":
    run()
