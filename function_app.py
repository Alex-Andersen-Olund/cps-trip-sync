"""
function_app.py — Azure Functions v2 entry point for cps-trip-sync.

Functions:
  trip_down_fast_timer  — enqueues one message per company (narrow window, every 2 min)
  trip_down_fast_queue  — processes one company from fast queue
  trip_down_full_timer  — enqueues one message per company (full window, every 30 min)
  trip_down_full_queue  — processes one company from full queue
  trip_up_timer         — Azure SQL → NAV queue processor, every 2 min
  trip_up_http          — force-trigger POST /api/trip/up

  trip_down_timer       — LEGACY: single-run all companies, kept until queue triggers are verified
  trip_down_http        — force-trigger POST /api/trip/down (legacy)

Environment variables (Function App config / local.settings.json):
  SQL_SERVER, SQL_DATABASE       — Azure SQL connection
  NAV_BASE                       — NAV OData base URL
  NAV_USERNAME, NAV_PASS         — NTLM credentials
  NAV_DOMAIN                     — NTLM domain (default: admin)
  NAV_COMPANIES                  — comma-separated company names
  AzureWebJobsStorage            — Storage Account connection string (for queues)
"""
import json
import typing
import logging
from datetime import date, timedelta

import azure.functions as func

from sync_trips_down import sync_trips_down, sync_company_down
from sync_trips_up   import sync_trips_up
from nav_trip_client import NavTripClient

app = func.FunctionApp()

_FAST_QUEUE = "trip-sync-fast-queue"
_FULL_QUEUE = "trip-sync-full-queue"


# ------------------------------------------------------------------ #
# TripData_Down — queue-based (fast, per-company)
# ------------------------------------------------------------------ #

@app.timer_trigger(
    schedule="0 */2 * * * *",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=False
)
@app.queue_output(
    arg_name="outputQueue",
    queue_name=_FAST_QUEUE,
    connection="AzureWebJobsStorage"
)
def trip_down_fast_timer(timer: func.TimerRequest,
                         outputQueue: func.Out[typing.List[str]]) -> None:
    """Every 2 min: enqueue one message per company for the narrow operational window."""
    if timer.past_due:
        logging.warning("trip_down_fast_timer: past due")

    today = date.today()
    from_date = today - timedelta(days=1)
    to_date   = today + timedelta(days=2)

    nav = NavTripClient()
    companies = nav.get_companies()

    messages = [
        json.dumps({"company": c, "from_date": from_date.isoformat(), "to_date": to_date.isoformat()})
        for c in companies
    ]
    outputQueue.set(messages)
    logging.info(f"trip_down_fast_timer: enqueued {len(messages)} company messages")


@app.queue_trigger(
    arg_name="msg",
    queue_name=_FAST_QUEUE,
    connection="AzureWebJobsStorage"
)
def trip_down_fast_queue(msg: func.QueueMessage) -> None:
    """Process one company from the fast queue — runs in parallel per company."""
    payload   = json.loads(msg.get_body().decode())
    company   = payload["company"]
    from_date = date.fromisoformat(payload["from_date"])
    to_date   = date.fromisoformat(payload["to_date"])

    logging.info(f"trip_down_fast_queue: [{company}] {from_date} → {to_date}")
    sync_company_down(company, from_date, to_date)


# ------------------------------------------------------------------ #
# TripData_Down — queue-based (full, per-company)
# ------------------------------------------------------------------ #

@app.timer_trigger(
    schedule="0 */30 * * * *",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=False
)
@app.queue_output(
    arg_name="outputQueue",
    queue_name=_FULL_QUEUE,
    connection="AzureWebJobsStorage"
)
def trip_down_full_timer(timer: func.TimerRequest,
                         outputQueue: func.Out[typing.List[str]]) -> None:
    """Every 30 min: enqueue one message per company for the full planning window."""
    if timer.past_due:
        logging.warning("trip_down_full_timer: past due")

    today = date.today()
    from_date = today - timedelta(days=3)
    to_date   = today + timedelta(days=10)

    nav = NavTripClient()
    companies = nav.get_companies()

    messages = [
        json.dumps({"company": c, "from_date": from_date.isoformat(), "to_date": to_date.isoformat()})
        for c in companies
    ]
    outputQueue.set(messages)
    logging.info(f"trip_down_full_timer: enqueued {len(messages)} company messages")


@app.queue_trigger(
    arg_name="msg",
    queue_name=_FULL_QUEUE,
    connection="AzureWebJobsStorage"
)
def trip_down_full_queue(msg: func.QueueMessage) -> None:
    """Process one company from the full queue — runs in parallel per company."""
    payload   = json.loads(msg.get_body().decode())
    company   = payload["company"]
    from_date = date.fromisoformat(payload["from_date"])
    to_date   = date.fromisoformat(payload["to_date"])

    logging.info(f"trip_down_full_queue: [{company}] {from_date} → {to_date}")
    sync_company_down(company, from_date, to_date)


# ------------------------------------------------------------------ #
# TripData_Down — LEGACY (single run, all companies)
# Keep until queue triggers are deployed and verified on PROD.
# ------------------------------------------------------------------ #

@app.timer_trigger(
    schedule="0 */5 * * * *",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=False
)
def trip_down_timer(timer: func.TimerRequest) -> None:
    """LEGACY — 5-min sync of all companies in sequence. Disable once queue triggers verified."""
    if timer.past_due:
        logging.warning("trip_down_timer: past due")
    logging.info("trip_down_timer: starting (legacy)")
    sync_trips_down()
    logging.info("trip_down_timer: done")


@app.route(
    route="trip/down",
    auth_level=func.AuthLevel.FUNCTION,
    methods=["POST"]
)
def trip_down_http(req: func.HttpRequest) -> func.HttpResponse:
    """Force-trigger TripData_Down sync (legacy — all companies, full window)."""
    logging.info("trip_down_http: triggered")
    try:
        sync_trips_down()
        return func.HttpResponse("TripData_Down sync completed", status_code=200)
    except Exception as e:
        logging.exception("trip_down_http: sync failed")
        return func.HttpResponse(f"TripData_Down sync failed: {e}", status_code=500)


# ------------------------------------------------------------------ #
# TripData_Up — Azure SQL → NAV
# ------------------------------------------------------------------ #

@app.timer_trigger(
    schedule="0 */2 * * * *",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=False
)
def trip_up_timer(timer: func.TimerRequest) -> None:
    """2-minute fallback: push any pending CPS changes to NAV."""
    if timer.past_due:
        logging.warning("trip_up_timer: past due")
    logging.info("trip_up_timer: starting")
    sync_trips_up()
    logging.info("trip_up_timer: done")


@app.route(
    route="trip/up",
    auth_level=func.AuthLevel.FUNCTION,
    methods=["POST"]
)
def trip_up_http(req: func.HttpRequest) -> func.HttpResponse:
    """HTTP trigger for TripData_Up — called by CPS fire-and-forget after a DB write."""
    logging.info("trip_up_http: triggered")
    try:
        sync_trips_up()
        return func.HttpResponse("TripData_Up sync completed", status_code=200)
    except Exception as e:
        logging.exception("trip_up_http: sync failed")
        return func.HttpResponse(f"TripData_Up sync failed: {e}", status_code=500)
