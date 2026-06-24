"""
nav_trip_client.py — NAV OData client for trip endpoints.

Covers:
  - PartialTripAll  (read — primary trip list)
  - PartialTrip     (read supplement Plan_Info_2 + PATCH resource assignments)
  - Route           (read — route stops)
  - TripList        (read — shipment links)
  - PartialTripWS_PT_additional_resource  (read + POST Trailer2/Dolly)
"""
import os
import json
import requests
from requests_ntlm import HttpNtlmAuth
from datetime import date, timedelta

# Max trip_nos per OData $filter batch — keeps URLs under ~4000 chars
_BATCH_SIZE = 100


class NavTripClient:
    def __init__(self):
        self.username = os.getenv("NAV_USERNAME")
        self.password = os.getenv("NAV_PASS")
        self.domain   = os.getenv("NAV_DOMAIN", "admin")
        self.nav_base = os.getenv("NAV_BASE")  # e.g. http://navbatchsrv:7348/WSNTLM/ODataV4

        if not self.username or not self.password or not self.nav_base:
            raise RuntimeError("NAV_USERNAME, NAV_PASS and NAV_BASE must be set")

        self.auth = HttpNtlmAuth(f"{self.domain}\\{self.username}", self.password)
        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "OData-Version": "4.0",
        }

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _company_url(self, company_name: str) -> str:
        encoded = requests.utils.quote(company_name, safe="")
        return f"{self.nav_base}/Company('{encoded}')"

    def _get_all(self, url: str, params: dict = None, timeout: int = 120) -> list:
        """Paginated GET — follows @odata.nextLink."""
        results = []
        while url:
            resp = requests.get(url, headers=self.headers, auth=self.auth,
                                params=params, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            results.extend(data.get("value", []))
            url = data.get("@odata.nextLink")
            params = None
        return results

    def _batch_filter(self, field: str, values: list) -> list[str]:
        """Split values into OData $filter strings of at most _BATCH_SIZE items.
        Returns list of filter strings, e.g. ["Trip_No eq 'T001' or Trip_No eq 'T002'", ...]
        """
        filters = []
        for i in range(0, len(values), _BATCH_SIZE):
            chunk = values[i:i + _BATCH_SIZE]
            clause = " or ".join(f"{field} eq '{v}'" for v in chunk)
            filters.append(clause)
        return filters

    # ------------------------------------------------------------------ #
    # Companies
    # ------------------------------------------------------------------ #

    def get_companies(self) -> list[str]:
        """Return company list from NAV_COMPANIES env var or NAV API."""
        override = os.getenv("NAV_COMPANIES", "").strip()
        if override:
            return [c.strip() for c in override.split(",") if c.strip()]
        url = f"{self.nav_base}/Company"
        resp = requests.get(url, headers=self.headers, auth=self.auth, timeout=30)
        resp.raise_for_status()
        companies = resp.json().get("value", [])
        result = []
        for c in companies:
            name = c.get("name") or c.get("Name") or c.get("displayName") or c.get("id")
            if name:
                result.append(name)
        return result

    # ------------------------------------------------------------------ #
    # PartialTripAll — primary trip read
    # ------------------------------------------------------------------ #

    def get_partial_trips(self, company: str,
                          from_date: date = None,
                          to_date: date = None,
                          use_status_filter: bool = False) -> list:
        """Fetch all PartialTrips for a company.

        use_status_filter=True (preferred):
            Status ne '30' and Starting_Date ge today-7
            Captures all open trips regardless of planned start date,
            with a 7-day lookback as a safety net against stale status.

        use_status_filter=False (date window fallback):
            Starting_Date ge from_date and le to_date
            Default window: today-3 to today+7.

        Returns raw NAV dicts.
        """
        url = f"{self._company_url(company)}/PartialTripAll"

        if use_status_filter:
            cutoff = (date.today() - timedelta(days=7)).isoformat()
            # Status is Edm.String in NAV OData — use ne '30' (not delivered),
            # combined with date cutoff as safety net against old stale trips.
            params = {
                "$filter": f"Status ne '30' and Starting_Date ge {cutoff}T00:00:00Z"
            }
        else:
            if from_date is None:
                from_date = date.today() - timedelta(days=3)
            if to_date is None:
                to_date = date.today() + timedelta(days=3)
            params = {
                "$filter": (
                    f"Starting_Date ge {from_date.isoformat()}T00:00:00Z "
                    f"and Starting_Date le {to_date.isoformat()}T23:59:59Z"
                )
            }

        return self._get_all(url, params)

    # ------------------------------------------------------------------ #
    # Route
    # ------------------------------------------------------------------ #

    def get_routes(self, company: str, trip_nos: list[str]) -> list:
        """Fetch Route records for a list of trip_nos (batched)."""
        if not trip_nos:
            return []
        results = []
        for f in self._batch_filter("Trip_No", trip_nos):
            url = f"{self._company_url(company)}/Route"
            results.extend(self._get_all(url, {"$filter": f}, timeout=300))
        return results

    # ------------------------------------------------------------------ #
    # TripList
    # ------------------------------------------------------------------ #

    def get_trip_list(self, company: str, trip_nos: list[str]) -> list:
        """Fetch TripList records for a list of trip_nos (batched)."""
        if not trip_nos:
            return []
        results = []
        for f in self._batch_filter("Trip_No", trip_nos):
            url = f"{self._company_url(company)}/TripList"
            results.extend(self._get_all(url, {"$filter": f}, timeout=300))
        return results

    # ------------------------------------------------------------------ #
    # PartialTrip — Plan_Info_2 supplement
    # ------------------------------------------------------------------ #

    def get_plan_info_2(self, company: str, trip_nos: list[str]) -> dict:
        """Fetch Plan_Info_2 from PartialTrip for a list of trip_nos.

        Returns dict keyed by (trip_no, line_no) → plan_info_2 string.
        """
        if not trip_nos:
            return {}
        results = {}
        for f in self._batch_filter("Trip_No", trip_nos):
            url = f"{self._company_url(company)}/PartialTrip"
            rows = self._get_all(url, {"$filter": f, "$select": "Trip_No,Line_No,Plan_Info_2"})
            for r in rows:
                key = (r.get("Trip_No", ""), r.get("Line_No", 0))
                results[key] = r.get("Plan_Info_2", "")
        return results

    # ------------------------------------------------------------------ #
    # AdditionalResources (Trailer2, Dolly)
    # ------------------------------------------------------------------ #

    def get_additional_resources(self, company: str, trip_no: str, pt_line_no: int) -> list:
        """Fetch PartialTripWS_PT_additional_resource for a single (trip_no, pt_line_no).

        NAV requires both Trip_No and PT_Line_No in the filter — passing only
        Trip_No returns a 400 error from the server.
        """
        url = f"{self._company_url(company)}/PartialTripWS_PT_additional_resource"
        f = f"Trip_No eq '{trip_no}' and PT_Line_No eq {pt_line_no}"
        return self._get_all(url, {"$filter": f})

    # ------------------------------------------------------------------ #
    # PATCH PartialTrip — write resource assignments back to NAV
    # ------------------------------------------------------------------ #

    def patch_partial_trip(self, company: str, trip_no: str,
                           line_no: int, fields: dict) -> None:
        """PATCH resource assignment fields on a PartialTrip in NAV.

        fields: dict with any subset of Vehicle, Trailer, Driver, Driver_2,
                Starting_Date, Start_Time.
        Raises requests.HTTPError on failure.
        """
        encoded_company = requests.utils.quote(company, safe="")
        encoded_trip    = requests.utils.quote(trip_no, safe="")
        url = (
            f"{self.nav_base}/Company('{encoded_company}')"
            f"/PartialTrip(Trip_No='{encoded_trip}',Line_No={line_no})"
        )
        patch_headers = {**self.headers, "If-Match": "*"}
        resp = requests.patch(url, headers=patch_headers, auth=self.auth,
                              data=json.dumps(fields), timeout=30)
        resp.raise_for_status()

    # ------------------------------------------------------------------ #
    # POST AdditionalResource — create or remove Trailer2/Dolly
    # ------------------------------------------------------------------ #

    def post_additional_resource(self, company: str, payload: dict) -> None:
        """POST to PartialTripWS_PT_additional_resource.

        To remove: include Delete=true in payload.
        Raises requests.HTTPError on failure.
        """
        url = f"{self._company_url(company)}/PartialTripWS_PT_additional_resource"
        resp = requests.post(url, headers=self.headers, auth=self.auth,
                             data=json.dumps(payload), timeout=30)
        resp.raise_for_status()
