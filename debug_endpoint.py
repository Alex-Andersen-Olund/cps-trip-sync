import dotenv, requests
from requests_ntlm import HttpNtlmAuth
from pathlib import Path

dotenv.load_dotenv(Path(__file__).parent / ".env")

auth    = HttpNtlmAuth("admin\\Standindriver", "Driver2022")
headers = {"Accept": "application/json", "OData-Version": "4.0"}
base    = "http://navbatchsrvtest.admin.alex-andersen.dk:7048/DynamicsNAV100Test/ODataV4"

# Test 1 — endpoint exists + top 5 rows (no date filter)
url = f"{base}/Company('Alex%20Andersen%20%C3%98lund')/PartialTripAll?$top=5"
r = requests.get(url, auth=auth, headers=headers, timeout=30)
print(f"Status: {r.status_code}")
print(r.text[:800])

# Test 2 — wider date window (today-10 to today+7) to catch older TEST data
from datetime import date, timedelta
from_date = date.today() - timedelta(days=10)
to_date   = date.today() + timedelta(days=7)
url2 = (
    f"{base}/Company('Alex%20Andersen%20%C3%98lund')/PartialTripAll"
    f"?$filter=Starting_Date ge {from_date.isoformat()}T00:00:00Z"
    f" and Starting_Date le {to_date.isoformat()}T23:59:59Z&$top=5"
)
r2 = requests.get(url2, auth=auth, headers=headers, timeout=30)
print(f"\nWider window ({from_date} → {to_date}) — Status: {r2.status_code}")
import json
data = r2.json()
print(f"Trips returned: {len(data.get('value', []))}")
if data.get("value"):
    first = data["value"][0]
    print(f"First trip: {first.get('Trip_No')} Starting_Date={first.get('Starting_Date')}")
