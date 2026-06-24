"""
sync_trips_up.py — Azure SQL → NAV trip sync (TripData_Up).

Processes the trips_pending_sync queue and PATCHes changes back to NAV.
Handles Trailer2/Dolly via PartialTripWS_PT_additional_resource POST.

Retry logic:
  attempts=0 → process immediately
  attempts=1 → retry after 2 min (checked on next timer run)
  attempts=2 → retry after 5 min
  attempts >= 3 → mark as 'failed', log error
"""
import json
import logging
from datetime import datetime, timezone, timedelta
from db_client import get_connection
from nav_trip_client import NavTripClient


def sync_trips_up():
    nav = NavTripClient()
    now = datetime.now(timezone.utc)

    processed = failed = skipped = 0

    with get_connection() as conn:
        cursor = conn.cursor()

        # Fetch pending rows — respect retry backoff
        cursor.execute("""
            SELECT id, trip_no, line_no, changed_fields, attempts,
                   last_attempt_at, company
            FROM trips_pending_sync
            WHERE status = 'pending'
            ORDER BY created_at ASC
        """)
        rows = cursor.fetchall()

    for row in rows:
        pending_id, trip_no, line_no, changed_fields_json, attempts, last_attempt_at, company = row

        # ---- retry backoff ---- #
        if attempts > 0 and last_attempt_at:
            backoff = timedelta(minutes=2 if attempts == 1 else 5)
            if now - last_attempt_at.replace(tzinfo=timezone.utc) < backoff:
                skipped += 1
                continue

        # ---- parse changed fields ---- #
        try:
            fields = json.loads(changed_fields_json or "{}")
        except (json.JSONDecodeError, TypeError):
            logging.error(f"[up] pending_id={pending_id}: invalid changed_fields JSON — marking failed")
            _mark(pending_id, "failed", attempts, now)
            failed += 1
            continue

        if not fields:
            _mark(pending_id, "done", attempts, now)
            continue

        # ---- separate main fields from additional resources ---- #
        trip_fields = {k: v for k, v in fields.items()
                       if k not in ("AdditionalResources",)}
        add_res     = fields.get("AdditionalResources")  # list of dicts or None

        # ---- resolve company if not stored ---- #
        resolved_company = company or _lookup_company(trip_no, line_no)
        if not resolved_company:
            logging.warning(f"[up] pending_id={pending_id}: cannot resolve company for "
                            f"trip {trip_no}/{line_no} — will retry")
            _bump_attempts(pending_id, attempts, now)
            continue

        # ---- PATCH PartialTrip ---- #
        if trip_fields:
            try:
                nav.patch_partial_trip(resolved_company, trip_no, line_no, trip_fields)
            except Exception as e:
                logging.error(f"[up] pending_id={pending_id}: PATCH failed — {e}")
                if attempts + 1 >= 3:
                    _mark(pending_id, "failed", attempts + 1, now)
                    failed += 1
                else:
                    _bump_attempts(pending_id, attempts, now)
                continue

        # ---- POST AdditionalResources ---- #
        if add_res:
            ar_ok = _sync_additional_resources(nav, resolved_company, trip_no, line_no,
                                               add_res, pending_id, attempts, now)
            if not ar_ok:
                failed += 1
                continue

        # ---- success ---- #
        _mark(pending_id, "done", attempts, now, synced_up=True, trip_no=trip_no, line_no=line_no)
        processed += 1

    logging.info(f"[sync_trips_up] Done — processed={processed} failed={failed} skipped(backoff)={skipped}")


# ------------------------------------------------------------------ #
# Additional resources sync
# ------------------------------------------------------------------ #

def _sync_additional_resources(nav, company, trip_no, line_no,
                                add_res_list, pending_id, attempts, now) -> bool:
    """POST create/delete additional resources. Returns True on success."""
    for ar in add_res_list:
        try:
            payload = {
                "Trip_No": trip_no,
                "PT_Line_No": line_no,
                "Resource_Type": ar.get("Resource_Type"),
                "Resource_No": ar.get("Resource_No"),
            }
            if ar.get("Delete"):
                payload["Delete"] = True
            nav.post_additional_resource(company, payload)
        except Exception as e:
            logging.error(f"[up] pending_id={pending_id}: AdditionalResource POST failed — {e}")
            if attempts + 1 >= 3:
                _mark(pending_id, "failed", attempts + 1, now)
            else:
                _bump_attempts(pending_id, attempts, now)
            return False
    return True


# ------------------------------------------------------------------ #
# DB helpers
# ------------------------------------------------------------------ #

def _mark(pending_id, status, attempts, now, synced_up=False,
          trip_no=None, line_no=None):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE trips_pending_sync
            SET status = ?, attempts = ?, last_attempt_at = ?
            WHERE id = ?
        """, (status, attempts, now, pending_id))

        if synced_up and trip_no is not None and line_no is not None:
            cursor.execute("""
                UPDATE trips SET synced_up_at = ?
                WHERE trip_no = ? AND line_no = ?
            """, (now, trip_no, line_no))


def _bump_attempts(pending_id, attempts, now):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE trips_pending_sync
            SET attempts = ?, last_attempt_at = ?
            WHERE id = ?
        """, (attempts + 1, now, pending_id))


def _lookup_company(trip_no, line_no) -> str | None:
    """Look up which company a trip belongs to from the trips table."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT company FROM trips WHERE trip_no = ? AND line_no = ?",
            (trip_no, line_no)
        )
        row = cursor.fetchone()
        return row[0] if row else None
