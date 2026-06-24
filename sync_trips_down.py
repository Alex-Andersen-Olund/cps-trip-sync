"""
sync_trips_down.py — NAV → Azure SQL trip sync (TripData_Down).

Fetches PartialTripAll + Route + TripList + Plan_Info_2 + AdditionalResources
from all NAV companies and upserts into Azure SQL.

Conflict resolution:
  Down sync does NOT overwrite trip resource fields (Vehicle, Trailer, Driver,
  Driver_2) if a pending row exists in trips_pending_sync for that trip —
  meaning CPS has made a change that hasn't been pushed to NAV yet.
  All other fields (dates, status, plan_info etc.) are always updated from NAV.

Performance:
  Each table uses a temp-table + MERGE pattern instead of per-row SELECT+INSERT/UPDATE.
  All rows for a table are bulk-inserted into a #tmp_* table via executemany,
  then a single MERGE statement handles insert/update in one roundtrip.
"""
import logging
from datetime import datetime, timezone, date, timedelta
from db_client import get_connection
from nav_trip_client import NavTripClient


def sync_company_down(company: str, from_date: date = None, to_date: date = None,
                      sync_supplements: bool = True,
                      use_status_filter: bool = False) -> dict:
    """Sync trips for a single company.

    use_status_filter=True  — use Status lt 30 + Starting_Date ge today-7 (preferred)
    use_status_filter=False — use explicit from_date / to_date window

    Called directly by queue triggers (one message per company) so each
    company runs in its own isolated Function instance.

    Returns a summary dict with row counts.
    """
    nav = NavTripClient()
    now = datetime.now(timezone.utc)

    filter_desc = "status<30 + last 7d" if use_status_filter else f"{from_date} → {to_date}"
    logging.info(f"[{company}] fetching trips ({filter_desc})...")
    try:
        trips = nav.get_partial_trips(company, from_date, to_date, use_status_filter)
    except Exception as e:
        logging.warning(f"[{company}] could not fetch trips — {e}")
        return {"trips": 0, "routes": 0, "trip_list": 0, "add_res": 0}

    if not trips:
        logging.info(f"[{company}] 0 trips in window — skipping")
        return {"trips": 0, "routes": 0, "trip_list": 0, "add_res": 0}

    trip_nos = list({t.get("Trip_No", "") for t in trips if t.get("Trip_No")})

    # Only fetch supplements for active (non-delivered) trips.
    # Status '30' = delivered — routes/trip_list won't change, no need to re-sync.
    # This limits supplement batches to ~2700 instead of ~4300 on full window.
    active_trip_nos = list({
        t.get("Trip_No", "") for t in trips
        if t.get("Trip_No") and t.get("Status", "") != "30"
    })
    logging.info(
        f"[{company}] {len(trips)} trips ({len(active_trip_nos)} active), "
        f"fetching routes + trip_list + plan_info_2 + add_res..."
    )

    if sync_supplements:
        routes     = _fetch(company, "routes",      lambda: nav.get_routes(company, active_trip_nos))
        trip_list  = _fetch(company, "trip_list",   lambda: nav.get_trip_list(company, active_trip_nos))
        plan_info2 = _fetch(company, "plan_info_2", lambda: nav.get_plan_info_2(company, active_trip_nos), default={})
        # Additional resources endpoint requires both Trip_No and PT_Line_No filters
        add_res_pairs = [
            (t.get("Trip_No", ""), t.get("Line_No", 0))
            for t in trips if t.get("Additional_Resources")
        ]
        add_res = []
        for tn, ln in add_res_pairs:
            add_res.extend(_fetch(company, f"add_res({tn}/{ln})",
                                  lambda tn=tn, ln=ln: nav.get_additional_resources(company, tn, ln)))
    else:
        routes, trip_list, plan_info2, add_res = [], [], {}, []

    with get_connection() as conn:
        cursor = conn.cursor()

        # ---- build pending-sync set for conflict guard ---- #
        cursor.execute(
            "SELECT DISTINCT trip_no, line_no FROM trips_pending_sync WHERE status = 'pending'"
        )
        pending = {(r[0], r[1]) for r in cursor.fetchall()}

        # ---- upsert via MERGE ---- #
        t_count  = _merge_trips(cursor, trips, plan_info2, pending, company, now)
        r_count  = _merge_routes(cursor, routes, now)
        tl_count = _merge_trip_list(cursor, trip_list, now)
        ar_count = _merge_add_res(cursor, add_res, now)

    summary = {"trips": t_count, "routes": r_count, "trip_list": tl_count, "add_res": ar_count}
    logging.info(f"[{company}] done — trips={t_count} routes={r_count} trip_list={tl_count} add_res={ar_count}")
    return summary


def sync_trips_down(sync_supplements: bool = True):
    """Backward-compat: sync all companies with the default full date window.

    Used by the existing trip_down_timer / trip_down_http until the queue-based
    triggers (trip_down_fast / trip_down_full) are deployed and verified.
    """
    nav = NavTripClient()
    companies = nav.get_companies()
    from_date = date.today() - timedelta(days=3)
    to_date   = date.today() + timedelta(days=7)

    total = {"trips": 0, "routes": 0, "trip_list": 0, "add_res": 0}
    for company in companies:
        result = sync_company_down(company, from_date, to_date, sync_supplements)
        for k in total:
            total[k] += result[k]

    print(f"[sync_trips_down] Done — trips={total['trips']} routes={total['routes']} "
          f"trip_list={total['trip_list']} add_res={total['add_res']}")


# ------------------------------------------------------------------ #
# MERGE helpers
# ------------------------------------------------------------------ #

def _merge_trips(cursor, trips, plan_info2, pending, company, now):
    """Bulk upsert trips via temp table + MERGE.

    Trips with a pending CPS change skip vehicle/trailer/driver/driver_2 on UPDATE
    to avoid overwriting dispatcher work that hasn't synced up to NAV yet.
    """
    if not trips:
        return 0

    cursor.execute("""
        CREATE TABLE #tmp_trips (
            trip_no              NVARCHAR(20),
            line_no              INT,
            partial_trip         INT,
            starting_date        DATETIME2,
            start_time           NVARCHAR(10),
            ending_date          DATETIME2,
            end_time             NVARCHAR(10),
            start_country        NVARCHAR(10),
            start_city           NVARCHAR(MAX),
            end_country          NVARCHAR(10),
            end_city             NVARCHAR(MAX),
            department           NVARCHAR(20),
            plan_department      NVARCHAR(20),
            subdepartment        NVARCHAR(20),
            vehicle              NVARCHAR(20),
            trailer              NVARCHAR(20),
            driver               NVARCHAR(20),
            driver_2             NVARCHAR(20),
            status               NVARCHAR(5),
            plan_info            NVARCHAR(MAX),
            plan_info_2          NVARCHAR(MAX),
            txt_product          NVARCHAR(MAX),
            txt_file             NVARCHAR(50),
            eupl                 NVARCHAR(20),
            additional_resources BIT,
            actual_starting_date DATETIME2,
            actual_ending_date   DATETIME2,
            has_pending          BIT
        )
    """)

    rows = []
    for t in trips:
        trip_no = (t.get("Trip_No") or "").strip()
        line_no = t.get("Line_No") or 0
        if not trip_no:
            continue
        plan_i2 = plan_info2.get((trip_no, line_no), "") or ""
        rows.append((
            trip_no, line_no, t.get("Partial_trip"),
            _parse_date(t.get("Starting_Date")), t.get("Start_Time"),
            _parse_date(t.get("Ending_Date")), t.get("End_Time"),
            t.get("Start_Country"), t.get("Start_City"),
            t.get("End_Country"), t.get("End_City"),
            t.get("Department"), t.get("Plan_Department"), t.get("Subdepartment"),
            t.get("Vehicle"), t.get("Trailer"), t.get("Driver"), t.get("Driver_2"),
            t.get("Status"), t.get("Plan_Info"), plan_i2,
            t.get("txtProduct"), t.get("txtFile"), t.get("EUPL"),
            1 if t.get("Additional_Resources") else 0,
            _parse_date(t.get("Actual_Starting_Date")),
            _parse_date(t.get("Actual_Ending_Date")),
            1 if (trip_no, line_no) in pending else 0,
        ))

    cursor.executemany(
        "INSERT INTO #tmp_trips VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows
    )

    # Two WHEN MATCHED branches: one for normal trips (full update), one for
    # pending trips (skip resource assignment fields)
    cursor.execute("""
        MERGE trips AS tgt
        USING #tmp_trips AS src
            ON tgt.trip_no = src.trip_no AND tgt.line_no = src.line_no
        WHEN MATCHED AND src.has_pending = 0 THEN UPDATE SET
            partial_trip         = src.partial_trip,
            starting_date        = src.starting_date,
            start_time           = src.start_time,
            ending_date          = src.ending_date,
            end_time             = src.end_time,
            start_country        = src.start_country,
            start_city           = src.start_city,
            end_country          = src.end_country,
            end_city             = src.end_city,
            department           = src.department,
            plan_department      = src.plan_department,
            subdepartment        = src.subdepartment,
            vehicle              = src.vehicle,
            trailer              = src.trailer,
            driver               = src.driver,
            driver_2             = src.driver_2,
            status               = src.status,
            plan_info            = src.plan_info,
            plan_info_2          = src.plan_info_2,
            txt_product          = src.txt_product,
            txt_file             = src.txt_file,
            eupl                 = src.eupl,
            additional_resources = src.additional_resources,
            actual_starting_date = src.actual_starting_date,
            actual_ending_date   = src.actual_ending_date,
            nav_updated_at       = ?,
            updated_at           = ?
        WHEN MATCHED AND src.has_pending = 1 THEN UPDATE SET
            partial_trip         = src.partial_trip,
            starting_date        = src.starting_date,
            start_time           = src.start_time,
            ending_date          = src.ending_date,
            end_time             = src.end_time,
            start_country        = src.start_country,
            start_city           = src.start_city,
            end_country          = src.end_country,
            end_city             = src.end_city,
            department           = src.department,
            plan_department      = src.plan_department,
            subdepartment        = src.subdepartment,
            status               = src.status,
            plan_info            = src.plan_info,
            plan_info_2          = src.plan_info_2,
            txt_product          = src.txt_product,
            txt_file             = src.txt_file,
            eupl                 = src.eupl,
            additional_resources = src.additional_resources,
            actual_starting_date = src.actual_starting_date,
            actual_ending_date   = src.actual_ending_date,
            nav_updated_at       = ?,
            updated_at           = ?
        WHEN NOT MATCHED THEN INSERT (
            trip_no, line_no, partial_trip,
            starting_date, start_time, ending_date, end_time,
            start_country, start_city, end_country, end_city,
            department, plan_department, subdepartment,
            vehicle, trailer, driver, driver_2,
            status, plan_info, plan_info_2,
            txt_product, txt_file, eupl,
            additional_resources,
            actual_starting_date, actual_ending_date,
            company, nav_updated_at, updated_at
        ) VALUES (
            src.trip_no, src.line_no, src.partial_trip,
            src.starting_date, src.start_time, src.ending_date, src.end_time,
            src.start_country, src.start_city, src.end_country, src.end_city,
            src.department, src.plan_department, src.subdepartment,
            src.vehicle, src.trailer, src.driver, src.driver_2,
            src.status, src.plan_info, src.plan_info_2,
            src.txt_product, src.txt_file, src.eupl,
            src.additional_resources,
            src.actual_starting_date, src.actual_ending_date,
            ?, ?, ?
        );
    """, (now, now, now, now, company, now, now))

    cursor.execute("DROP TABLE #tmp_trips")
    return len(rows)


def _merge_routes(cursor, routes, now):
    """Bulk upsert routes via temp table + MERGE."""
    if not routes:
        return 0

    cursor.execute("""
        CREATE TABLE #tmp_routes (
            trip_no           NVARCHAR(20),
            line_no           INT,
            sequence_no       INT,
            action_code       NVARCHAR(20),
            address_code      NVARCHAR(20),
            address_name      NVARCHAR(200),
            address           NVARCHAR(200),
            city              NVARCHAR(100),
            country           NVARCHAR(10),
            post_code         NVARCHAR(20),
            starting_date     DATETIME2,
            starting_time     NVARCHAR(10),
            eta_date          DATETIME2,
            eta_time          NVARCHAR(10),
            action_duration   DECIMAL(6,2),
            drive_duration    DECIMAL(6,2),
            decimal_latitude  DECIMAL(12,8),
            decimal_longitude DECIMAL(12,8),
            distance          DECIMAL(10,2),
            status            NVARCHAR(20)
        )
    """)

    rows = []
    for r in routes:
        trip_no = (r.get("Trip_No") or "").strip()
        if not trip_no:
            continue
        rows.append((
            trip_no, r.get("Line_No") or 0, r.get("Sequence_No") or 0,
            r.get("Action_Code"), r.get("Address_Code"), r.get("Address_name"),
            r.get("Address"), r.get("City"), r.get("Country"), r.get("Post_Code"),
            _parse_date(r.get("Starting_Date")), r.get("Starting_Time"),
            _parse_date(r.get("ETA_Date")), r.get("ETA_Time"),
            _parse_decimal(r.get("Action_Duration_h_m")),
            _parse_decimal(r.get("Drive_Duration_h_m")),
            _parse_decimal(r.get("Decimal_Latitude")),
            _parse_decimal(r.get("Decimal_Longitude")),
            _parse_decimal(r.get("Distance")),
            r.get("Status"),
        ))

    cursor.executemany(
        "INSERT INTO #tmp_routes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows
    )

    cursor.execute("""
        MERGE routes AS tgt
        USING #tmp_routes AS src
            ON  tgt.trip_no     = src.trip_no
            AND tgt.line_no     = src.line_no
            AND tgt.sequence_no = src.sequence_no
        WHEN MATCHED THEN UPDATE SET
            action_code       = src.action_code,
            address_code      = src.address_code,
            address_name      = src.address_name,
            address           = src.address,
            city              = src.city,
            country           = src.country,
            post_code         = src.post_code,
            starting_date     = src.starting_date,
            starting_time     = src.starting_time,
            eta_date          = src.eta_date,
            eta_time          = src.eta_time,
            action_duration   = src.action_duration,
            drive_duration    = src.drive_duration,
            decimal_latitude  = src.decimal_latitude,
            decimal_longitude = src.decimal_longitude,
            distance          = src.distance,
            status            = src.status,
            updated_at        = ?
        WHEN NOT MATCHED THEN INSERT (
            trip_no, line_no, sequence_no,
            action_code, address_code, address_name, address,
            city, country, post_code,
            starting_date, starting_time,
            eta_date, eta_time,
            action_duration, drive_duration,
            decimal_latitude, decimal_longitude,
            distance, status, updated_at
        ) VALUES (
            src.trip_no, src.line_no, src.sequence_no,
            src.action_code, src.address_code, src.address_name, src.address,
            src.city, src.country, src.post_code,
            src.starting_date, src.starting_time,
            src.eta_date, src.eta_time,
            src.action_duration, src.drive_duration,
            src.decimal_latitude, src.decimal_longitude,
            src.distance, src.status, ?
        );
    """, (now, now))

    cursor.execute("DROP TABLE #tmp_routes")
    return len(rows)


def _merge_trip_list(cursor, trip_list, now):
    """Bulk upsert trip_list via temp table + MERGE."""
    if not trip_list:
        return 0

    cursor.execute("""
        CREATE TABLE #tmp_trip_list (
            trip_no              NVARCHAR(20),
            partial_trip_line_no INT,
            line_no              INT,
            sequence_no          INT,
            action_code          NVARCHAR(20),
            address_name         NVARCHAR(200),
            city                 NVARCHAR(100),
            file_no              NVARCHAR(50),
            shipment_no          INT,
            quantity             DECIMAL(10,2),
            unit_of_measure      NVARCHAR(20)
        )
    """)

    rows = []
    for tl in trip_list:
        trip_no = (tl.get("Trip_No") or "").strip()
        if not trip_no:
            continue
        rows.append((
            trip_no,
            tl.get("Partial_Trip_Line_No") or 0,
            tl.get("Line_No") or 0,
            tl.get("Sequence_No") or 0,
            tl.get("Action_Code"), tl.get("Address_name"),
            tl.get("City"), tl.get("File_No"),
            tl.get("Shipment_No"),
            _parse_decimal(tl.get("Quantity")),
            tl.get("Unit_of_Measure"),
        ))

    cursor.executemany(
        "INSERT INTO #tmp_trip_list VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        rows
    )

    cursor.execute("""
        MERGE trip_list AS tgt
        USING #tmp_trip_list AS src
            ON  tgt.trip_no              = src.trip_no
            AND tgt.partial_trip_line_no = src.partial_trip_line_no
            AND tgt.line_no              = src.line_no
            AND tgt.sequence_no          = src.sequence_no
        WHEN MATCHED THEN UPDATE SET
            action_code     = src.action_code,
            address_name    = src.address_name,
            city            = src.city,
            file_no         = src.file_no,
            shipment_no     = src.shipment_no,
            quantity        = src.quantity,
            unit_of_measure = src.unit_of_measure,
            updated_at      = ?
        WHEN NOT MATCHED THEN INSERT (
            trip_no, partial_trip_line_no, line_no, sequence_no,
            action_code, address_name, city,
            file_no, shipment_no, quantity, unit_of_measure,
            updated_at
        ) VALUES (
            src.trip_no, src.partial_trip_line_no, src.line_no, src.sequence_no,
            src.action_code, src.address_name, src.city,
            src.file_no, src.shipment_no, src.quantity, src.unit_of_measure,
            ?
        );
    """, (now, now))

    cursor.execute("DROP TABLE #tmp_trip_list")
    return len(rows)


def _merge_add_res(cursor, add_res, now):
    """Bulk upsert trip_additional_resources via temp table + MERGE."""
    if not add_res:
        return 0

    cursor.execute("""
        CREATE TABLE #tmp_add_res (
            line_no       INT,
            trip_no       NVARCHAR(20),
            pt_line_no    INT,
            resource_type NVARCHAR(20),
            resource_no   NVARCHAR(20)
        )
    """)

    rows = []
    for ar in add_res:
        line_no = ar.get("Line_No") or 0
        if not line_no:
            continue
        rows.append((
            line_no,
            (ar.get("Trip_No") or "").strip(),
            ar.get("PT_Line_No") or 0,
            ar.get("Resource_Type"), ar.get("Resource_No"),
        ))

    cursor.executemany("INSERT INTO #tmp_add_res VALUES (?,?,?,?,?)", rows)

    cursor.execute("""
        MERGE trip_additional_resources AS tgt
        USING #tmp_add_res AS src ON tgt.line_no = src.line_no
        WHEN MATCHED THEN UPDATE SET
            trip_no       = src.trip_no,
            pt_line_no    = src.pt_line_no,
            resource_type = src.resource_type,
            resource_no   = src.resource_no,
            updated_at    = ?
        WHEN NOT MATCHED THEN INSERT (
            line_no, trip_no, pt_line_no, resource_type, resource_no, updated_at
        ) VALUES (
            src.line_no, src.trip_no, src.pt_line_no, src.resource_type, src.resource_no, ?
        );
    """, (now, now))

    cursor.execute("DROP TABLE #tmp_add_res")
    return len(rows)


# ------------------------------------------------------------------ #
# Shared helpers
# ------------------------------------------------------------------ #

def _fetch(company, label, fn, default=None):
    """Call fn(), return result or default on error."""
    if default is None:
        default = []
    try:
        return fn()
    except Exception as e:
        logging.warning(f"[{company}] {label} failed — {e}")
        return default


def _parse_date(val):
    """Parse NAV date string '2026-06-22' or '0001-01-01' → datetime or None."""
    if not val:
        return None
    try:
        d = datetime.strptime(str(val)[:10], "%Y-%m-%d")
        if d.year <= 1:
            return None
        return d
    except ValueError:
        return None


def _parse_decimal(val):
    """Parse decimal/float value safely."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
