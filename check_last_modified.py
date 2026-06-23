"""
check_last_modified.py — Probe PartialTripAll for Last_Modified field.

If NAV exposes Last_Modified on PartialTripAll we can replace the date-window
filter with a delta filter (Last_Modified ge {last_sync_at}), dramatically
reducing the data fetched per sync cycle.

Checks:
  1. Fetch a small sample from PartialTripAll (top 5 records, one company)
  2. Print all field names returned — spot Last_Modified or similar
  3. If found: show sample values and confirm usability
  4. Also checks PartialTrip for comparison

Usage:
    python check_last_modified.py              # test NAV (.env)
    python check_last_modified.py prod         # PROD NAV (.env.prod)
    python check_last_modified.py prod "Alex Andersen Ølund"   # specific company
"""
from pathlib import Path
import sys
import dotenv

BASE_DIR = Path(__file__).resolve().parent
args = sys.argv[1:]

env_arg = args[0] if args and args[0] in ("prod", "test") else "test"
rest    = [a for a in args if a not in ("prod", "test")]
override_company = rest[0] if rest else None

env_file = ".env.prod" if env_arg == "prod" else ".env"
dotenv.load_dotenv(BASE_DIR / env_file)

import requests
from datetime import datetime
from nav_trip_client import NavTripClient

SEP  = "=" * 60
SEP2 = "-" * 60

CANDIDATE_FIELDS = [
    "Last_Modified", "Last_Modified_DateTime", "LastModified",
    "SystemModifiedAt", "Modify_Date", "ModifyDate",
    "Updated_At", "Timestamp", "ETag",
]


def section(title):
    print(f"\n{SEP2}")
    print(f"  {title}")
    print(SEP2)


def probe_endpoint(nav, company, endpoint, label):
    encoded = requests.utils.quote(company, safe="")
    url = f"{nav.nav_base}/Company('{encoded}')/{endpoint}"
    try:
        resp = requests.get(
            url,
            headers=nav.headers,
            auth=nav.auth,
            params={"$top": "5"},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        records = data.get("value", [])
    except Exception as e:
        print(f"  ❌  {label}: request failed — {e}")
        return

    if not records:
        print(f"  ⚠   {label}: 0 records returned (empty endpoint for this company/date?).")
        return

    print(f"  ℹ   {label}: {len(records)} sample record(s) returned.")

    all_keys = set()
    for r in records:
        all_keys.update(r.keys())

    # Look for Last_Modified candidates
    found = []
    for field in CANDIDATE_FIELDS:
        if field in all_keys:
            found.append(field)

    # Also do a case-insensitive scan for anything with "modif" or "stamp" or "updated"
    fuzzy = [k for k in all_keys
             if any(s in k.lower() for s in ("modif", "stamp", "updated", "changed", "sync"))]
    fuzzy = [f for f in fuzzy if f not in found]

    if found:
        print(f"\n  ✅  Found candidate field(s): {', '.join(found)}")
        for field in found:
            vals = [str(r.get(field)) for r in records if r.get(field) is not None]
            print(f"     {field}: {', '.join(vals[:3])}")
    else:
        print(f"\n  ❌  No Last_Modified / timestamp field found on {label}.")

    if fuzzy:
        print(f"\n  ℹ   Other potentially relevant fields: {', '.join(fuzzy)}")
        for field in fuzzy:
            vals = [str(r.get(field)) for r in records if r.get(field) is not None]
            print(f"     {field}: {', '.join(vals[:3])}")

    # Print all field names for reference
    print(f"\n  All fields on {label} ({len(all_keys)} total):")
    for k in sorted(all_keys):
        sample_val = records[0].get(k)
        print(f"     {k:<45} {str(sample_val)[:60]}")


def run():
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{SEP}")
    print(f"  Last_Modified Probe [{env_arg.upper()}]  —  {ts}")
    print(SEP)

    nav = NavTripClient()

    # Pick company
    if override_company:
        company = override_company
    else:
        companies = nav.get_companies()
        if not companies:
            print("  ❌  No companies returned from NAV.")
            sys.exit(1)
        company = companies[0]

    print(f"\n  Company: {company}")

    section("1. PartialTripAll")
    probe_endpoint(nav, company, "PartialTripAll", "PartialTripAll")

    section("2. PartialTrip (for comparison)")
    probe_endpoint(nav, company, "PartialTrip", "PartialTrip")

    section("Conclusion")
    print("  If Last_Modified (or equivalent) was found:")
    print("    → Delta-sync is feasible: replace date-window filter with")
    print("      Last_Modified ge {last_sync_at} in sync_trips_down.py")
    print("    → Store last_sync_at per company in DB or App Settings.")
    print()
    print("  If NOT found:")
    print("    → Keep date-window filter as-is.")
    print("    → Queue-based parallelism ensures acceptable sync times.")

    print(f"\n{SEP}")
    print(f"  Probe complete — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{SEP}\n")


if __name__ == "__main__":
    run()
