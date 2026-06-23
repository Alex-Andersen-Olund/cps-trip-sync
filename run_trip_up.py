"""
run_trip_up.py — CLI entry point for TripData_Up (Azure SQL → NAV).

Usage:
    python run_trip_up.py          # test environment (.env)
    python run_trip_up.py prod     # production environment (.env.prod)
"""
from pathlib import Path
import sys
import dotenv
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent
env      = sys.argv[1] if len(sys.argv) > 1 else "test"
env_file = ".env.prod" if env == "prod" else ".env"
dotenv.load_dotenv(BASE_DIR / env_file)

from sync_trips_up import sync_trips_up


def run():
    sep = "=" * 50
    ts  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{sep}")
    print(f"CPS TripData_Up [{env.upper()}] -- {ts}")
    print(sep)

    try:
        sync_trips_up()
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(sep)
        print(f"TripData_Up complete -- {ts}")
        print(sep + "\n")
        return 0
    except Exception as e:
        print(f"ERROR: {e}")
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(sep)
        print(f"TripData_Up FAILED -- {ts}")
        print(sep + "\n")
        return 1


if __name__ == "__main__":
    sys.exit(run())
