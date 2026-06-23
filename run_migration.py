"""
run_migration.py — Run a SQL migration file against Azure SQL.

Usage:
    python run_migration.py v1          # test environment (.env)
    python run_migration.py v1 prod     # production environment (.env.prod)
"""
from pathlib import Path
import sys
import dotenv

BASE_DIR = Path(__file__).resolve().parent

if len(sys.argv) < 2:
    print("Usage: python run_migration.py <version> [prod]")
    print("  e.g. python run_migration.py v1")
    print("  e.g. python run_migration.py v1 prod")
    sys.exit(1)

version  = sys.argv[1]
env      = sys.argv[2] if len(sys.argv) > 2 else "test"
env_file = ".env.prod" if env == "prod" else ".env"
dotenv.load_dotenv(BASE_DIR / env_file)

from db_client import get_connection

sql_file = BASE_DIR / "migrations" / f"{version}_trip_schema.sql"
if not sql_file.exists():
    print(f"ERROR: Migration file not found: {sql_file}")
    sys.exit(1)

sql = sql_file.read_text(encoding="utf-8")

print(f"Running migration {version} on [{env.upper()}]...")
print(f"File: {sql_file}")

with get_connection() as conn:
    cursor = conn.cursor()
    batches = [b.strip() for b in sql.split("\nGO") if b.strip()]
    for batch in batches:
        if batch:
            cursor.execute(batch)

print(f"Migration {version} complete.")
