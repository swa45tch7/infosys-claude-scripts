#!/usr/bin/env python3
"""
lm_device_onboard.py
====================
Standardized bulk device onboarding for LogicMonitor (LM Envision).
Built for Infosys delivery teams. Reads a CSV, resolves groups and collectors,
and creates/updates resources via LogicMonitor REST API v3.

Usage
-----
    python3 lm_device_onboard.py --csv devices.csv
    python3 lm_device_onboard.py --csv devices.csv --dry-run
    python3 lm_device_onboard.py --csv devices.csv --collector-id 171 --update-existing

Credentials
-----------
Prompted interactively (Access Key is never echoed). Optionally supplied via
environment variables to support pipeline use:
    LM_COMPANY, LM_ACCESS_ID, LM_ACCESS_KEY

CSV columns
-----------
Required:
    name            IP address or DNS name (LogicMonitor "name" / host)
Optional:
    displayName     Display name in LM. Defaults to name.
    groupFullPath   e.g. Infosys/Customers/ACME/Network  (auto-created if absent)
    hostGroupIds    Comma-separated group IDs. Overrides groupFullPath.
    description      Free text
    collectorId     Numeric collector ID. Overrides the session-level collector.
    collectorName   Collector hostname/description. Resolved to an ID.
    disableAlerting  true / false
Properties:
    Any column prefixed with "prop." becomes a custom property.
    Example: prop.snmp.community, prop.location, prop.system.categories

Outputs
-------
    <csv_basename>_onboarding_report.csv   per-row outcome
    <csv_basename>_onboarding.log          full run log

API reference: POST /device/devices, GET /setting/collectors, GET|POST /device/groups
"""

from __future__ import annotations

import argparse
import base64
import csv
import getpass
import hashlib
import hmac
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    sys.exit("Missing dependency. Install with:  pip install requests")

API_VERSION = "3"
REQUEST_TIMEOUT = 45
MAX_RETRIES = 4
BACKOFF_BASE = 2.0
PAGE_SIZE = 1000
PROP_PREFIX = "prop."
TRUE_VALUES = {"true", "yes", "y", "1", "enable", "enabled"}

log = logging.getLogger("lm-onboard")


# --------------------------------------------------------------------------- #
# API client
# --------------------------------------------------------------------------- #
class LogicMonitorError(RuntimeError):
    """Raised for non-retryable API failures."""


class LogicMonitorClient:
    """Minimal LMv1-authenticated client for the endpoints this script needs."""

    def __init__(self, company: str, access_id: str, access_key: str):
        self.base_url = f"https://{company}.logicmonitor.com/santaba/rest"
        self._access_id = access_id
        self._access_key = access_key.encode("utf-8")
        self._session = requests.Session()
        self._group_cache: dict[str, int] = {}

    # -- auth ------------------------------------------------------------- #
    def _auth_header(self, verb: str, resource_path: str, body: str) -> str:
        epoch = str(int(time.time() * 1000))
        message = f"{verb}{epoch}{body}{resource_path}"
        digest = hmac.new(self._access_key, message.encode("utf-8"), hashlib.sha256).hexdigest()
        signature = base64.b64encode(digest.encode("utf-8")).decode("utf-8")
        return f"LMv1 {self._access_id}:{signature}:{epoch}"

    # -- transport -------------------------------------------------------- #
    def request(
        self,
        verb: str,
        resource_path: str,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = json.dumps(payload) if payload is not None else ""
        url = self.base_url + resource_path
        params = dict(params or {})
        params["v"] = API_VERSION

        for attempt in range(1, MAX_RETRIES + 1):
            headers = {
                "Content-Type": "application/json",
                "X-Version": API_VERSION,
                "Authorization": self._auth_header(verb, resource_path, body),
            }
            try:
                response = self._session.request(
                    verb,
                    url,
                    data=body if body else None,
                    params=params,
                    headers=headers,
                    timeout=REQUEST_TIMEOUT,
                )
            except requests.RequestException as exc:
                if attempt == MAX_RETRIES:
                    raise LogicMonitorError(f"Network error calling {resource_path}: {exc}") from exc
                self._sleep(attempt, f"network error ({exc.__class__.__name__})")
                continue

            # 429 = rate limited, 5xx = transient server side. Both worth a retry.
            if response.status_code == 429 or 500 <= response.status_code < 600:
                if attempt == MAX_RETRIES:
                    raise LogicMonitorError(
                        f"{verb} {resource_path} failed after {MAX_RETRIES} attempts "
                        f"(HTTP {response.status_code}): {response.text[:300]}"
                    )
                wait = response.headers.get("X-Rate-Limit-Window") if response.status_code == 429 else None
                self._sleep(attempt, f"HTTP {response.status_code}", float(wait) if wait else None)
                continue

            if response.status_code == 401:
                raise LogicMonitorError(
                    "Authentication failed (HTTP 401). Check the portal name, Access ID "
                    "and Access Key, and confirm the API token is enabled."
                )
            if response.status_code == 403:
                raise LogicMonitorError(
                    "Permission denied (HTTP 403). The API token's role needs Manage "
                    "rights on Resources and View rights on Collectors."
                )

            try:
                parsed = response.json()
            except ValueError:
                raise LogicMonitorError(
                    f"{verb} {resource_path} returned non-JSON (HTTP {response.status_code})"
                ) from None

            if not response.ok or parsed.get("errmsg", "OK") != "OK":
                raise LogicMonitorError(
                    f"{verb} {resource_path} -> HTTP {response.status_code}: "
                    f"{parsed.get('errmsg') or parsed}"
                )
            return parsed.get("data", parsed)

        raise LogicMonitorError(f"{verb} {resource_path} exhausted retries")

    @staticmethod
    def _sleep(attempt: int, reason: str, override: float | None = None) -> None:
        wait = override if override else BACKOFF_BASE ** attempt
        log.warning("Retry %d/%d in %.1fs (%s)", attempt, MAX_RETRIES, wait, reason)
        time.sleep(wait)

    # -- collectors ------------------------------------------------------- #
    def list_collectors(self) -> list[dict[str, Any]]:
        data = self.request(
            "GET",
            "/setting/collectors",
            params={
                "size": PAGE_SIZE,
                "sort": "id",
                "fields": "id,hostname,description,collectorGroupName,isDown,platform,collectorSize",
            },
        )
        return data.get("items", [])

    # -- devices ---------------------------------------------------------- #
    def find_device(self, name: str) -> dict[str, Any] | None:
        data = self.request(
            "GET",
            "/device/devices",
            params={"filter": f'name:"{name}"', "size": 1, "fields": "id,name,displayName"},
        )
        items = data.get("items", [])
        return items[0] if items else None

    def add_device(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/device/devices", payload=payload)

    def update_device(self, device_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request(
            "PATCH",
            f"/device/devices/{device_id}",
            payload=payload,
            params={"opType": "replace"},
        )

    # -- groups ----------------------------------------------------------- #
    def resolve_group_path(self, full_path: str, create: bool = True) -> int:
        """Return the group ID for a '/'-delimited path, creating levels as needed."""
        clean = full_path.strip().strip("/")
        if not clean:
            return 1  # root
        if clean in self._group_cache:
            return self._group_cache[clean]

        parent_id = 1
        walked: list[str] = []
        for segment in [s.strip() for s in clean.split("/") if s.strip()]:
            walked.append(segment)
            key = "/".join(walked)
            if key in self._group_cache:
                parent_id = self._group_cache[key]
                continue

            data = self.request(
                "GET",
                "/device/groups",
                params={"filter": f'parentId:{parent_id},name:"{segment}"', "size": 1, "fields": "id,name"},
            )
            items = data.get("items", [])
            if items:
                parent_id = items[0]["id"]
            elif create:
                created = self.request(
                    "POST", "/device/groups", payload={"name": segment, "parentId": parent_id}
                )
                parent_id = created["id"]
                log.info("Created device group: %s (id %s)", key, parent_id)
            else:
                raise LogicMonitorError(f"Device group not found: {key}")

            self._group_cache[key] = parent_id
        return parent_id


# --------------------------------------------------------------------------- #
# CSV handling
# --------------------------------------------------------------------------- #
@dataclass
class RowResult:
    row: int
    name: str
    display_name: str = ""
    action: str = "skipped"
    device_id: str = ""
    collector_id: str = ""
    group: str = ""
    detail: str = ""


@dataclass
class Session:
    client: LogicMonitorClient
    default_collector_id: int
    collectors_by_name: dict[str, int] = field(default_factory=dict)
    valid_collector_ids: set[int] = field(default_factory=set)
    dry_run: bool = False
    update_existing: bool = False


HOSTNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._\-:]*[A-Za-z0-9])?$")


def normalise_header(header: str) -> str:
    return header.strip().lstrip("\ufeff")


def read_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise LogicMonitorError(f"{csv_path.name} has no header row.")
        reader.fieldnames = [normalise_header(h) for h in reader.fieldnames]
        if "name" not in reader.fieldnames:
            raise LogicMonitorError(
                f"{csv_path.name} must contain a 'name' column "
                f"(found: {', '.join(reader.fieldnames)})"
            )
        return [
            {k: (v or "").strip() for k, v in row.items() if k}
            for row in reader
            if any((v or "").strip() for v in row.values())
        ]


def build_properties(row: dict[str, str]) -> list[dict[str, str]]:
    props = []
    for column, value in row.items():
        if column.lower().startswith(PROP_PREFIX) and value:
            props.append({"name": column[len(PROP_PREFIX):].strip(), "value": value})
    return props


def resolve_collector(row: dict[str, str], session: Session, result: RowResult) -> int:
    raw_id = row.get("collectorId", "")
    if raw_id:
        if not raw_id.lstrip("-").isdigit():
            raise ValueError(f"collectorId must be numeric, got '{raw_id}'")
        collector_id = int(raw_id)
        if session.valid_collector_ids and collector_id not in session.valid_collector_ids:
            raise ValueError(f"collectorId {collector_id} does not exist in this portal")
        return collector_id

    raw_name = row.get("collectorName", "")
    if raw_name:
        collector_id = session.collectors_by_name.get(raw_name.lower())
        if collector_id is None:
            raise ValueError(f"collectorName '{raw_name}' not found in this portal")
        return collector_id

    return session.default_collector_id


def resolve_groups(row: dict[str, str], session: Session, result: RowResult) -> str:
    explicit = row.get("hostGroupIds", "")
    if explicit:
        ids = [part.strip() for part in explicit.split(",") if part.strip()]
        if not all(i.isdigit() for i in ids):
            raise ValueError(f"hostGroupIds must be numeric IDs, got '{explicit}'")
        result.group = explicit
        return ",".join(ids)

    path = row.get("groupFullPath", "")
    if path:
        result.group = path
        if session.dry_run:
            return ""  # do not create groups during a dry run
        return str(session.client.resolve_group_path(path))

    result.group = "root"
    return "1"


def build_payload(row: dict[str, str], session: Session, result: RowResult) -> dict[str, Any]:
    name = row["name"]
    if not HOSTNAME_RE.match(name):
        raise ValueError(f"'{name}' is not a valid IP address or DNS name")

    display_name = row.get("displayName") or name
    result.display_name = display_name

    collector_id = resolve_collector(row, session, result)
    result.collector_id = str(collector_id)

    payload: dict[str, Any] = {
        "name": name,
        "displayName": display_name,
        "preferredCollectorId": collector_id,
        "disableAlerting": row.get("disableAlerting", "").lower() in TRUE_VALUES,
    }

    group_ids = resolve_groups(row, session, result)
    if group_ids:
        payload["hostGroupIds"] = group_ids

    if row.get("description"):
        payload["description"] = row["description"]

    properties = build_properties(row)
    if properties:
        payload["customProperties"] = properties

    return payload


# --------------------------------------------------------------------------- #
# Onboarding
# --------------------------------------------------------------------------- #
def onboard_row(index: int, row: dict[str, str], session: Session) -> RowResult:
    result = RowResult(row=index, name=row.get("name", ""))

    if not result.name:
        result.action = "failed"
        result.detail = "Missing 'name' value"
        return result

    try:
        payload = build_payload(row, session, result)
    except (ValueError, LogicMonitorError) as exc:
        result.action = "failed"
        result.detail = str(exc)
        return result

    prop_count = len(payload.get("customProperties", []))

    if session.dry_run:
        result.action = "dry-run"
        result.detail = f"Would onboard with {prop_count} property(ies)"
        return result

    try:
        existing = session.client.find_device(result.name)
        if existing:
            result.device_id = str(existing["id"])
            if not session.update_existing:
                result.action = "skipped"
                result.detail = "Already exists. Re-run with --update-existing to modify."
                return result
            session.client.update_device(existing["id"], payload)
            result.action = "updated"
            result.detail = f"{prop_count} property(ies) applied"
            return result

        created = session.client.add_device(payload)
        result.device_id = str(created.get("id", ""))
        result.action = "created"
        result.detail = f"{prop_count} property(ies) applied"
    except LogicMonitorError as exc:
        result.action = "failed"
        result.detail = str(exc)

    return result


def write_report(path: Path, results: list[RowResult]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["row", "name", "displayName", "action", "deviceId", "collectorId", "group", "detail"]
        )
        for r in results:
            writer.writerow(
                [r.row, r.name, r.display_name, r.action, r.device_id, r.collector_id, r.group, r.detail]
            )


# --------------------------------------------------------------------------- #
# Interactive setup
# --------------------------------------------------------------------------- #
def prompt_credentials(args: argparse.Namespace) -> tuple[str, str, str]:
    company = args.company or os.getenv("LM_COMPANY") or input(
        "LogicMonitor portal / company name (e.g. infosys): "
    ).strip()
    access_id = os.getenv("LM_ACCESS_ID") or input("Access ID: ").strip()
    access_key = os.getenv("LM_ACCESS_KEY") or getpass.getpass("Access Key (hidden): ").strip()

    if not (company and access_id and access_key):
        raise LogicMonitorError("Portal name, Access ID and Access Key are all required.")
    return company, access_id, access_key


def select_collector(collectors: list[dict[str, Any]], preset: int | None) -> int:
    if not collectors:
        raise LogicMonitorError(
            "No collectors returned. Confirm the portal has at least one collector "
            "and that the API token can view collectors."
        )

    available = {c["id"]: c for c in collectors}
    if preset is not None:
        if preset not in available:
            raise LogicMonitorError(f"Collector ID {preset} does not exist in this portal.")
        chosen = available[preset]
        log.info("Using collector %s (id %s) from --collector-id", _label(chosen), preset)
        return preset

    print("\nAvailable collectors")
    print("-" * 78)
    print(f"{'#':>3}  {'ID':>6}  {'Status':<8}  Collector")
    print("-" * 78)
    for position, collector in enumerate(collectors, start=1):
        status = "DOWN" if collector.get("isDown") else "up"
        print(f"{position:>3}  {collector['id']:>6}  {status:<8}  {_label(collector)}")
    print("-" * 78)

    while True:
        raw = input("Select collector by # (or enter 'id:<number>'): ").strip()
        if raw.lower().startswith("id:"):
            candidate = raw[3:].strip()
            if candidate.isdigit() and int(candidate) in available:
                return int(candidate)
        elif raw.isdigit() and 1 <= int(raw) <= len(collectors):
            return collectors[int(raw) - 1]["id"]
        print("Invalid selection. Try again.")


def _label(collector: dict[str, Any]) -> str:
    hostname = collector.get("hostname") or "unknown-host"
    description = collector.get("description") or ""
    group = collector.get("collectorGroupName") or ""
    parts = [hostname]
    if description and description != hostname:
        parts.append(f"({description})")
    if group:
        parts.append(f"[group: {group}]")
    return " ".join(parts)


def configure_logging(log_path: Path, verbose: bool) -> None:
    handlers: list[logging.Handler] = [logging.FileHandler(log_path, encoding="utf-8")]
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter("%(message)s"))
    handlers.append(console)

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        handlers=handlers,
    )


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bulk-onboard devices into LogicMonitor from a CSV file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--csv", required=True, help="Path to the device CSV file")
    parser.add_argument("--company", help="Portal name, e.g. infosys (skips the prompt)")
    parser.add_argument("--collector-id", type=int, help="Default collector ID (skips selection)")
    parser.add_argument(
        "--update-existing",
        action="store_true",
        help="Update devices that already exist instead of skipping them",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the CSV and credentials without writing to LogicMonitor",
    )
    parser.add_argument("--verbose", action="store_true", help="Verbose console output")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    csv_path = Path(args.csv).expanduser().resolve()
    if not csv_path.is_file():
        print(f"CSV not found: {csv_path}", file=sys.stderr)
        return 2

    stem = csv_path.with_suffix("")
    configure_logging(Path(f"{stem}_onboarding.log"), args.verbose)

    log.info("LogicMonitor device onboarding")
    log.info("CSV: %s", csv_path)

    try:
        rows = read_rows(csv_path)
        log.info("Parsed %d data row(s)", len(rows))

        company, access_id, access_key = prompt_credentials(args)
        client = LogicMonitorClient(company, access_id, access_key)
        log.info("Portal: %s.logicmonitor.com", company)

        collectors = client.list_collectors()
        log.info("Retrieved %d collector(s)", len(collectors))
        collector_id = select_collector(collectors, args.collector_id)

        session = Session(
            client=client,
            default_collector_id=collector_id,
            collectors_by_name={
                key.lower(): c["id"]
                for c in collectors
                for key in (c.get("hostname"), c.get("description"))
                if key
            },
            valid_collector_ids={c["id"] for c in collectors},
            dry_run=args.dry_run,
            update_existing=args.update_existing,
        )
    except LogicMonitorError as exc:
        log.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        log.warning("Cancelled before any changes were made.")
        return 130

    mode = "DRY RUN — no changes will be made" if args.dry_run else "LIVE"
    log.info("Mode: %s", mode)
    log.info("Default collector: %s", collector_id)
    log.info("-" * 78)

    results: list[RowResult] = []
    try:
        for index, row in enumerate(rows, start=2):  # row 1 is the header
            result = onboard_row(index, row, session)
            results.append(result)
            level = logging.ERROR if result.action == "failed" else logging.INFO
            log.log(
                level,
                "row %-4d %-40s %-9s %s",
                result.row,
                result.name[:40],
                result.action,
                result.detail,
            )
    except KeyboardInterrupt:
        log.warning("Interrupted. Writing a report for %d processed row(s).", len(results))

    report_path = Path(f"{stem}_onboarding_report.csv")
    write_report(report_path, results)

    tally: dict[str, int] = {}
    for r in results:
        tally[r.action] = tally.get(r.action, 0) + 1

    log.info("-" * 78)
    log.info("Summary: %s", ", ".join(f"{k}={v}" for k, v in sorted(tally.items())) or "nothing processed")
    log.info("Report: %s", report_path)

    return 1 if tally.get("failed") else 0


if __name__ == "__main__":
    sys.exit(main())
