#!/usr/bin/env python3
"""
lm_alert_rules.py
=================
Creates or updates LogicMonitor alert rules from an Excel sheet and binds each
rule to its escalation chain.

Built for Infosys delivery teams. Run lm_escalation_chains.py first, since a
rule cannot be created without an existing chain.

Usage
-----
    python3 lm_alert_rules.py --excel alerting.xlsx --dry-run
    python3 lm_alert_rules.py --excel alerting.xlsx
    python3 lm_alert_rules.py --excel alerting.xlsx --update-existing

Excel columns
-------------
Required:
    ruleName              Alert rule name
    priority              Evaluation order. A number, or high / medium / low.
                          Lower numbers are evaluated first.
    escalationChainName   Name of an existing escalation chain

Optional (filters — blank means match everything):
    dataSource            DataSource name, wildcards allowed, e.g. *MYSQL*
    instance              Instance name, wildcards allowed
    datapoint             Datapoint name, wildcards allowed
    devices               Device display names, separated by ;
    deviceGroups          Resource group full paths, separated by ;
    levelStr              All | Warning | Error | Critical   (default All)

Optional (behaviour):
    escalationInterval    Minutes between escalation stages. 0 disables escalation.
    suppressAlertClear    true / false
    suppressAlertAckSdt   true / false
    description           Rule description
    note                  Free text, carried into the report only

One row is one alert rule. Nothing is grouped.

Priority matters
----------------
An alert is routed by the first matching rule only. Put specific rules above
broad ones by giving them a lower priority number. The script reports every
rule's priority so the resulting order can be reviewed before going live.

Outputs
-------
    <excel_basename>_rules_report.csv
    <excel_basename>_rules.log
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

REQUIRED = ("ruleName", "priority", "escalationChainName")
PRIORITY_WORDS = {"high": 100, "medium": 200, "low": 300}
LEVELS = {"all": "All", "warning": "Warn", "warn": "Warn", "error": "Error", "critical": "Critical"}
EXTRA_COLUMNS = ("ruleId", "priority", "chain", "scope")


def split_list(value: str) -> list[str]:
    return [part.strip() for part in value.replace(",", ";").split(";") if part.strip()]


def resolve_priority(value: str) -> int:
    text = value.strip().lower()
    if text in PRIORITY_WORDS:
        return PRIORITY_WORDS[text]
    return as_int(value, "priority")


def build_payload(row: dict, chain_id: int) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": row["ruleName"],
        "priority": resolve_priority(row["priority"]),
        "escalatingChainId": chain_id,
        "datasource": row.get("dataSource", "") or "*",
        "instance": row.get("instance", "") or "*",
        "datapoint": row.get("datapoint", "") or "*",
    }

    level_raw = (row.get("levelStr", "") or "all").strip().lower()
    if level_raw not in LEVELS:
        raise ValueError(
            f"levelStr '{row.get('levelStr')}' is not valid. "
            "Use All, Warning, Error or Critical."
        )
    payload["levelStr"] = LEVELS[level_raw]

    devices = split_list(row.get("devices", ""))
    groups = split_list(row.get("deviceGroups", ""))
    if devices:
        payload["devices"] = devices
    if groups:
        payload["deviceGroups"] = groups

    if row.get("escalationInterval"):
        payload["escalationInterval"] = as_int(row["escalationInterval"], "escalationInterval")
    if row.get("suppressAlertClear"):
        payload["suppressAlertClear"] = as_bool(row["suppressAlertClear"])
    if row.get("suppressAlertAckSdt"):
        payload["suppressAlertAckSdt"] = as_bool(row["suppressAlertAckSdt"])
    if row.get("description"):
        payload["description"] = row["description"]

    return payload


def describe_scope(payload: dict[str, Any]) -> str:
    parts = []
    if payload.get("devices"):
        parts.append(f"{len(payload['devices'])} device(s)")
    if payload.get("deviceGroups"):
        parts.append(f"{len(payload['deviceGroups'])} group(s)")
    if not parts:
        parts.append("all resources")
    parts.append(f"ds={payload['datasource']}")
    parts.append(f"dp={payload['datapoint']}")
    return " ".join(parts)


def process(
    row: dict, client: LogicMonitorClient | None, dry_run: bool, update_existing: bool
) -> RowResult:
    result = RowResult(row=row["_row"], target=row.get("ruleName", ""))
    result.extra = {"ruleId": "", "priority": "", "chain": row.get("escalationChainName", ""), "scope": ""}

    missing = [column for column in REQUIRED if not row.get(column)]
    if missing:
        result.action = "failed"
        result.detail = f"Missing required value(s): {', '.join(missing)}"
        return result

    if dry_run:
        try:
            payload = build_payload(row, chain_id=0)
        except ValueError as exc:
            result.action = "failed"
            result.detail = str(exc)
            return result
        result.extra["priority"] = str(payload["priority"])
        result.extra["scope"] = describe_scope(payload)
        result.action = "dry-run"
        result.detail = f"Would route {payload['levelStr']} alerts for {result.extra['scope']}"
        return result

    try:
        chain = client.find_escalation_chain(row["escalationChainName"])
        payload = build_payload(row, chain["id"])
        result.extra["priority"] = str(payload["priority"])
        result.extra["scope"] = describe_scope(payload)

        # Validate the referenced resources before creating the rule, so a typo
        # surfaces here rather than as a rule that silently matches nothing.
        for group_path in payload.get("deviceGroups", []):
            client.find_device_group(group_path)
        for device_name in payload.get("devices", []):
            client.find_device(device_name)

        existing = client.find_alert_rule(payload["name"])
        if existing:
            result.extra["ruleId"] = str(existing["id"])
            if not update_existing:
                result.action = "skipped"
                result.detail = "Rule already exists. Re-run with --update-existing to modify."
                return result
            client.request("PATCH", f"/setting/alert/rules/{existing['id']}", payload=payload)
            result.action = "updated"
            result.detail = f"priority {payload['priority']} -> chain '{chain['name']}'"
            return result

        created = client.request("POST", "/setting/alert/rules", payload=payload)
        result.extra["ruleId"] = str(created.get("id", ""))
        result.action = "created"
        result.detail = f"priority {payload['priority']} -> chain '{chain['name']}'"
    except (LogicMonitorError, ValueError) as exc:
        result.action = "failed"
        result.detail = str(exc)

    return result


def warn_on_priority_clashes(rows: list[dict]) -> None:
    seen: dict[int, str] = {}
    for row in rows:
        try:
            priority = resolve_priority(row.get("priority", ""))
        except ValueError:
            continue
        name = row.get("ruleName", "")
        if priority in seen and seen[priority] != name:
            log.warning(
                "Priority %d is used by both '%s' and '%s'. Routing order between them "
                "is not guaranteed.",
                priority,
                seen[priority],
                name,
            )
        seen[priority] = name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create or update LogicMonitor alert rules from an Excel sheet."
    )
    parser.add_argument("--excel", required=True, help="Path to the Excel file")
    parser.add_argument("--sheet", help="Worksheet name (default: first sheet)")
    parser.add_argument("--company", help="Portal name, e.g. infosys (skips the prompt)")
    parser.add_argument(
        "--update-existing",
        action="store_true",
        help="Update rules that already exist instead of skipping them",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the sheet and show the routing each rule would produce",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    excel_path = Path(args.excel).expanduser().resolve()
    stem = Path(f"{excel_path.with_suffix('')}_rules")
    configure_logging(Path(f"{stem}.log"), args.verbose)

    log.info("LogicMonitor alert rule setup")
    log.info("Workbook: %s", excel_path)

    try:
        rows = read_sheet(excel_path, args.sheet, required=REQUIRED)
        log.info("Parsed %d rule row(s)", len(rows))
        warn_on_priority_clashes(rows)

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
        # Create in priority order so the portal's rule list reads sensibly.
        def sort_key(row: dict) -> int:
            try:
                return resolve_priority(row.get("priority", ""))
            except ValueError:
                return 10**6

        for row in sorted(rows, key=sort_key):
            result = process(row, client, args.dry_run, args.update_existing)
            results.append(result)
            level = logging.ERROR if result.action == "failed" else logging.INFO
            log.log(level, "%-38s %-9s %s", result.target[:38], result.action, result.detail)
    except KeyboardInterrupt:
        log.warning("Interrupted. Writing a report for %d processed rule(s).", len(results))

    return finish(stem, results, EXTRA_COLUMNS)


if __name__ == "__main__":
    sys.exit(main())
