#!/usr/bin/env python3
"""
lm_threshold_apply.py
=====================
Applies static datapoint alert thresholds to specific devices from an Excel sheet.

Built for Infosys delivery teams. Reads one row per severity, composes the
LogicMonitor threshold expression, and writes it to the DataSource on each device.

Usage
-----
    python3 lm_threshold_apply.py --excel thresholds.xlsx --dry-run
    python3 lm_threshold_apply.py --excel thresholds.xlsx
    python3 lm_threshold_apply.py --excel thresholds.xlsx --sheet Thresholds

Excel columns
-------------
Required:
    device        Device display name (preferred) or host name as in LogicMonitor
    dataSource    DataSource name exactly as applied to the device, e.g. Ping, CPU
    datapoint     Datapoint name, e.g. PingLossPercent, CPUBusyPercent
    severity      warn | error | critical
    value         The threshold value for that severity

Optional:
    operator      > >= < <= = !=   (default >)
    instance      Instance name, or * for every instance (default *)
    scope         instance | datasource   (default instance)
    alertTransitionInterval        Polls the threshold must be breached before alerting
    alertClearTransitionInterval   Polls below threshold before the alert clears
    disableAlerting                true / false
    alertExpr     Full expression override, e.g. "> 80 90 95". Wins over severity rows.
    note          Free text, carried into the report only

How rows combine
----------------
Rows sharing device + dataSource + instance + datapoint are grouped into one
threshold expression. The severities present fill the slots in order:

    warn=80, error=90, critical=95   ->  "> 80 90 95"
    warn=80, error=90                ->  "> 80 90"
    warn=80                          ->  "> 80"

LogicMonitor evaluates the expression right to left, so the highest severity wins.
If the lowest severity you supply is not "warn", the lower slots are padded with
the same value, which makes only your intended severity fire. Every composed
expression is written to the log so it can be checked.

Outputs
-------
    <excel_basename>_thresholds_report.csv
    <excel_basename>_thresholds.log
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

REQUIRED = ("device", "dataSource", "datapoint")
VALID_OPERATORS = {">", ">=", "<", "<=", "=", "!=", "=="}
SEVERITY_ORDER = ("warn", "error", "critical")
SEVERITY_ALIASES = {
    "warn": "warn",
    "warning": "warn",
    "w": "warn",
    "error": "error",
    "err": "error",
    "e": "error",
    "critical": "critical",
    "crit": "critical",
    "c": "critical",
}
EXTRA_COLUMNS = ("dataSource", "datapoint", "instance", "expression")


# --------------------------------------------------------------------------- #
# Grouping and expression building
# --------------------------------------------------------------------------- #
def group_rows(rows: list[dict]) -> list[dict]:
    """Collapse severity rows into one threshold spec per device/datasource/datapoint."""
    grouped: dict[tuple, dict] = {}
    order: list[tuple] = []

    for row in rows:
        device = row.get("device", "")
        datasource = row.get("dataSource", "")
        datapoint = row.get("datapoint", "")
        instance = row.get("instance", "") or "*"
        scope = (row.get("scope", "") or "instance").lower()

        key = (device.lower(), datasource.lower(), instance.lower(), datapoint.lower(), scope)
        if key not in grouped:
            grouped[key] = {
                "rows": [],
                "device": device,
                "dataSource": datasource,
                "datapoint": datapoint,
                "instance": instance,
                "scope": scope,
                "severities": {},
                "operator": "",
                "alertExpr": "",
                "settings": {},
                "note": "",
            }
            order.append(key)

        spec = grouped[key]
        spec["rows"].append(row["_row"])

        if row.get("alertExpr"):
            spec["alertExpr"] = row["alertExpr"]
        if row.get("operator"):
            spec["operator"] = row["operator"]
        if row.get("note"):
            spec["note"] = row["note"]

        severity_raw = (row.get("severity", "") or "").strip().lower()
        value = row.get("value", "")
        if severity_raw and value:
            severity = SEVERITY_ALIASES.get(severity_raw)
            if severity is None:
                spec["error"] = (
                    f"Unknown severity '{row.get('severity')}'. Use warn, error or critical."
                )
            else:
                spec["severities"][severity] = value

        for column in (
            "alertTransitionInterval",
            "alertClearTransitionInterval",
            "disableAlerting",
        ):
            if row.get(column):
                spec["settings"][column] = row[column]

    return [grouped[key] for key in order]


def build_expression(spec: dict) -> str:
    """Compose the LogicMonitor threshold expression for one spec."""
    if spec["alertExpr"]:
        return spec["alertExpr"].strip()

    severities = spec["severities"]
    if not severities:
        raise ValueError(
            "No threshold given. Supply severity and value, or an alertExpr override."
        )

    operator = (spec["operator"] or ">").strip()
    if operator not in VALID_OPERATORS:
        raise ValueError(
            f"Operator '{operator}' is not supported. Use one of: {' '.join(sorted(VALID_OPERATORS))}"
        )

    for value in severities.values():
        try:
            float(value)
        except ValueError:
            raise ValueError(f"Threshold value '{value}' is not numeric") from None

    highest = max(i for i, name in enumerate(SEVERITY_ORDER) if name in severities)
    slots: list[str] = []
    for index in range(highest + 1):
        name = SEVERITY_ORDER[index]
        if name in severities:
            slots.append(severities[name])
        else:
            # Pad a lower slot with the next supplied value so only the
            # intended severity fires. Right-to-left priority handles the rest.
            following = next(
                severities[SEVERITY_ORDER[j]]
                for j in range(index + 1, len(SEVERITY_ORDER))
                if SEVERITY_ORDER[j] in severities
            )
            slots.append(following)

    return f"{operator} {' '.join(slots)}"


def build_settings(spec: dict) -> dict[str, Any]:
    settings: dict[str, Any] = {}
    raw = spec["settings"]
    if "alertTransitionInterval" in raw:
        settings["alertTransitionInterval"] = as_int(
            raw["alertTransitionInterval"], "alertTransitionInterval"
        )
    if "alertClearTransitionInterval" in raw:
        settings["alertClearTransitionInterval"] = as_int(
            raw["alertClearTransitionInterval"], "alertClearTransitionInterval"
        )
    if "disableAlerting" in raw:
        settings["disableAlerting"] = as_bool(raw["disableAlerting"])
    return settings


# --------------------------------------------------------------------------- #
# Apply
# --------------------------------------------------------------------------- #
def apply_instance_scope(
    client: LogicMonitorClient,
    device_id: int,
    hds_id: int,
    spec: dict,
    expression: str,
    settings: dict[str, Any],
) -> str:
    """Set the threshold on each matching instance of the DataSource."""
    instances = client.list_instances(device_id, hds_id)
    if not instances:
        raise LogicMonitorError(
            "The DataSource is applied but has no discovered instances yet. "
            "Wait for the next Active Discovery cycle."
        )

    wanted = spec["instance"]
    if wanted == "*":
        targets = instances
    else:
        needle = wanted.lower()
        targets = [
            i
            for i in instances
            if needle in (i.get("name") or "").lower()
            or needle in (i.get("displayName") or "").lower()
        ]
        if not targets:
            available = ", ".join(sorted(i.get("name", "") for i in instances)[:10])
            raise LogicMonitorError(
                f"No instance matches '{wanted}'. Instances include: {available}"
            )

    datapoint = spec["datapoint"]
    applied = 0
    unmatched: list[str] = []

    for instance in targets:
        path = (
            f"/device/devices/{device_id}/devicedatasources/{hds_id}"
            f"/instances/{instance['id']}/alertsettings"
        )
        alert_settings = client.get_items(path)
        match = next(
            (
                item
                for item in alert_settings
                if (item.get("dataPointName") or "").lower() == datapoint.lower()
            ),
            None,
        )
        if match is None:
            unmatched.append(instance.get("name", str(instance["id"])))
            continue

        payload: dict[str, Any] = dict(settings)
        if expression:
            payload["alertExpr"] = expression
        client.request("PATCH", f"{path}/{match['id']}", payload=payload)
        applied += 1

    if applied == 0:
        names = ", ".join(
            sorted(
                {
                    (item.get("dataPointName") or "")
                    for instance in targets[:1]
                    for item in client.get_items(
                        f"/device/devices/{device_id}/devicedatasources/{hds_id}"
                        f"/instances/{instance['id']}/alertsettings"
                    )
                }
            )[:12]
        )
        raise LogicMonitorError(
            f"Datapoint '{datapoint}' not found on any matching instance. "
            f"Datapoints available: {names or 'none'}"
        )

    detail = f"{applied} instance(s) updated"
    if unmatched:
        detail += f"; datapoint absent on {len(unmatched)} instance(s)"
    return detail


def apply_datasource_scope(
    client: LogicMonitorClient,
    device_id: int,
    device_datasource: dict,
    spec: dict,
    expression: str,
    settings: dict[str, Any],
) -> str:
    """Set the threshold once at the device's DataSource level, not per instance."""
    hds_id = device_datasource["id"]
    datasource_id = device_datasource.get("dataSourceId")
    if not datasource_id:
        raise LogicMonitorError("Could not determine the DataSource ID for this device.")

    groups = client.get_items(
        f"/device/devices/{device_id}/devicedatasources/{hds_id}/groups",
        params={"fields": "id,name"},
    )
    if not groups:
        raise LogicMonitorError(
            "No instance group found for this DataSource on this device. "
            "Use scope=instance instead."
        )
    group_id = groups[0]["id"]

    datapoints = client.get_items(
        f"/setting/datasources/{datasource_id}/datapoints", params={"fields": "id,name"}
    )
    datapoint = next(
        (d for d in datapoints if (d.get("name") or "").lower() == spec["datapoint"].lower()),
        None,
    )
    if datapoint is None:
        names = ", ".join(sorted(d.get("name", "") for d in datapoints)[:12])
        raise LogicMonitorError(
            f"Datapoint '{spec['datapoint']}' is not defined on DataSource "
            f"'{spec['dataSource']}'. Datapoints include: {names or 'none'}"
        )

    payload: dict[str, Any] = dict(settings)
    if expression:
        payload["alertExpr"] = expression
    client.request(
        "PATCH",
        f"/device/devices/{device_id}/devicedatasources/{hds_id}"
        f"/groups/{group_id}/datapoints/{datapoint['id']}/alertconfig",
        payload=payload,
    )
    return f"DataSource-level threshold set (group {group_id})"


def process(spec: dict, client: LogicMonitorClient, dry_run: bool) -> RowResult:
    rows = ",".join(spec["rows"])
    target = f"{spec['device']}"
    result = RowResult(row=rows, target=target)
    result.extra = {
        "dataSource": spec["dataSource"],
        "datapoint": spec["datapoint"],
        "instance": spec["instance"],
        "expression": "",
    }

    if spec.get("error"):
        result.action = "failed"
        result.detail = spec["error"]
        return result

    missing = [column for column in REQUIRED if not spec.get(column)]
    if missing:
        result.action = "failed"
        result.detail = f"Missing required value(s): {', '.join(missing)}"
        return result

    if spec["scope"] not in {"instance", "datasource"}:
        result.action = "failed"
        result.detail = f"scope must be 'instance' or 'datasource', got '{spec['scope']}'"
        return result

    try:
        settings = build_settings(spec)
        if spec["alertExpr"] or spec["severities"]:
            expression = build_expression(spec)
        elif settings:
            # Settings-only row, e.g. disableAlerting with no threshold change.
            expression = ""
        else:
            raise ValueError(
                "Nothing to apply. Supply severity and value, an alertExpr override, "
                "or a setting such as disableAlerting."
            )
    except ValueError as exc:
        result.action = "failed"
        result.detail = str(exc)
        return result

    result.extra["expression"] = expression or "(settings only)"

    if dry_run:
        change = f"threshold to {expression}" if expression else "settings only"
        changed_settings = ", ".join(f"{k}={v}" for k, v in settings.items())
        result.action = "dry-run"
        result.detail = f"Would set {spec['scope']} {change}" + (
            f" [{changed_settings}]" if changed_settings else ""
        )
        return result

    try:
        device = client.find_device(spec["device"])
        device_datasource = client.find_device_datasource(device["id"], spec["dataSource"])
        if spec["scope"] == "instance":
            result.detail = apply_instance_scope(
                client, device["id"], device_datasource["id"], spec, expression, settings
            )
        else:
            result.detail = apply_datasource_scope(
                client, device["id"], device_datasource, spec, expression, settings
            )
        result.action = "applied"
    except LogicMonitorError as exc:
        result.action = "failed"
        result.detail = str(exc)

    return result


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Apply datapoint alert thresholds to devices from an Excel sheet."
    )
    parser.add_argument("--excel", required=True, help="Path to the threshold Excel file")
    parser.add_argument("--sheet", help="Worksheet name (default: first sheet)")
    parser.add_argument("--company", help="Portal name, e.g. infosys (skips the prompt)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the sheet and show every composed expression without applying anything",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    excel_path = Path(args.excel).expanduser().resolve()
    stem = Path(f"{excel_path.with_suffix('')}_thresholds")
    configure_logging(Path(f"{stem}.log"), args.verbose)

    log.info("LogicMonitor threshold apply")
    log.info("Workbook: %s", excel_path)

    try:
        rows = read_sheet(excel_path, args.sheet, required=REQUIRED)
        specs = group_rows(rows)
        log.info("Parsed %d row(s) into %d threshold spec(s)", len(rows), len(specs))

        client = prompt_credentials(args.company) if not args.dry_run else None
        if args.dry_run:
            log.info("Mode: DRY RUN - no portal connection, no changes")
        else:
            log.info("Mode: LIVE")
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
            result = process(spec, client, args.dry_run)
            results.append(result)
            level = logging.ERROR if result.action == "failed" else logging.INFO
            log.log(
                level,
                "%-28s %-22s %-18s %-9s %s",
                result.target[:28],
                spec["dataSource"][:22],
                spec["datapoint"][:18],
                result.action,
                result.detail,
            )
    except KeyboardInterrupt:
        log.warning("Interrupted. Writing a report for %d processed spec(s).", len(results))

    return finish(stem, results, EXTRA_COLUMNS)


if __name__ == "__main__":
    sys.exit(main())
