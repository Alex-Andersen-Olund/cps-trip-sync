"""
clear_trips.py — Truncate all trip tables in Azure SQL.

Usage:
    python clear_trips.py          # test environment (.env)
    python clear_trips.py prod     # production environment (.env.prod)
"""
from pathlib import Path
import sys
import dotenv

BASE_DIR = Path(__file__).resolve().parent
env      = sys.argv[1] if len(sys.argv) > 1 else "test"
env_file = ".env.prod" if env == "prod" else ".env"
dotenv.load_dotenv(BASE_DIR / env_file)

from db_client import get_connection

tables = [
    "trips_pending_sync",
    "trip_additional_resources",
    "trip_list",
    "routes",
    "trips",
]

print(f"Clearing all trip tables on [{env.upper()}]...")
with get_connection() as conn:
    cursor = conn.cursor()
    for table in tables:
        cursor.execute(f"DELETE FROM {table}")
        print(f"  {table} — cleared")

print("Done.")
