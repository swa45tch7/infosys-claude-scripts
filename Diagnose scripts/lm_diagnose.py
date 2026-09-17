#!/usr/bin/env python3
"""
lm_diagnose.py
==============
Live diagnostic tool for LogicMonitor (LM Envision). Runs read-only checks
against a portal and reports findings with the next action for each.

Built for Infosys delivery teams. Makes no changes to the portal.

Usage
-----
    python3 lm_diagnose.py device ACME-CORE-SW-01
    python3 lm_diagnose.py collector lm-collector-mum-01
    python3 lm_diagnose.py alerting ACME-APP-01 --datasource CPU --datapoint CPUBusyPercent
    python3 lm_diagnose.py credentials ACME-APP-01
    python3 lm_diagnose.py portal
    python3 lm_diagnose.py all ACME-APP-01

    python3 lm_diagnose.py device ACME-APP-01 --out findings.md
    python3 lm_diagnose.py portal --json

Scenarios
---------
    device        Discovery and no-data problems on one resource
    collector     Performance, capacity and health of one Collector
    alerting      Why an alert did not fire, or reached the wrong people
    credentials   Missing or wrong properties and credentials on one resource
    portal        Portal-wide sweep for collectors down and stale resources
    all           device + credentials + alerting for one resource

Findings
--------
    FAIL   Something is broken and is very likely the cause
    WARN   Worth attention, may or may not be the cause
    OK     Checked and healthy
    INFO   Context, no action implied

Exit code is 1 if any FAIL was reported, otherwise 0.

Requires lm_api.py in the same folder.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lm_api import (
    LogicMonitorClient,
    LogicMonitorError,
    configure_logging,
    prompt_credentials,
)

log = logging.getLogger("lm")

# Age after which collected data is treated as stale, in minutes.
STALE_DATA_MINUTES = 30
# Collector uptime below this is treated as a recent restart, in minutes.
RECENT_RESTART_MINUTES = 60

# Credential properties expected for common system.categories values.
# Presence is checked, never the value.
CATEGORY_CREDENTIALS = {
    "snmp": ("snmp.community", "snmp.version"),
    "snmptcpudp": ("snmp.community",),
    "cisco": ("snmp.community",),
    "netapp": ("netapp.user", "netapp.pass"),
    "windows": ("wmi.user", "wmi.pass"),
    "microsoftwindows": ("wmi.user", "wmi.pass"),
    "linuxssh": ("ssh.user",),
    "mysql": ("jdbc.mysql.user",),
    "oracledb": ("jdbc.oracle.user",),
    "esx": ("esx.user", "esx.pass"),
    "vmware": ("esx.user", "esx.pass"),
}

SEVERITY_ORDER = {"FAIL": 0, "WARN": 1, "INFO": 2, "OK": 3}
LEVEL_RANK = {"warn": 1, "warning": 1, "error": 2, "critical": 3}


# --------------------------------------------------------------------------- #
# Findings
# --------------------------------------------------------------------------- #
@dataclass
class Finding:
    severity: str
    check: str
    detail: str
    action: str = ""


@dataclass
class Report:
    scenario: str
    subject: str
    findings: list[Finding] = field(default_factory=list)

    def add(self, severity: str, check: str, detail: str, action: str = "") -> None:
        self.findings.append(Finding(severity, check, detail, action))

    @property
    def failed(self) -> bool:
        return any(finding.severity == "FAIL" for finding in self.findings)


def minutes_since(epoch_value: Any) -> float | None:
    """Convert an epoch timestamp (seconds or milliseconds) to minutes ago."""
    try:
        value = float(epoch_value)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    if value > 1e11:  # milliseconds
        value /= 1000.0
    return (time.time() - value) / 60.0


def describe_age(minutes: float | None) -> str:
    if minutes is None:
        return "unknown"
    if minutes < 60:
        return f"{minutes:.0f} min ago"
    if minutes < 2880:
        return f"{minutes / 60:.1f} hours ago"
    return f"{minutes / 1440:.1f} days ago"


# --------------------------------------------------------------------------- #
# Shared lookups
# --------------------------------------------------------------------------- #
def get_device(client: LogicMonitorClient, identifier: str) -> dict:
    device = client.find_device(identifier)
    return client.request("GET", f"/device/devices/{device['id']}")


def get_device_group_paths(client: LogicMonitorClient, device: dict) -> list[str]:
    raw = device.get("hostGroupIds") or ""
    ids = [part.strip() for part in str(raw).split(",") if part.strip().isdigit()]
    paths: list[str] = []
    for group_id in ids:
        try:
            group = client.request("GET", f"/device/groups/{group_id}")
            path = (group.get("fullPath") or "").strip("/")
            if path:
                paths.append(path)
        except LogicMonitorError:
            continue
    return paths


def get_collector(client: LogicMonitorClient, identifier: str) -> dict:
    if identifier.isdigit():
        return client.request("GET", f"/setting/collectors/{identifier}")

    collectors = client.get_items("/setting/collectors")
    needle = identifier.strip().lower()
    exact = [
        c
        for c in collectors
        if needle in {(c.get("hostname") or "").lower(), (c.get("description") or "").lower()}
    ]
    if len(exact) == 1:
        return client.request("GET", f"/setting/collectors/{exact[0]['id']}")

    partial = [
        c
        for c in collectors
        if needle in (c.get("hostname") or "").lower()
        or needle in (c.get("description") or "").lower()
    ]
    if len(partial) == 1:
        return client.request("GET", f"/setting/collectors/{partial[0]['id']}")
    if len(partial) > 1:
        names = ", ".join((c.get("hostname") or str(c["id"])) for c in partial[:8])
        raise LogicMonitorError(f"'{identifier}' matches several collectors: {names}")
    raise LogicMonitorError(f"Collector not found: {identifier}")


def latest_data_age(client: LogicMonitorClient, device_id: int, hds_id: int, instance_id: int):
    """Return (minutes_since_latest_value, datapoints_all_null) for one instance."""
    try:
        data = client.request(
            "GET",
            f"/device/devices/{device_id}/devicedatasources/{hds_id}"
            f"/instances/{instance_id}/data",
            params={"size": 5},
        )
    except LogicMonitorError:
        return None, None

    times = data.get("time") or []
    values = data.get("values") or []
    if not times:
        return None, None

    age = minutes_since(times[0])
    all_null = True
    for row in values[:3]:
        if isinstance(row, list) and any(cell is not None for cell in row):
            all_null = False
            break
    return age, all_null


# --------------------------------------------------------------------------- #
# Scenario: device discovery / no data
# --------------------------------------------------------------------------- #
def diagnose_device(client: LogicMonitorClient, identifier: str) -> Report:
    report = Report("Device discovery and data collection", identifier)
    device = get_device(client, identifier)
    device_id = device["id"]

    report.add(
        "INFO",
        "Resource",
        f"{device.get('displayName')} (host {device.get('name')}, id {device_id}, "
        f"type {device.get('deviceType')})",
    )

    # -- host status --
    status = (device.get("hostStatus") or "").lower()
    if status == "normal":
        report.add("OK", "Host status", "normal")
    elif status == "dead":
        report.add(
            "FAIL",
            "Host status",
            "dead - LogicMonitor cannot reach the resource",
            "Check network reachability and firewall rules from the Collector to the "
            "resource, then confirm the host or IP in the resource's Info tab is correct.",
        )
    else:
        report.add("WARN", "Host status", status or "unknown", "Review the resource's Info tab.")

    # -- collector binding --
    preferred = device.get("preferredCollectorId")
    current = device.get("currentCollectorId")
    if preferred in (None, 0):
        report.add(
            "FAIL",
            "Collector assignment",
            "No preferred Collector is set",
            "Assign a Collector on the resource's Info tab, or via the onboarding script.",
        )
    else:
        try:
            collector = client.request("GET", f"/setting/collectors/{preferred}")
        except LogicMonitorError:
            collector = {}
        label = collector.get("hostname") or f"id {preferred}"
        if collector.get("isDown"):
            report.add(
                "FAIL",
                "Collector health",
                f"Preferred Collector {label} is DOWN",
                f"Run: python3 lm_diagnose.py collector {preferred}",
            )
        else:
            report.add("OK", "Collector health", f"{label} is up")

        if current and preferred and current != preferred:
            report.add(
                "WARN",
                "Collector failover",
                f"Running on Collector {current}, preferred is {preferred}",
                "The resource has failed over. Confirm the preferred Collector is healthy "
                "before failing back.",
            )

    # -- alerting switch --
    if device.get("disableAlerting"):
        report.add(
            "WARN",
            "Alerting",
            "Alerting is disabled at resource level",
            "Expected for staging or UAT. Otherwise clear it on the resource's Info tab.",
        )

    # -- applied datasources --
    datasources = client.get_items(
        f"/device/devices/{device_id}/devicedatasources",
        params={
            "fields": "id,dataSourceName,dataSourceId,instanceNumber,"
            "monitoringInstanceNumber,stopMonitoring,alertStatus"
        },
    )
    if not datasources:
        categories = get_property(client, device_id, "system.categories")
        report.add(
            "FAIL",
            "DataSource matching",
            "No DataSources are applied to this resource",
            "Nothing will ever be collected. Check system.categories "
            f"(currently {categories or 'not set'}) and the sysOID, then force a "
            "DataSource rematch from the resource's Info tab.",
        )
        return report

    report.add("OK", "DataSource matching", f"{len(datasources)} DataSource(s) applied")

    # -- instances and discovery --
    empty: list[str] = []
    stopped: list[str] = []
    for datasource in datasources:
        name = datasource.get("dataSourceName") or str(datasource["id"])
        if datasource.get("stopMonitoring"):
            stopped.append(name)
            continue
        if not datasource.get("instanceNumber"):
            empty.append(name)

    if empty:
        assigned_age = minutes_since(device.get("createdOn"))
        recent = assigned_age is not None and assigned_age < 60
        report.add(
            "WARN" if recent else "FAIL",
            "Active Discovery",
            f"{len(empty)} DataSource(s) have zero instances: {', '.join(empty[:8])}",
            "Active Discovery has not returned anything. If the resource was added in the "
            "last hour this may simply be pending. Otherwise check the credential "
            "properties for that collection method and run Active Discovery manually "
            "from the resource's Instances tab."
            if recent
            else "Check the credential properties for that collection method "
            "(snmp.community, wmi.user, ssh.user and so on), confirm the protocol is "
            "permitted from the Collector, then run Active Discovery manually.",
        )
    if stopped:
        report.add(
            "WARN",
            "Monitoring disabled",
            f"{len(stopped)} DataSource(s) have monitoring stopped: {', '.join(stopped[:8])}",
            "Re-enable from the resource's Instances tab if this was not deliberate.",
        )

    # -- data freshness on the largest DataSource --
    populated = [d for d in datasources if d.get("instanceNumber") and not d.get("stopMonitoring")]
    if not populated:
        return report

    sample = max(populated, key=lambda d: d.get("instanceNumber", 0))
    instances = client.get_items(
        f"/device/devices/{device_id}/devicedatasources/{sample['id']}/instances",
        params={"fields": "id,name,stopMonitoring"},
    )
    active = [i for i in instances if not i.get("stopMonitoring")]
    if not active:
        report.add(
            "WARN",
            "Data freshness",
            f"Every instance of {sample.get('dataSourceName')} has monitoring stopped",
            "Re-enable at least one instance to confirm collection works.",
        )
        return report

    checked = active[:3]
    stale = 0
    null_only = 0
    ages: list[str] = []
    for instance in checked:
        age, all_null = latest_data_age(client, device_id, sample["id"], instance["id"])
        ages.append(f"{instance.get('name')}: {describe_age(age)}")
        if age is None:
            stale += 1
        elif age > STALE_DATA_MINUTES:
            stale += 1
        if all_null:
            null_only += 1

    label = f"{sample.get('dataSourceName')} ({len(checked)} instance(s) sampled)"
    if stale == len(checked):
        report.add(
            "FAIL",
            "Data freshness",
            f"No recent data on {label}. Latest: {'; '.join(ages)}",
            "The DataSource is applied and discovered but is not collecting. Check the "
            "collection method's credentials and the Collector's own health, then use "
            "Poll Now on the instance to see the live collection error.",
        )
    elif stale:
        report.add(
            "WARN",
            "Data freshness",
            f"{stale} of {len(checked)} sampled instances are stale on {label}. "
            f"{'; '.join(ages)}",
            "Partial collection usually points at per-instance permissions or a "
            "Collector nearing capacity.",
        )
    else:
        report.add("OK", "Data freshness", f"{label} collecting normally. {'; '.join(ages)}")

    if null_only:
        report.add(
            "WARN",
            "Datapoint values",
            f"{null_only} sampled instance(s) return timestamps but no values",
            "The collection ran and returned nothing usable. Check the datapoint's "
            "collection definition and the target's response using Poll Now.",
        )

    return report


# --------------------------------------------------------------------------- #
# Scenario: collector performance
# --------------------------------------------------------------------------- #
# Collector self-monitoring datapoints that indicate saturation. LogicMonitor
# documents the task queue and unavailable scheduled tasks as the leading
# indicators of a Collector approaching its configured capacity.
COLLECTOR_SIGNALS = {
    "unavailablescheduletaskrate": (
        "Tasks that could not be scheduled",
        "Above zero means the Collector cannot keep up. Increase the Collector size "
        "first, then tune the threadpool, then split the load across Collectors.",
    ),
    "taskqueue": (
        "Task queue depth",
        "A growing queue means tasks are waiting to be scheduled. This is the leading "
        "indicator of a Collector reaching capacity.",
    ),
    "scheduledtaskqueue": (
        "Scheduled task queue depth",
        "A growing queue means tasks are waiting to be scheduled.",
    ),
}


def diagnose_collector(client: LogicMonitorClient, identifier: str) -> Report:
    report = Report("Collector performance and capacity", identifier)
    collector = get_collector(client, identifier)
    collector_id = collector["id"]
    label = collector.get("hostname") or f"id {collector_id}"

    report.add(
        "INFO",
        "Collector",
        f"{label} (id {collector_id}, size {collector.get('collectorSize') or 'unknown'}, "
        f"version {collector.get('collectorVersion') or 'unknown'}, "
        f"platform {collector.get('platform') or 'unknown'}, "
        f"group {collector.get('collectorGroupName') or 'none'})",
    )

    # -- up or down --
    if collector.get("isDown"):
        report.add(
            "FAIL",
            "Status",
            "Collector is DOWN",
            "Every resource on this Collector has stopped collecting. Check the "
            "Collector service on the host, then its network path to the portal.",
        )
    else:
        report.add("OK", "Status", "Collector is up")

    # -- uptime and watchdog --
    uptime_seconds = collector.get("upTime")
    if isinstance(uptime_seconds, (int, float)) and uptime_seconds > 0:
        uptime_minutes = uptime_seconds / 60.0
        if uptime_minutes < RECENT_RESTART_MINUTES:
            report.add(
                "WARN",
                "Uptime",
                f"Restarted {uptime_minutes:.0f} minutes ago",
                "A recent restart explains gaps in collected data. Check the Collector "
                "Events tab for the restart reason.",
            )
        else:
            report.add("OK", "Uptime", f"Up for {uptime_minutes / 1440:.1f} days")

    watchdog_age = minutes_since(collector.get("watchdogUpdatedOn"))
    if watchdog_age is not None and watchdog_age > 15:
        report.add(
            "WARN",
            "Watchdog",
            f"Watchdog last reported {describe_age(watchdog_age)}",
            "A stale watchdog suggests the Collector host is under pressure or has lost "
            "connectivity to the portal.",
        )

    # -- load, compared to peers rather than to a fixed number --
    hosts = collector.get("numberOfHosts")
    instances = collector.get("numberOfInstances")
    report.add(
        "INFO",
        "Load",
        f"{hosts if hosts is not None else 'unknown'} resource(s), "
        f"{instances if instances is not None else 'unknown'} instance(s)",
    )

    peers = client.get_items(
        "/setting/collectors",
        params={"fields": "id,hostname,collectorSize,numberOfHosts,numberOfInstances,isDown"},
    )
    same_size = [
        p
        for p in peers
        if p.get("collectorSize") == collector.get("collectorSize")
        and p["id"] != collector_id
        and isinstance(p.get("numberOfInstances"), int)
    ]
    if same_size and isinstance(instances, int):
        counts = sorted(p["numberOfInstances"] for p in same_size)
        median = counts[len(counts) // 2]
        if median > 0 and instances > median * 2:
            report.add(
                "WARN",
                "Load vs peers",
                f"{instances} instances against a median of {median} across "
                f"{len(same_size)} other {collector.get('collectorSize')} Collector(s)",
                "This Collector carries a disproportionate share of the load. Rebalance "
                "resources or increase its size.",
            )
        else:
            report.add(
                "OK",
                "Load vs peers",
                f"{instances} instances, in line with the median of {median} for its size",
            )
    elif isinstance(instances, int):
        report.add(
            "INFO",
            "Load vs peers",
            "No other Collector of this size to compare against",
            "LogicMonitor's documented capacity depends on the collection methods in use, "
            "so compare against the Collector Capacity guidance for your traffic profile.",
        )

    # -- failover --
    if collector.get("backupAgentId"):
        report.add("OK", "Failover", f"Failover Collector configured (id {collector['backupAgentId']})")
    else:
        report.add(
            "WARN",
            "Failover",
            "No failover Collector configured",
            "Every resource on this Collector loses monitoring if it goes down. Configure "
            "a failover Collector or use an Auto-Balanced Collector Group.",
        )

    # -- the Collector's own monitored metrics --
    collector_device_name = collector.get("hostname")
    saturation_checked = False
    if collector_device_name:
        try:
            device = client.find_device(collector_device_name)
            saturation_checked = check_collector_saturation(client, device["id"], report)
        except LogicMonitorError:
            pass

    if not saturation_checked:
        report.add(
            "WARN",
            "Collector self-monitoring",
            "Could not read this Collector's own performance metrics",
            "The Collector host does not appear to be in monitoring. Enable Collector "
            "monitoring so task queue, unavailable scheduled tasks and JVM heap are "
            "tracked - these are the metrics that show saturation.",
        )

    return report


def check_collector_saturation(
    client: LogicMonitorClient, device_id: int, report: Report
) -> bool:
    """Read the Collector's own DataSource values for saturation indicators."""
    datasources = client.get_items(
        f"/device/devices/{device_id}/devicedatasources",
        params={"fields": "id,dataSourceName,instanceNumber"},
    )
    relevant = [
        d
        for d in datasources
        if "collector" in (d.get("dataSourceName") or "").lower() and d.get("instanceNumber")
    ]
    if not relevant:
        return False

    reported = False
    for datasource in relevant:
        instances = client.get_items(
            f"/device/devices/{device_id}/devicedatasources/{datasource['id']}/instances",
            params={"fields": "id,name,stopMonitoring"},
        )
        for instance in [i for i in instances if not i.get("stopMonitoring")][:4]:
            try:
                data = client.request(
                    "GET",
                    f"/device/devices/{device_id}/devicedatasources/{datasource['id']}"
                    f"/instances/{instance['id']}/data",
                    params={"size": 3},
                )
            except LogicMonitorError:
                continue

            names = [str(n) for n in (data.get("dataPoints") or [])]
            rows = data.get("values") or []
            if not names or not rows:
                continue
            latest = rows[0] if isinstance(rows[0], list) else []

            for index, datapoint in enumerate(names):
                key = datapoint.lower().replace("_", "").replace(" ", "")
                signal = next(
                    (COLLECTOR_SIGNALS[k] for k in COLLECTOR_SIGNALS if k in key), None
                )
                if signal is None or index >= len(latest):
                    continue
                value = latest[index]
                if value is None:
                    continue
                reported = True
                title, action = signal
                if isinstance(value, (int, float)) and value > 0:
                    report.add(
                        "FAIL" if value > 1 else "WARN",
                        f"{title} ({datapoint})",
                        f"{value} on {instance.get('name')}",
                        action,
                    )
                else:
                    report.add("OK", f"{title} ({datapoint})", f"{value} on {instance.get('name')}")

            for index, datapoint in enumerate(names):
                key = datapoint.lower()
                if "heap" not in key or index >= len(latest):
                    continue
                value = latest[index]
                if not isinstance(value, (int, float)):
                    continue
                reported = True
                if "percent" in key or "used" in key and value <= 100:
                    if value > 85:
                        report.add(
                            "FAIL",
                            f"JVM heap ({datapoint})",
                            f"{value} on {instance.get('name')}",
                            "Heap is close to its limit. Increase the Collector size, or "
                            "raise the heap limit if the host has memory available.",
                        )
                    elif value > 70:
                        report.add(
                            "WARN",
                            f"JVM heap ({datapoint})",
                            f"{value} on {instance.get('name')}",
                            "Heap is trending high. Watch it before adding more resources "
                            "to this Collector.",
                        )
                    else:
                        report.add("OK", f"JVM heap ({datapoint})", str(value))

    return reported


# --------------------------------------------------------------------------- #
# Scenario: alerting not firing or misrouted
# --------------------------------------------------------------------------- #
def glob_match(pattern: str, value: str) -> bool:
    pattern = (pattern or "*").strip() or "*"
    if pattern == "*":
        return True
    return fnmatch.fnmatch((value or "").lower(), pattern.lower())


def group_match(rule_groups: list[str], device_paths: list[str]) -> bool:
    if not rule_groups:
        return True
    for rule_path in rule_groups:
        target = (rule_path or "").strip("/").lower()
        for device_path in device_paths:
            path = device_path.lower()
            if path == target or path.startswith(target + "/"):
                return True
    return False


def diagnose_alerting(
    client: LogicMonitorClient,
    identifier: str,
    datasource: str,
    datapoint: str,
    level: str,
) -> Report:
    report = Report("Alert routing", f"{identifier} / {datasource} / {datapoint} / {level}")
    device = get_device(client, identifier)
    device_id = device["id"]
    display_name = device.get("displayName") or ""
    paths = get_device_group_paths(client, device)

    report.add("INFO", "Resource", f"{display_name} (id {device_id})")
    report.add("INFO", "Resource groups", ", ".join(paths) or "none resolved")

    # -- suppression at resource level --
    if device.get("disableAlerting"):
        report.add(
            "FAIL",
            "Resource alerting",
            "Alerting is disabled on the resource",
            "No alert can be raised regardless of thresholds or rules. Clear it on the "
            "resource's Info tab.",
        )
    else:
        report.add("OK", "Resource alerting", "Enabled")

    # -- active SDT --
    try:
        sdts = client.get_items(
            "/sdt/sdts", params={"filter": f'deviceId:{device_id}', "fields": "id,type,endDateTime,comment"}
        )
    except LogicMonitorError:
        sdts = []
    if sdts:
        report.add(
            "FAIL",
            "Scheduled downtime",
            f"{len(sdts)} SDT entr(y/ies) cover this resource",
            "Notifications are suppressed while an SDT is active. Check the resource's "
            "SDT tab.",
        )
    else:
        report.add("OK", "Scheduled downtime", "No SDT found for this resource")

    # -- threshold present --
    if datasource != "*":
        try:
            device_datasource = client.find_device_datasource(device_id, datasource)
            check_threshold_present(client, device_id, device_datasource, datapoint, report)
        except LogicMonitorError as exc:
            report.add("FAIL", "Threshold", str(exc), "Correct the DataSource name and re-run.")

    # -- rule matching --
    rules = client.get_items(
        "/setting/alert/rules",
        params={
            "fields": "id,name,priority,levelStr,datasource,instance,datapoint,"
            "devices,deviceGroups,escalatingChainId,escalationInterval,suppressAlertClear"
        },
    )
    rules.sort(key=lambda r: r.get("priority") or 10**6)

    matches: list[dict] = []
    for rule in rules:
        if not glob_match(rule.get("datasource", "*"), datasource):
            continue
        if not glob_match(rule.get("datapoint", "*"), datapoint):
            continue

        rule_level = (rule.get("levelStr") or "All").lower()
        if rule_level not in {"all", ""}:
            if LEVEL_RANK.get(rule_level, 0) != LEVEL_RANK.get(level.lower(), 0):
                continue

        devices = rule.get("devices") or []
        groups = rule.get("deviceGroups") or []
        if devices:
            if not any(glob_match(d, display_name) for d in devices):
                continue
        elif not group_match(groups, paths):
            continue

        matches.append(rule)

    if not matches:
        report.add(
            "FAIL",
            "Alert rule match",
            "No alert rule matches this resource, DataSource, datapoint and severity",
            "The alert would be raised in the portal but no notification would be sent. "
            "Add a rule covering this scope, or widen an existing one.",
        )
        return report

    winner = matches[0]
    report.add(
        "OK",
        "Alert rule match",
        f"'{winner.get('name')}' (priority {winner.get('priority')}) would route this alert",
    )

    if len(matches) > 1:
        others = ", ".join(
            f"{r.get('name')} (priority {r.get('priority')})" for r in matches[1:6]
        )
        report.add(
            "WARN" if len(matches) > 1 else "INFO",
            "Rule precedence",
            f"{len(matches)} rules match. Only the first applies. Also matching: {others}",
            "If notifications are reaching the wrong team, lower the priority number of "
            "the rule you want to win.",
        )

    if winner.get("suppressAlertClear"):
        report.add(
            "WARN",
            "Clear notifications",
            "The matching rule suppresses clear notifications",
            "Recipients will not be told when the alert clears.",
        )

    interval = winner.get("escalationInterval")
    if interval == 0:
        report.add(
            "WARN",
            "Escalation interval",
            "Escalation interval is 0 on the matching rule",
            "Only the first stage of the chain is ever notified. Set a non-zero interval "
            "if later stages are expected to be reached.",
        )

    # -- chain trace --
    chain_id = winner.get("escalatingChainId")
    if not chain_id:
        report.add(
            "FAIL",
            "Escalation chain",
            "The matching rule has no escalation chain",
            "Notifications have nowhere to go. Bind a chain to the rule.",
        )
        return report

    try:
        chain = client.request("GET", f"/setting/alert/chains/{chain_id}")
    except LogicMonitorError as exc:
        report.add("FAIL", "Escalation chain", f"Could not read chain {chain_id}: {exc}")
        return report

    destinations = chain.get("destinations") or []
    stages = destinations[0].get("stages") if destinations else []
    if not stages:
        report.add(
            "FAIL",
            "Escalation chain",
            f"Chain '{chain.get('name')}' has no stages",
            "A chain must contain at least one stage with a recipient.",
        )
        return report

    report.add(
        "OK",
        "Escalation chain",
        f"'{chain.get('name')}' with {len(stages)} stage(s)",
    )
    for number, stage in enumerate(stages, start=1):
        if not stage:
            report.add(
                "WARN",
                f"Chain stage {number}",
                "Empty stage",
                "An empty stage only delays notification. Intentional in some designs, "
                "an accident in most.",
            )
            continue
        recipients = ", ".join(
            f"{entry.get('addr')} ({entry.get('method') or entry.get('type')})"
            for entry in stage
        )
        report.add("INFO", f"Chain stage {number}", recipients)

    if chain.get("enableThrottling"):
        report.add(
            "INFO",
            "Throttling",
            f"Throttling on: {chain.get('throttlingAlerts')} alert(s) per "
            f"{chain.get('throttlingPeriod')} minute(s)",
            "Notifications beyond this rate are held back, which can look like alerts "
            "not firing during a storm.",
        )

    return report


def check_threshold_present(
    client: LogicMonitorClient,
    device_id: int,
    device_datasource: dict,
    datapoint: str,
    report: Report,
) -> None:
    instances = client.list_instances(device_id, device_datasource["id"])
    active = [i for i in instances if not i.get("stopMonitoring")]
    if not active:
        report.add(
            "FAIL",
            "Threshold",
            "No active instances on this DataSource",
            "Nothing can alert until an instance exists and is monitored.",
        )
        return

    instance = active[0]
    settings = client.get_items(
        f"/device/devices/{device_id}/devicedatasources/{device_datasource['id']}"
        f"/instances/{instance['id']}/alertsettings"
    )
    match = next(
        (s for s in settings if (s.get("dataPointName") or "").lower() == datapoint.lower()),
        None,
    )
    if match is None:
        names = ", ".join(sorted((s.get("dataPointName") or "") for s in settings)[:12])
        report.add(
            "FAIL",
            "Threshold",
            f"Datapoint '{datapoint}' not found on {device_datasource.get('dataSourceName')}",
            f"Datapoints available: {names or 'none'}",
        )
        return

    expression = (match.get("alertExpr") or "").strip()
    if not expression:
        report.add(
            "FAIL",
            "Threshold",
            f"No threshold set on {datapoint} (checked instance {instance.get('name')})",
            "Without a threshold no alert is ever raised, so alert rules never come into "
            "play. Set one with lm_threshold_apply.py.",
        )
    else:
        report.add(
            "OK",
            "Threshold",
            f"{datapoint} threshold is '{expression}' on instance {instance.get('name')}",
        )

    if match.get("disableAlerting"):
        report.add(
            "FAIL",
            "Datapoint alerting",
            f"Alerting is disabled on {datapoint}",
            "Clear disableAlerting for this datapoint.",
        )


# --------------------------------------------------------------------------- #
# Scenario: credentials and properties
# --------------------------------------------------------------------------- #
def get_property(client: LogicMonitorClient, device_id: int, name: str) -> str:
    properties = client.get_items(
        f"/device/devices/{device_id}/properties", params={"fields": "name,value,type"}
    )
    for entry in properties:
        if (entry.get("name") or "").lower() == name.lower():
            return entry.get("value") or ""
    return ""


def diagnose_credentials(client: LogicMonitorClient, identifier: str) -> Report:
    report = Report("Credentials and properties", identifier)
    device = get_device(client, identifier)
    device_id = device["id"]

    properties = client.get_items(
        f"/device/devices/{device_id}/properties", params={"fields": "name,value,type"}
    )
    by_name = {(p.get("name") or "").lower(): p for p in properties}
    report.add("INFO", "Properties", f"{len(properties)} property(ies) visible on this resource")

    # -- categories drive DataSource matching --
    categories_raw = (by_name.get("system.categories", {}).get("value") or "").strip()
    if not categories_raw:
        report.add(
            "WARN",
            "system.categories",
            "Not set",
            "Many DataSources match on system.categories. Without it, monitoring may be "
            "thinner than expected. Set it on the resource or inherit it from the group.",
        )
        categories: list[str] = []
    else:
        categories = [c.strip() for c in categories_raw.split(",") if c.strip()]
        report.add("OK", "system.categories", categories_raw)

    sysoid = (by_name.get("system.sysoid", {}).get("value") or "").strip()
    if sysoid:
        report.add("OK", "system.sysoid", sysoid)
    elif device.get("deviceType") == 0:
        report.add(
            "WARN",
            "system.sysoid",
            "Not set",
            "SNMP-based DataSource matching relies on the sysOID. Its absence usually "
            "means SNMP is not responding - confirm snmp.community and UDP 161 access.",
        )

    # -- expected credentials per category --
    expected: dict[str, str] = {}
    for category in categories:
        key = category.lower().replace("_", "").replace("-", "")
        for pattern, props in CATEGORY_CREDENTIALS.items():
            if pattern in key:
                for prop in props:
                    expected[prop] = category

    if expected:
        missing = [prop for prop in expected if prop.lower() not in by_name]
        present = [prop for prop in expected if prop.lower() in by_name]
        if present:
            report.add(
                "OK",
                "Credential properties present",
                ", ".join(sorted(present)),
            )
        if missing:
            report.add(
                "FAIL",
                "Credential properties missing",
                ", ".join(f"{prop} (expected for category {expected[prop]})" for prop in sorted(missing)),
                "Set these on the resource or, better, on the resource group so the whole "
                "customer estate inherits them. Missing credentials are the usual cause of "
                "Active Discovery returning nothing.",
            )
    else:
        report.add(
            "INFO",
            "Credential properties",
            "No credential expectations derived from the categories on this resource",
        )

    # -- local overrides worth knowing about --
    local = [
        p.get("name")
        for p in properties
        if (p.get("type") or "").lower() in {"custom", "system"}
        and (p.get("name") or "").lower().split(".")[0]
        in {"snmp", "wmi", "ssh", "jdbc", "esx", "netapp"}
    ]
    if local:
        report.add(
            "INFO",
            "Local credential overrides",
            ", ".join(sorted(set(n for n in local if n))[:10]),
            "A local override wins over the group value. Confirm it is deliberate, "
            "because a stale local credential survives every group-level fix.",
        )

    # -- properties whose value looks empty --
    blank = [
        p.get("name")
        for p in properties
        if not (p.get("value") or "").strip()
        and (p.get("name") or "").lower().split(".")[0]
        in {"snmp", "wmi", "ssh", "jdbc", "esx", "netapp"}
    ]
    if blank:
        report.add(
            "WARN",
            "Blank credential properties",
            ", ".join(sorted(set(n for n in blank if n))[:10]),
            "A property that exists with an empty value blocks inheritance from the group "
            "without providing a value of its own. Delete it or fill it in.",
        )

    return report


# --------------------------------------------------------------------------- #
# Scenario: portal sweep
# --------------------------------------------------------------------------- #
def diagnose_portal(client: LogicMonitorClient) -> Report:
    report = Report("Portal sweep", client.company)

    collectors = client.get_items(
        "/setting/collectors",
        params={
            "fields": "id,hostname,description,isDown,collectorSize,numberOfHosts,"
            "numberOfInstances,backupAgentId,collectorGroupName,collectorVersion"
        },
    )
    report.add("INFO", "Collectors", f"{len(collectors)} Collector(s) in the portal")

    down = [c for c in collectors if c.get("isDown")]
    if down:
        affected = sum(c.get("numberOfHosts") or 0 for c in down)
        report.add(
            "FAIL",
            "Collectors down",
            f"{len(down)} down: "
            + ", ".join((c.get("hostname") or str(c["id"])) for c in down[:8])
            + f" - affecting about {affected} resource(s)",
            "Start with these. Every resource on a down Collector has stopped collecting.",
        )
    else:
        report.add("OK", "Collectors down", "None")

    no_failover = [
        c for c in collectors if not c.get("backupAgentId") and (c.get("numberOfHosts") or 0) > 0
    ]
    if no_failover:
        report.add(
            "WARN",
            "Collectors without failover",
            f"{len(no_failover)}: "
            + ", ".join((c.get("hostname") or str(c["id"])) for c in no_failover[:8]),
            "Configure a failover Collector or an Auto-Balanced Collector Group.",
        )

    versions = {c.get("collectorVersion") for c in collectors if c.get("collectorVersion")}
    if len(versions) > 3:
        report.add(
            "WARN",
            "Collector versions",
            f"{len(versions)} different versions in use",
            "A wide version spread complicates support. Plan a staged upgrade.",
        )

    # -- dead resources --
    dead = client.get_items(
        "/device/devices",
        params={"filter": 'hostStatus:"dead"', "fields": "id,displayName,preferredCollectorId"},
    )
    if dead:
        report.add(
            "FAIL",
            "Dead resources",
            f"{len(dead)}: " + ", ".join((d.get("displayName") or str(d["id"])) for d in dead[:10]),
            "Run: python3 lm_diagnose.py device <name> on each.",
        )
    else:
        report.add("OK", "Dead resources", "None")

    # -- resources with alerting disabled --
    muted = client.get_items(
        "/device/devices",
        params={"filter": "disableAlerting:true", "fields": "id,displayName"},
    )
    if muted:
        report.add(
            "WARN",
            "Resources with alerting disabled",
            f"{len(muted)}: " + ", ".join((d.get("displayName") or str(d["id"])) for d in muted[:10]),
            "Expected for staging. Confirm none of these are production.",
        )

    # -- alert rules sanity --
    rules = client.get_items(
        "/setting/alert/rules",
        params={"fields": "id,name,priority,escalatingChainId,levelStr"},
    )
    report.add("INFO", "Alert rules", f"{len(rules)} rule(s) configured")

    orphaned = [r for r in rules if not r.get("escalatingChainId")]
    if orphaned:
        report.add(
            "FAIL",
            "Alert rules without a chain",
            ", ".join((r.get("name") or str(r["id"])) for r in orphaned[:8]),
            "These rules match alerts but send nothing. Bind an escalation chain.",
        )

    priorities: dict[int, list[str]] = {}
    for rule in rules:
        priority = rule.get("priority")
        if isinstance(priority, int):
            priorities.setdefault(priority, []).append(rule.get("name") or str(rule["id"]))
    clashes = {p: names for p, names in priorities.items() if len(names) > 1}
    if clashes:
        sample = "; ".join(f"{p}: {', '.join(names[:3])}" for p, names in list(clashes.items())[:4])
        report.add(
            "WARN",
            "Duplicate rule priorities",
            f"{len(clashes)} priority value(s) shared by more than one rule. {sample}",
            "Routing order between rules at the same priority is not guaranteed. Give "
            "each rule a distinct priority.",
        )

    # -- chains with no recipients --
    chains = client.get_items("/setting/alert/chains", params={"fields": "id,name,destinations"})
    empty_chains = []
    for chain in chains:
        destinations = chain.get("destinations") or []
        stages = destinations[0].get("stages") if destinations else []
        if not stages or not any(stage for stage in stages):
            empty_chains.append(chain.get("name") or str(chain["id"]))
    if empty_chains:
        report.add(
            "FAIL",
            "Escalation chains with no recipients",
            ", ".join(empty_chains[:8]),
            "Any rule bound to these chains notifies nobody.",
        )
    else:
        report.add("OK", "Escalation chains", f"{len(chains)} chain(s), all with recipients")

    return report


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
SYMBOLS = {"FAIL": "[FAIL]", "WARN": "[WARN]", "OK": "[ OK ]", "INFO": "[INFO]"}


def print_report(report: Report) -> None:
    log.info("")
    log.info("=" * 78)
    log.info("%s", report.scenario.upper())
    log.info("Subject: %s", report.subject)
    log.info("=" * 78)

    ordered = sorted(report.findings, key=lambda f: SEVERITY_ORDER.get(f.severity, 9))
    for finding in ordered:
        log.info("%s %-34s %s", SYMBOLS.get(finding.severity, ""), finding.check, finding.detail)
        if finding.action:
            for line in wrap(finding.action, 68):
                log.info("       -> %s", line)

    tally: dict[str, int] = {}
    for finding in report.findings:
        tally[finding.severity] = tally.get(finding.severity, 0) + 1
    log.info("-" * 78)
    log.info(
        "%s",
        "  ".join(
            f"{severity}={tally[severity]}"
            for severity in ("FAIL", "WARN", "OK", "INFO")
            if severity in tally
        ),
    )


def wrap(text: str, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        if sum(len(w) + 1 for w in current) + len(word) > width and current:
            lines.append(" ".join(current))
            current = []
        current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines


def to_markdown(reports: list[Report]) -> str:
    lines = ["# LogicMonitor diagnostic findings", ""]
    for report in reports:
        lines += [f"## {report.scenario}", "", f"**Subject:** {report.subject}", ""]
        lines += ["| Result | Check | Detail | Recommended action |", "|---|---|---|---|"]
        ordered = sorted(report.findings, key=lambda f: SEVERITY_ORDER.get(f.severity, 9))
        for finding in ordered:
            check = finding.check.replace("|", "\\|")
            detail = finding.detail.replace("|", "\\|")
            action = finding.action.replace("|", "\\|") or "-"
            lines.append(f"| {finding.severity} | {check} | {detail} | {action} |")
        lines.append("")
    return "\n".join(lines)


def to_json(reports: list[Report]) -> str:
    return json.dumps(
        [
            {
                "scenario": report.scenario,
                "subject": report.subject,
                "findings": [
                    {
                        "severity": finding.severity,
                        "check": finding.check,
                        "detail": finding.detail,
                        "action": finding.action,
                    }
                    for finding in report.findings
                ],
            }
            for report in reports
        ],
        indent=2,
    )


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only diagnostics for LogicMonitor. Makes no changes.",
    )
    parser.add_argument(
        "scenario",
        choices=["device", "collector", "alerting", "credentials", "portal", "all"],
        help="Which diagnostic to run",
    )
    parser.add_argument(
        "subject",
        nargs="?",
        help="Resource display name, or Collector hostname / ID. Not needed for 'portal'.",
    )
    parser.add_argument("--datasource", default="*", help="alerting: DataSource name")
    parser.add_argument("--datapoint", default="*", help="alerting: datapoint name")
    parser.add_argument(
        "--level",
        default="critical",
        choices=["warning", "error", "critical"],
        help="alerting: severity to simulate (default critical)",
    )
    parser.add_argument("--company", help="Portal name, e.g. infosys (skips the prompt)")
    parser.add_argument("--out", help="Write findings to a Markdown file")
    parser.add_argument("--json", action="store_true", help="Print findings as JSON")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.scenario != "portal" and not args.subject:
        print(f"The '{args.scenario}' scenario needs a subject.", file=sys.stderr)
        return 2

    configure_logging(Path("lm_diagnose.log"), args.verbose)
    log.info("LogicMonitor diagnostics (read-only)")

    try:
        client = prompt_credentials(args.company)
    except (LogicMonitorError, KeyboardInterrupt) as exc:
        log.error("%s", exc or "Cancelled")
        return 1

    reports: list[Report] = []
    try:
        if args.scenario == "device":
            reports.append(diagnose_device(client, args.subject))
        elif args.scenario == "collector":
            reports.append(diagnose_collector(client, args.subject))
        elif args.scenario == "credentials":
            reports.append(diagnose_credentials(client, args.subject))
        elif args.scenario == "alerting":
            reports.append(
                diagnose_alerting(
                    client, args.subject, args.datasource, args.datapoint, args.level
                )
            )
        elif args.scenario == "portal":
            reports.append(diagnose_portal(client))
        elif args.scenario == "all":
            reports.append(diagnose_device(client, args.subject))
            reports.append(diagnose_credentials(client, args.subject))
            reports.append(
                diagnose_alerting(
                    client, args.subject, args.datasource, args.datapoint, args.level
                )
            )
    except LogicMonitorError as exc:
        log.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        log.warning("Interrupted.")
        return 130

    if args.json:
        print(to_json(reports))
    else:
        for report in reports:
            print_report(report)

    if args.out:
        out_path = Path(args.out).expanduser().resolve()
        out_path.write_text(to_markdown(reports), encoding="utf-8")
        log.info("")
        log.info("Findings written to %s", out_path)

    return 1 if any(report.failed for report in reports) else 0


if __name__ == "__main__":
    sys.exit(main())
