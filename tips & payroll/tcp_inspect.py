"""Read TCP catalogs and optional period records; never output credentials."""

import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler


class InspectionError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def error_detail(exc):
    """Return only recognized error labels, never arbitrary response text."""
    content = exc.read(8192)
    try:
        payload = json.loads(content)
    except (ValueError, UnicodeError):
        if b"request blocked" in content.lower():
            return "request blocked by gateway"
        return "non-JSON error response"
    if not isinstance(payload, dict):
        return "unrecognized error response"
    recognized = {
        "invalid_client", "invalid_scope", "invalid_request", "unauthorized_client",
        "unsupported_grant_type", "access_denied", "Forbidden",
        "Missing Authentication Token", "Unauthorized", "Too Many Requests",
    }
    return next((payload[key] for key in ("error", "message") if isinstance(payload.get(key), str) and payload[key] in recognized), "unrecognized error response")


def select(record, fields):
    return {key: record[key] for key in fields.split() if key in record}


def project(kind, record):
    fields = {
        "locations": "id name active",
        "jobcodes": "id jobCodeId description jobClass jobCodeGroup locationName active leaveCode forceUnpaid askTracked1 askTracked2 askTracked3",
        "costcodes": "id fullName description isActive",
        "customfields": "id description category fieldNumber moduleType moduleNumber subModuleNumber",
        "employees": "employeeId employeeRecordId employeeName firstName lastName hireDate department location defaultJobCode defaultCostCode roleId isSuspended terminationDate",
        "employeejobcodes": "id employeeId jobCodeId activePeriodStartDate activePeriodStopDate isActive isDefault description location",
        "worksegments": "id employeeId employeeRecordId jobCodeId costCode laborCodes timeIn timeOut tracked1 tracked2 tracked3 employeeDefaultLocationId customFields missedInPunch missedOutPunch",
    }
    if kind == "hours":
        result = select(record, "period info")
        result["employee"] = project("employees", record.get("employee") or {})
        result["segments"] = []
        for segment in record.get("segments") or []:
            item = select(segment, "recordId timeIn timeOut hours isLeave comp clockedInStatus")
            item["jobCode"] = project("jobcodes", segment.get("jobCode") or {})
            item["costCode"] = project("costcodes", segment.get("costCode") or {})
            result["segments"].append(item)
        return result
    return select(record, fields[kind])


class TCP:
    def __init__(self):
        self.opener = build_opener(NoRedirect)
        self.headers = {}
        self.last_request = 0
        self.requests = 0

    def request(self, url, data=None):
        if self.requests >= 150:
            raise InspectionError("Request safety limit reached; results are incomplete.")
        for attempt in range(3):
            time.sleep(max(0, 1.1 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            self.requests += 1
            headers = {"Accept": "application/json", "User-Agent": "LC-Automation/1.0 (TCP payroll reporting)"}
            if data is None:
                headers.update(self.headers)
            else:
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            req = Request(url, data=data, headers=headers)
            try:
                with self.opener.open(req, timeout=60) as response:
                    return json.load(response)
            except HTTPError as exc:
                # Do not print response bodies: they can echo credentials or records.
                if exc.code in (429, 502, 503, 504) and attempt < 2:
                    time.sleep(10 * (attempt + 1))
                    continue
                raise InspectionError(f"TCP returned HTTP {exc.code}: {error_detail(exc)}.") from None
            except (URLError, TimeoutError, ValueError):
                raise InspectionError("TCP connection or JSON response failed.") from None

    def authenticate(self):
        missing = [key for key in ("TCP_CLIENT_ID", "TCP_CLIENT_SECRET", "TCP_API_KEY") if not os.environ.get(key, "").strip()]
        if missing:
            raise InspectionError("Missing configuration: " + ", ".join(missing))
        body = urlencode({
            "grant_type": "client_credentials",
            "scope": "tcp-openapi/tcpopenapi.read",
            "client_id": os.environ["TCP_CLIENT_ID"].strip(),
            "client_secret": os.environ["TCP_CLIENT_SECRET"].strip(),
        }).encode()
        result = self.request("https://auth.api.tcplusondemand.com/oauth2/token", body)
        token = result.get("access_token")
        if not isinstance(token, str) or not token:
            raise InspectionError("TCP did not return an access token.")
        self.headers = {
            "Authorization": "Bearer " + token,
            "x-api-key": os.environ["TCP_API_KEY"].strip(),
        }
        company = os.environ.get("TCP_COMPANY_ID", "").strip()
        if company:
            self.headers["X-Tcp-CompanyId"] = company

    def collect(self, kind, params=None):
        records = []
        fingerprints = set()
        for page in range(1, 101):
            query = {**(params or {}), "perPage": 250, "page": page}
            response = self.request("https://api.tcplusondemand.com/v1/" + kind + "?" + urlencode(query))
            if response.get("errors"):
                raise InspectionError("TCP returned application errors; collection is incomplete.")
            rows = response.get("data")
            if rows is None:
                rows = []
            if not isinstance(rows, list):
                raise InspectionError("Unexpected TCP response data structure.")
            if not rows:
                return records
            fingerprint = json.dumps(rows, sort_keys=True)
            if fingerprint in fingerprints:
                raise InspectionError("TCP repeated a page; collection is incomplete.")
            fingerprints.add(fingerprint)
            records.extend(project(kind, row) for row in rows)
            # Continue to an empty page: do not assume the server honors page size.
        raise InspectionError("Page limit reached; collection is incomplete.")


def period():
    start = os.environ.get("TCP_PAY_PERIOD_START", "").strip()
    end = os.environ.get("TCP_PAY_PERIOD_END", "").strip()
    if not start and not end:
        return None
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        raise InspectionError("Supply both period dates as YYYY-MM-DD.") from None
    if last < first or (last - first).days >= 90:
        raise InspectionError("Period must contain between 1 and 90 days inclusive.")
    return {"startDate": first.isoformat(), "stopDate": last.isoformat()}


def main():
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "datasets": {}, "errors": {}}
    client = TCP()
    try:
        dates = period()
        report["period"] = dates
        client.authenticate()
        report["authenticated"] = True
        queries = {"locations": {}, "jobcodes": {}, "costcodes": {}, "customfields": {}, "employees": {"includeRoles": "true"}}
        if dates:
            # Read a one-day buffer, then select shifts by start date locally.
            # This avoids losing overnight shifts at the reporting boundary.
            hours_stop = (date.fromisoformat(dates["stopDate"]) + timedelta(days=1)).isoformat()
            report["hours_query_stop"] = hours_stop
            report["date_basis"] = "Shifts beginning within the inclusive requested dates; all hours in those shifts. API period totals cover the buffered query, so report totals must be calculated from the filtered segments."
            queries["hours"] = {**dates, "stopDate": hours_stop, "includeJobCode": "true", "includeCostCode": "true", "includeRate": "false", "includeOverlapping": "false"}
            queries["worksegments"] = {"startDate": dates["startDate"] + "T00:00:00", "stopDate": dates["stopDate"] + "T23:59:59", "includeSegmentTime": "timeIn", "includeEmployeeDefaultLocation": "true", "sortByWorkSegmentId": "true"}
        for kind, params in queries.items():
            try:
                records = client.collect(kind, params)
                if dates and kind in ("hours", "worksegments"):
                    def in_period(row):
                        return dates["startDate"] <= (row.get("timeIn") or "")[:10] <= dates["stopDate"]
                    if kind == "hours":
                        for row in records:
                            row["segments"] = [segment for segment in row["segments"] if in_period(segment)]
                    else:
                        records = [row for row in records if in_period(row)]
                report["datasets"][kind] = {"complete": True, "count": len(records), "records": records}
                print(f"{kind}: {len(records)} records retrieved.")
            except InspectionError as exc:
                report["errors"][kind] = str(exc)
                print(f"{kind}: failed; see private report.")
        # A bounded prior-period sample checks tip availability. It is never
        # included in the selected period totals.
        tip_jobs = [str(row["jobCodeId"]) for row in report["datasets"].get("jobcodes", {}).get("records", []) if row.get("description", "").strip().lower() == "tipping"]
        if dates and tip_jobs:
            try:
                prior_start = (date.fromisoformat(dates["startDate"]) - timedelta(days=14)).isoformat()
                prior_end = (date.fromisoformat(dates["startDate"]) - timedelta(days=1)).isoformat()
                sample = client.request("https://api.tcplusondemand.com/v1/worksegments?" + urlencode({"startDate": prior_start + "T00:00:00", "stopDate": prior_end + "T23:59:59", "includeSegmentTime": "timeIn", "jobCodeIds": ",".join(tip_jobs[:20]), "includeEmployeeDefaultLocation": "true", "perPage": 25, "page": 1}))
                if sample.get("errors"):
                    raise InspectionError("TCP returned application errors for the tip sample.")
                report["historical_tip_sample"] = {
                    "complete": False,
                    "note": "First page of the prior 14 days; excluded from period totals.",
                    "startDate": prior_start,
                    "stopDate": prior_end,
                    "records": [project("worksegments", row) for row in sample.get("data") or []],
                }
            except InspectionError as exc:
                report["historical_tip_sample"] = {"error": str(exc)}
    except InspectionError as exc:
        report["errors"]["setup"] = str(exc)
        print(str(exc))
    report["request_count"] = client.requests
    output = Path(__file__).parent / "reports" / "inspection.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Private inspection report saved; no credentials are included.")
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
