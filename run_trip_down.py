"""
run_trip_down.py — CLI entry point for TripData_Down (NAV → Azure SQL).

Usage:
    python run_trip_down.py          # test environment (.env)
    python run_trip_down.py prod     # production environment (.env.prod)
"""
from pathlib import Path
import sys
import dotenv
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent
env      = sys.argv[1] if len(sys.argv) > 1 else "test"
env_file = ".env.prod" if env == "prod" else ".env"
dotenv.load_dotenv(BASE_DIR / env_file)

from sync_trips_down import sync_trips_down


# Pass --trips-only to skip routes/trip_list/plan_info_2/add_res
trips_only = "--trips-only" in sys.argv


def run():
    sep = "=" * 50
    ts  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{sep}")
    print(f"CPS TripData_Down [{env.upper()}]{' [TRIPS ONLY]' if trips_only else ''} -- {ts}")
    print(sep)

    try:
        sync_trips_down(sync_supplements=not trips_only)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(sep)
        print(f"TripData_Down complete -- {ts}")
        print(sep + "\n")
        return 0
    except Exception as e:
        print(f"ERROR: {e}")
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(sep)
        print(f"TripData_Down FAILED -- {ts}")
        print(sep + "\n")
        return 1


if __name__ == "__main__":
    sys.exit(run())
