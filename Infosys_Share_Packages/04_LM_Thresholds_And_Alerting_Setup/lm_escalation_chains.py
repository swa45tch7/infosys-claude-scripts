#!/usr/bin/env python3
"""
lm_escalation_chains.py
=======================
Creates or updates LogicMonitor escalation chains from an Excel sheet.

Built for Infosys delivery teams. Run this before lm_alert_rules.py, because
alert rules reference chains by name.

Usage
-----
    python3 lm_escalation_chains.py --excel alerting.xlsx --dry-run
    python3 lm_escalation_chains.py --excel alerting.xlsx
    python3 lm_escalation_chains.py --excel alerting.xlsx --update-existing

Excel columns
-------------
Required:
    chainName        Escalation chain name. Rows sharing a name build one chain.
    stage            Stage number, starting at 1
    recipientType    group | admin | arbitrary
    recipient        Recipient group name (group), LogicMonitor username (admin),
                     or email address / phone number (arbitrary)

Optional:
    method           email | sms | voice | smsemail   (not needed for group)
    contact          Phone number or contact string, where the method needs one
    description      Chain description. Taken from the first row of the chain.
    enableThrottling true / false
    throttlingPeriod     Minutes
    throttlingAlerts     Alert count within the period
    ccRecipient      Recipient copied on every stage
    ccRecipientType  group | admin | arbitrary   (default admin)
    ccMethod         email | sms | voice | smsemail
    note             Free text, carried into the report only

How stages work
---------------
Rows sharing chainName are grouped, then split into stages by the stage column.
Every recipient in a stage is notified together. A later stage is only notified
if the alert is still active and unacknowledged after the escalation interval,
which is set on the alert rule, not on the chain.

Stage numbers must be consecutive from 1. A gap is reported as a failure rather
than silently collapsed, because an accidental gap changes who gets paged.

Outputs
-------
    <excel_basename>_chains_report.csv
    <excel_basename>_chains.log
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

from lm_api import (
    LogicMonitorClient,
    LogicMonitorError,
    RowResult,
    as_bool,
    as_int,
    configure_logging,
    finish,
    prompt_credentials,
    read_sheet,
)

log = logging.getLogger("lm")

REQUIRED = ("chainName", "stage", "recipientType", "recipient")
VALID_TYPES = {"group", "admin", "arbitrary"}
VALID_METHODS = {"email", "sms", "voice", "smsemail"}
EXTRA_COLUMNS = ("chainId", "stages", "recipients")


def build_recipient(
    recipient_type: str, recipient: str, method: str, contact: str
) -> dict[str, Any]:
    recipient_type = recipient_type.strip().lower()
    if recipient_type not in VALID_TYPES:
        raise ValueError(
            f"recipientType '{recipient_type}' is not valid. Use: {', '.join(sorted(VALID_TYPES))}"
        )

    entry: dict[str, Any] = {"type": recipient_type, "addr": recipient.strip()}

    if recipient_type == "group":
        # A recipient group carries its own contact methods, so method is ignored.
        return entry

    method = (method or "email").strip().lower()
    if method not in VALID_METHODS:
        raise ValueError(
            f"method '{method}' is not valid. Use: {', '.join(sorted(VALID_METHODS))}"
        )
    entry["method"] = method
    if contact:
        entry["contact"] = contact.strip()
    return entry


def group_rows(rows: list[dict]) -> list[dict]:
    """Collapse recipient rows into one chain spec per chainName."""
    chains: dict[str, dict] = {}
    order: list[str] = []

    for row in rows:
        name = row.get("chainName", "")
        key = name.lower()
        if key not in chains:
            chains[key] = {
                "name": name,
                "rows": [],
                "stages": {},
                "description": "",
                "throttling": {},
                "cc": [],
                "note": "",
                "error": "",
            }
            order.append(key)

        spec = chains[key]
        spec["rows"].append(row["_row"])

        if row.get("description") and not spec["description"]:
            spec["description"] = row["description"]
        if row.get("note") and not spec["note"]:
            spec["note"] = row["note"]

        for column in ("enableThrottling", "throttlingPeriod", "throttlingAlerts"):
            if row.get(column):
                spec["throttling"][column] = row[column]

        if row.get("ccRecipient"):
            try:
                spec["cc"].append(
                    build_recipient(
                        row.get("ccRecipientType", "") or "admin",
                        row["ccRecipient"],
                        row.get("ccMethod", ""),
                        "",
                    )
                )
            except ValueError as exc:
                spec["error"] = spec["error"] or f"row {row['_row']}: {exc}"

        stage_raw = row.get("stage", "")
        try:
            stage = as_int(stage_raw, "stage")
            if stage < 1:
                raise ValueError("stage must be 1 or higher")
            recipient = build_recipient(
                row.get("recipientType", ""),
                row.get("recipient", ""),
                row.get("method", ""),
                row.get("contact", ""),
            )
        except ValueError as exc:
            spec["error"] = spec["error"] or f"row {row['_row']}: {exc}"
            continue

        spec["stages"].setdefault(stage, []).append(recipient)

    return [chains[key] for key in order]


def build_payload(spec: dict) -> dict[str, Any]:
    if spec["error"]:
        raise ValueError(spec["error"])
    if not spec["name"]:
        raise ValueError("chainName is empty")
    if not spec["stages"]:
        raise ValueError("No valid recipients found for this chain")

    numbers = sorted(spec["stages"])
    expected = list(range(1, len(numbers) + 1))
    if numbers != expected:
        raise ValueError(
            f"Stage numbers must run consecutively from 1. Found: {numbers}. "
            "Fix the stage column or add the missing stage."
        )

    payload: dict[str, Any] = {
        "name": spec["name"],
        "destinations": [
            {"type": "single", "stages": [spec["stages"][number] for number in numbers]}
        ],
    }
    if spec["description"]:
        payload["description"] = spec["description"]
    if spec["cc"]:
        payload["ccDestinations"] = spec["cc"]

    throttling = spec["throttling"]
    if "enableThrottling" in throttling:
        payload["enableThrottling"] = as_bool(throttling["enableThrottling"])
    if "throttlingPeriod" in throttling:
        payload["throttlingPeriod"] = as_int(throttling["throttlingPeriod"], "throttlingPeriod")
    if "throttlingAlerts" in throttling:
        payload["throttlingAlerts"] = as_int(throttling["throttlingAlerts"], "throttlingAlerts")

    return payload


def process(
    spec: dict, client: LogicMonitorClient | None, dry_run: bool, update_existing: bool
) -> RowResult:
    result = RowResult(row=",".join(spec["rows"]), target=spec["name"])

    try:
        payload = build_payload(spec)
    except ValueError as exc:
        result.action = "failed"
        result.detail = str(exc)
        return result

    stage_count = len(payload["destinations"][0]["stages"])
    recipient_count = sum(len(stage) for stage in payload["destinations"][0]["stages"])
    result.extra = {"chainId": "", "stages": str(stage_count), "recipients": str(recipient_count)}

    if dry_run:
        result.action = "dry-run"
        result.detail = f"Would create chain with {stage_count} stage(s)"
        return result

    try:
        existing = None
        try:
            existing = client.find_escalation_chain(spec["name"])
        except LogicMonitorError:
            pass

        if existing:
            result.extra["chainId"] = str(existing["id"])
            if not update_existing:
                result.action = "skipped"
                result.detail = "Chain already exists. Re-run with --update-existing to modify."
                return result
            client.request(
                "PATCH", f"/setting/alert/chains/{existing['id']}", payload=payload
            )
            result.action = "updated"
            result.detail = f"{stage_count} stage(s), {recipient_count} recipient(s)"
            return result

        created = client.request("POST", "/setting/alert/chains", payload=payload)
        result.extra["chainId"] = str(created.get("id", ""))
        result.action = "created"
        result.detail = f"{stage_count} stage(s), {recipient_count} recipient(s)"
    except LogicMonitorError as exc:
        result.action = "failed"
        result.detail = str(exc)

    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create or update LogicMonitor escalation chains from an Excel sheet."
    )
    parser.add_argument("--excel", required=True, help="Path to the Excel file")
    parser.add_argument("--sheet", help="Worksheet name (default: first sheet)")
    parser.add_argument("--company", help="Portal name, e.g. infosys (skips the prompt)")
    parser.add_argument(
        "--update-existing",
        action="store_true",
        help="Update chains that already exist instead of skipping them",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the sheet and show the stage layout without applying anything",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    excel_path = Path(args.excel).expanduser().resolve()
    stem = Path(f"{excel_path.with_suffix('')}_chains")
    configure_logging(Path(f"{stem}.log"), args.verbose)

    log.info("LogicMonitor escalation chain setup")
    log.info("Workbook: %s", excel_path)

    try:
        rows = read_sheet(excel_path, args.sheet, required=REQUIRED)
        specs = group_rows(rows)
        log.info("Parsed %d row(s) into %d chain(s)", len(rows), len(specs))

        client = None if args.dry_run else prompt_credentials(args.company)
        log.info("Mode: %s", "DRY RUN - no changes" if args.dry_run else "LIVE")
    except LogicMonitorError as exc:
        log.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        log.warning("Cancelled before any changes were made.")
        return 130

    log.info("-" * 78)
    results: list[RowResult] = []
    try:
        for spec in specs:
            result = process(spec, client, args.dry_run, args.update_existing)
            results.append(result)
            level = logging.ERROR if result.action == "failed" else logging.INFO
            log.log(level, "%-40s %-9s %s", result.target[:40], result.action, result.detail)
    except KeyboardInterrupt:
        log.warning("Interrupted. Writing a report for %d processed chain(s).", len(results))

    return finish(stem, results, EXTRA_COLUMNS)


if __name__ == "__main__":
    sys.exit(main())
