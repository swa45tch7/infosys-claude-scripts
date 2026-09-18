#!/usr/bin/env python3
"""Read-only portal health and configuration-deviation engine.

Compares a LogicMonitor portal against standards.json. Makes no changes.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from lm_api import LmClient

HERE = Path(__file__).resolve().parent
STANDARDS_PATH = HERE / "standards.json"

SEV_ORDER = {"critical": 0, "warn": 1, "info": 2, "ok": 3}
ALERT_SEVERITY = {4: "critical", 3: "error", 2: "warn", 1: "info"}

DEVICE_FIELDS = (
    "id,name,displayName,hostStatus,alertStatus,sdtStatus,deviceType,"
    "preferredCollectorId,currentCollectorId,autoPropsUpdatedOn,createdOn,"
    "updatedOn,hostGroupIds,disableAlerting,link"
)
DEVICE_PROP_FIELDS = "id,displayName,customProperties,inheritedProperties,systemProperties"
COLLECTOR_FIELDS = (
    "id,description,hostname,isDown,collectorVersion,build,ea,platform,"
    "collectorSize,numberOfHosts,numberOfInstances,numberOfWebsites,"
    "backupAgentId,enableFailBack,collectorGroupId,collectorGroupName,uptime"
)
ALERT_FIELDS = (
    "id,severity,acked,cleared,startEpoch,endEpoch,monitorObjectId,"
    "monitorObjectName,resourceTemplateName,dataPointName,instanceName,sdted,ackedBy"
)
RULE_FIELDS = (
    "id,name,priority,levelStr,escalatingChainId,datasource,devices,deviceGroups,"
    "escalationInterval,suppressAlertClear"
)


def load_standards(path: Path | None = None) -> dict[str, Any]:
    target = path or STANDARDS_PATH
    return json.loads(target.read_text(encoding="utf-8"))


@dataclass
class Finding:
    check_id: str
    severity: str
    subject: str
    finding: str
    standard: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        row = {
            "check_id": self.check_id,
            "severity": self.severity,
            "subject": self.subject,
            "finding": self.finding,
            "standard": self.standard,
        }
        row.update(self.details)
        return row


@dataclass
class CheckResult:
    check_id: str
    name: str
    suite: str
    findings: list[Finding]
    summary: dict[str, Any]
    notes: str = ""

    def counts(self) -> dict[str, int]:
        out = {"critical": 0, "warn": 0, "ok": 0, "info": 0}
        for item in self.findings:
            out[item.severity] = out.get(item.severity, 0) + 1
        return out

    def sort(self) -> None:
        self.findings.sort(key=lambda f: SEV_ORDER.get(f.severity, 9))


@dataclass
class PortalSnapshot:
    devices: list[dict[str, Any]]
    device_properties: list[dict[str, Any]]
    collectors: list[dict[str, Any]]
    alerts: list[dict[str, Any]]
    alert_rules: list[dict[str, Any]]
    chains: list[dict[str, Any]]
    sdts: list[dict[str, Any]]
    tokens: list[dict[str, Any]]
    api_ok: bool = True
    portal: str = ""


def fetch_snapshot(client: LmClient, standards: dict[str, Any]) -> PortalSnapshot:
    since = int(time.time()) - int(standards["alert_window_hours"]) * 3600
    devices = client.collect("/device/devices", {"fields": DEVICE_FIELDS})
    device_properties = client.collect("/device/devices", {"fields": DEVICE_PROP_FIELDS})
    collectors = client.collect(
        "/setting/collector/collectors", {"fields": COLLECTOR_FIELDS}
    )
    alerts = client.collect(
        "/alert/alerts",
        {
            "fields": ALERT_FIELDS,
            "filter": f"startEpoch>:{since}",
            "needMessage": "false",
        },
        max_items=int(standards["alert_max_records"]),
    )
    alert_rules = client.collect("/setting/alert/rules", {"fields": RULE_FIELDS})
    chains = client.collect("/setting/alert/chains")
    sdts = client.collect("/sdt/sdts")
    try:
        tokens = client.collect("/setting/admins/apitokens")
    except Exception:
        tokens = []
    return PortalSnapshot(
        devices=devices,
        device_properties=device_properties,
        collectors=collectors,
        alerts=alerts,
        alert_rules=alert_rules,
        chains=chains,
        sdts=sdts,
        tokens=tokens,
        api_ok=True,
        portal=f"{client.config.account}.{client.config.domain}",
    )


def _age_days(epoch: Any, now: float) -> float | None:
    if not epoch:
        return None
    try:
        value = float(epoch)
    except (TypeError, ValueError):
        return None
    if value > 10_000_000_000:
        value = value / 1000
    return round((now - value) / 86400, 1)


def _age_minutes(epoch: Any, now: float) -> float | None:
    days = _age_days(epoch, now)
    if days is None:
        return None
    return round(days * 1440, 1)


def _pct(part: float, whole: float) -> float | None:
    if not whole:
        return None
    return round(part / whole * 100, 1)


def _version_tuple(version: Any) -> tuple[int, int]:
    try:
        raw = str(version or "0").strip()
        if "." in raw:
            parts = [int("".join(c for c in p if c.isdigit()) or 0) for p in raw.split(".")]
            return (parts[0], parts[1] if len(parts) > 1 else 0)
        number = int("".join(c for c in raw if c.isdigit()) or 0)
        if number >= 1000:
            return (number // 1000, number % 1000)
        return (number, 0)
    except Exception:
        return (0, 0)


def _version_label(version: Any) -> str:
    major, minor = _version_tuple(version)
    return f"{major}.{minor:03d}" if major else str(version or "unknown")


def _flatten_props(device: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for bucket in ("systemProperties", "inheritedProperties", "customProperties"):
        for prop in device.get(bucket) or []:
            name = prop.get("name")
            if name:
                out[str(name)] = str(prop.get("value") or "")
    return out


def _boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "enabled"}


CheckFn = Callable[[PortalSnapshot, dict[str, Any], float], CheckResult]


def check_api_health(snap: PortalSnapshot, standards: dict[str, Any], now: float) -> CheckResult:
    if snap.api_ok:
        findings = [
            Finding(
                "api_health",
                "ok",
                snap.portal or "portal",
                "API reachable and inventory loaded",
                "Portal REST API v3 must respond to signed GET requests",
            )
        ]
    else:
        findings = [
            Finding(
                "api_health",
                "critical",
                snap.portal or "portal",
                "API was not reachable",
                "Portal REST API v3 must respond to signed GET requests",
            )
        ]
    return CheckResult(
        "api_health",
        "API and portal reachability",
        "health",
        findings,
        {
            "devices": len(snap.devices),
            "collectors": len(snap.collectors),
            "alert_rules": len(snap.alert_rules),
        },
        "Confirms the operations token can read resources, collectors and alerting.",
    )


def check_collector_status(snap: PortalSnapshot, standards: dict[str, Any], now: float) -> CheckResult:
    findings: list[Finding] = []
    down = 0
    for collector in snap.collectors:
        is_down = _boolish(collector.get("isDown"))
        if is_down:
            down += 1
            sev, text = "critical", "Collector is down"
        else:
            sev, text = "ok", "Collector is up"
        findings.append(
            Finding(
                "collector_status",
                sev,
                str(collector.get("description") or collector.get("hostname") or collector.get("id")),
                text,
                "Every collector that carries resources must be up",
                {
                    "collector_id": collector.get("id"),
                    "hostname": collector.get("hostname"),
                    "devices": collector.get("numberOfHosts"),
                },
            )
        )
    result = CheckResult(
        "collector_status",
        "Collector up/down",
        "health",
        findings,
        {"collectors": len(snap.collectors), "down": down},
        "A down collector stops collection for every assigned resource.",
    )
    result.sort()
    return result


def check_collector_capacity(snap: PortalSnapshot, standards: dict[str, Any], now: float) -> CheckResult:
    caps = standards["collector_capacity"]
    warn, crit = standards["collector_warn_pct"], standards["collector_critical_pct"]
    findings: list[Finding] = []
    for collector in snap.collectors:
        size = str(collector.get("collectorSize") or "medium").lower()
        cap = caps.get(size, caps["medium"])
        dev_pct = _pct(collector.get("numberOfHosts") or 0, cap["devices"])
        inst_pct = _pct(collector.get("numberOfInstances") or 0, cap["instances"])
        peak = max(dev_pct or 0, inst_pct or 0)
        if peak >= crit:
            sev, text = "critical", f"At {peak}% of LogicMonitor {size} capacity"
        elif peak >= warn:
            sev, text = "warn", f"At {peak}% of LogicMonitor {size} capacity"
        else:
            sev, text = "ok", f"Within LogicMonitor {size} capacity ({peak}%)"
        findings.append(
            Finding(
                "collector_capacity",
                sev,
                str(collector.get("description") or collector.get("hostname")),
                text,
                f"{size}: {cap['devices']} devices / {cap['instances']} instances; warn {warn}% critical {crit}%",
                {
                    "size": size,
                    "devices": collector.get("numberOfHosts"),
                    "instances": collector.get("numberOfInstances"),
                    "device_capacity_pct": dev_pct,
                    "instance_capacity_pct": inst_pct,
                    "collector_id": collector.get("id"),
                },
            )
        )
    result = CheckResult(
        "collector_capacity",
        "Collector capacity vs LogicMonitor sizing",
        "health",
        findings,
        {"collectors": len(snap.collectors)},
        "Capacities are LogicMonitor starting points by collector size. Confirm for the installed version.",
    )
    result.sort()
    return result


def check_collector_versions(snap: PortalSnapshot, standards: dict[str, Any], now: float) -> CheckResult:
    drift = int(standards["collector_version_major_drift"])
    findings: list[Finding] = []
    if not snap.collectors:
        return CheckResult(
            "collector_versions",
            "Collector version drift",
            "health",
            [],
            {"collectors": 0},
        )
    newest = max(_version_tuple(c.get("collectorVersion")) for c in snap.collectors)
    newest_label = f"{newest[0]}.{newest[1]:03d}"
    for collector in snap.collectors:
        ver = _version_tuple(collector.get("collectorVersion"))
        behind = newest[0] - ver[0]
        if behind > drift:
            sev = "critical"
            text = f"{behind} major versions behind {newest_label}"
        elif behind > 0 or ver < newest:
            sev = "warn"
            text = f"Behind newest in portal ({newest_label})"
        else:
            sev, text = "ok", f"Matches newest in portal ({newest_label})"
        findings.append(
            Finding(
                "collector_versions",
                sev,
                str(collector.get("description") or collector.get("hostname")),
                text,
                f"Collectors should not trail the newest in-portal major version by more than {drift}",
                {
                    "version": _version_label(collector.get("collectorVersion")),
                    "collector_id": collector.get("id"),
                },
            )
        )
    result = CheckResult(
        "collector_versions",
        "Collector version drift",
        "health",
        findings,
        {"collectors": len(snap.collectors), "newest_in_portal": newest_label},
        "Compared against the newest collector in this portal, not the latest public release.",
    )
    result.sort()
    return result


def check_collector_failover(snap: PortalSnapshot, standards: dict[str, Any], now: float) -> CheckResult:
    findings: list[Finding] = []
    missing = 0
    for collector in snap.collectors:
        backup = collector.get("backupAgentId") or 0
        devices = collector.get("numberOfHosts") or 0
        fail_back = _boolish(collector.get("enableFailBack"))
        if not backup and devices:
            missing += 1
            sev = "critical" if devices > 50 else "warn"
            text = "No failover collector assigned"
        elif not backup:
            sev, text = "info", "No failover collector, no resources assigned"
        elif not fail_back:
            sev, text = "warn", "Failover collector set but fail-back is disabled"
        else:
            sev, text = "ok", "Failover collector assigned with fail-back"
        findings.append(
            Finding(
                "collector_failover",
                sev,
                str(collector.get("description") or collector.get("hostname")),
                text,
                "Collectors with assigned resources must have a backup collector and fail-back enabled",
                {
                    "backup_collector_id": backup or "",
                    "fail_back_enabled": fail_back,
                    "devices_at_risk": devices,
                    "collector_id": collector.get("id"),
                },
            )
        )
    result = CheckResult(
        "collector_failover",
        "Collector failover",
        "health",
        findings,
        {"collectors": len(snap.collectors), "without_failover": missing},
        "Failover only helps when the backup collector has spare capacity and the same credential reach.",
    )
    result.sort()
    return result


def check_device_status(snap: PortalSnapshot, standards: dict[str, Any], now: float) -> CheckResult:
    findings: list[Finding] = []
    dead = 0
    for device in snap.devices:
        status = str(device.get("hostStatus") or "unknown").lower()
        if status.startswith("dead"):
            dead += 1
            sev, text = "critical", "Resource is dead (collector cannot reach it)"
        elif status in ("normal", "normal-sdt"):
            sev, text = "ok", "Resource is reachable"
        else:
            sev, text = "warn", f"Host status is {status}"
        findings.append(
            Finding(
                "device_status",
                sev,
                str(device.get("displayName") or device.get("name")),
                text,
                "Monitored resources must be reachable (hostStatus normal)",
                {
                    "host_status": device.get("hostStatus"),
                    "device_id": device.get("id"),
                    "collector_id": device.get("currentCollectorId")
                    or device.get("preferredCollectorId"),
                },
            )
        )
    result = CheckResult(
        "device_status",
        "Resource reachability",
        "health",
        findings,
        {"devices": len(snap.devices), "dead": dead},
        "Dead status means the assigned collector cannot reach the resource.",
    )
    result.sort()
    return result


def check_alerting_disabled(snap: PortalSnapshot, standards: dict[str, Any], now: float) -> CheckResult:
    findings: list[Finding] = []
    flagged = 0
    for device in snap.devices:
        disabled = _boolish(device.get("disableAlerting"))
        if disabled:
            flagged += 1
            findings.append(
                Finding(
                    "alerting_disabled",
                    "warn",
                    str(device.get("displayName") or device.get("name")),
                    "Resource-level alerting is disabled",
                    "Production resources should have alerting enabled unless they are in a documented staging group",
                    {"device_id": device.get("id")},
                )
            )
    if not findings:
        findings.append(
            Finding(
                "alerting_disabled",
                "ok",
                "estate",
                "No resources have alerting disabled",
                "Production resources should have alerting enabled",
            )
        )
    return CheckResult(
        "alerting_disabled",
        "Resources with alerting disabled",
        "deviation",
        findings,
        {"devices_scanned": len(snap.devices), "flagged": flagged},
        "Disabled alerting is a configuration deviation unless it is an agreed exception.",
    )


def check_stale_discovery(snap: PortalSnapshot, standards: dict[str, Any], now: float) -> CheckResult:
    limit = int(standards["stale_discovery_days"])
    findings: list[Finding] = []
    for device in snap.devices:
        age = _age_days(device.get("autoPropsUpdatedOn"), now)
        if age is None:
            sev, text = "warn", "No auto-discovery data recorded"
        elif age > limit:
            sev, text = "critical", f"Auto properties last refreshed {age} days ago"
        elif age > limit / 2:
            sev, text = "warn", f"Auto properties ageing ({age} days)"
        else:
            continue
        findings.append(
            Finding(
                "stale_discovery",
                sev,
                str(device.get("displayName") or device.get("name")),
                text,
                f"Auto-discovery properties should refresh within {limit} days",
                {"auto_props_age_days": age, "device_id": device.get("id")},
            )
        )
    result = CheckResult(
        "stale_discovery",
        "Stale auto-discovery",
        "deviation",
        findings,
        {"devices_scanned": len(snap.devices), "flagged": len(findings), "threshold_days": limit},
        "Stale auto properties usually point to a credential or collector problem.",
    )
    result.sort()
    return result


def check_duplicates(snap: PortalSnapshot, standards: dict[str, Any], now: float) -> CheckResult:
    by_name: dict[str, list[dict[str, Any]]] = {}
    for device in snap.devices:
        key = str(device.get("name") or "").strip().lower()
        if key:
            by_name.setdefault(key, []).append(device)
    findings: list[Finding] = []
    for key, group in by_name.items():
        if len(group) < 2:
            continue
        findings.append(
            Finding(
                "duplicates",
                "warn",
                key,
                f"{len(group)} resources share this address",
                "Each monitored address should appear once",
                {
                    "display_names": " | ".join(
                        str(g.get("displayName") or g.get("name")) for g in group
                    ),
                    "device_ids": ", ".join(str(g.get("id")) for g in group),
                },
            )
        )
    return CheckResult(
        "duplicates",
        "Duplicate host names",
        "deviation",
        findings,
        {"devices_scanned": len(snap.devices), "duplicate_sets": len(findings)},
        "Duplicates double-count licence usage and split alert history.",
    )


def check_property_coverage(snap: PortalSnapshot, standards: dict[str, Any], now: float) -> CheckResult:
    required = list(standards["required_properties"])
    recommended = list(standards.get("infosys_recommended_properties") or [])
    findings: list[Finding] = []
    for device in snap.device_properties:
        props = _flatten_props(device)
        missing = [name for name in required if not props.get(name)]
        missing_rec = [name for name in recommended if not props.get(name)]
        if not missing and not missing_rec:
            continue
        if missing:
            sev = "critical" if len(missing) == len(required) else "warn"
            text = f"Missing required properties: {', '.join(missing)}"
            standard = "Required: " + ", ".join(required)
        else:
            sev = "info"
            text = f"Missing Infosys recommended properties: {', '.join(missing_rec)}"
            standard = "Recommended: " + ", ".join(recommended)
        findings.append(
            Finding(
                "property_coverage",
                sev,
                str(device.get("displayName") or device.get("id")),
                text,
                standard,
                {"device_id": device.get("id")},
            )
        )
    result = CheckResult(
        "property_coverage",
        "Required property coverage",
        "deviation",
        findings,
        {
            "devices_scanned": len(snap.device_properties),
            "flagged": len(findings),
            "required_properties": ", ".join(required),
        },
        "Property coverage drives grouping, alert routing and reporting.",
    )
    result.sort()
    return result


def check_category_credentials(snap: PortalSnapshot, standards: dict[str, Any], now: float) -> CheckResult:
    expected = {k.lower(): list(v) for k, v in (standards.get("category_credentials") or {}).items()}
    findings: list[Finding] = []
    for device in snap.device_properties:
        props = _flatten_props(device)
        categories = [
            part.strip().lower()
            for part in str(props.get("system.categories") or "").split(",")
            if part.strip()
        ]
        missing: list[str] = []
        for category in categories:
            for name in expected.get(category, []):
                if not props.get(name):
                    missing.append(f"{category}:{name}")
        if not missing:
            continue
        findings.append(
            Finding(
                "category_credentials",
                "warn",
                str(device.get("displayName") or device.get("id")),
                "Missing credential properties for applied categories: " + ", ".join(missing),
                "Credential property presence required for each system.categories value (values are never read)",
                {"device_id": device.get("id"), "categories": ",".join(categories)},
            )
        )
    result = CheckResult(
        "category_credentials",
        "Category credential properties",
        "deviation",
        findings,
        {"devices_scanned": len(snap.device_properties), "flagged": len(findings)},
        "Presence only is checked; secret values are never printed.",
    )
    result.sort()
    return result


def check_ungrouped_devices(snap: PortalSnapshot, standards: dict[str, Any], now: float) -> CheckResult:
    root_ids = set(str(x) for x in standards.get("ungrouped_root_group_ids") or ["1"])
    findings: list[Finding] = []
    for device in snap.devices:
        groups = [g for g in str(device.get("hostGroupIds") or "").split(",") if g]
        if groups and set(groups) - root_ids:
            continue
        findings.append(
            Finding(
                "ungrouped_devices",
                "warn",
                str(device.get("displayName") or device.get("name")),
                "Sits only at the root group",
                "Resources must live in a customer or service group, not only the root group",
                {"group_ids": device.get("hostGroupIds"), "device_id": device.get("id")},
            )
        )
    return CheckResult(
        "ungrouped_devices",
        "Ungrouped resources",
        "deviation",
        findings,
        {"devices_scanned": len(snap.devices), "flagged": len(findings)},
        "Resources outside a group inherit no properties, alert rules or dashboards.",
    )


def check_alert_ack_sla(snap: PortalSnapshot, standards: dict[str, Any], now: float) -> CheckResult:
    sla = standards["ack_sla_minutes"]
    findings: list[Finding] = []
    for alert in snap.alerts:
        if alert.get("cleared") or alert.get("acked") or alert.get("sdted"):
            continue
        sev_name = ALERT_SEVERITY.get(alert.get("severity"), "info")
        limit = sla.get(sev_name)
        age = _age_minutes(alert.get("startEpoch"), now)
        if not limit or age is None or age < limit:
            continue
        findings.append(
            Finding(
                "alert_ack_sla",
                "critical" if age > limit * 3 else "warn",
                str(alert.get("monitorObjectName") or alert.get("id")),
                f"Open and unacknowledged for {int(age)} minutes ({sev_name})",
                f"{sev_name} alerts should be acknowledged within {limit} minutes",
                {
                    "alert_id": alert.get("id"),
                    "age_minutes": int(age),
                    "target_minutes": limit,
                    "datasource": alert.get("resourceTemplateName"),
                },
            )
        )
    result = CheckResult(
        "alert_ack_sla",
        "Unacknowledged alert SLA",
        "health",
        findings,
        {
            "alerts_in_window": len(snap.alerts),
            "breaches": len(findings),
            "window_hours": standards["alert_window_hours"],
        },
        "Ageing unacknowledged alerts usually mean routing or staffing gaps.",
    )
    result.sort()
    return result


def check_alert_noise(snap: PortalSnapshot, standards: dict[str, Any], now: float) -> CheckResult:
    warn_pct = float(standards["noise_share_warn_pct"])
    crit_pct = float(standards["noise_share_critical_pct"])
    buckets: dict[tuple[Any, Any, Any], int] = {}
    for alert in snap.alerts:
        key = (
            alert.get("monitorObjectName"),
            alert.get("resourceTemplateName"),
            alert.get("dataPointName"),
        )
        buckets[key] = buckets.get(key, 0) + 1
    total = len(snap.alerts) or 1
    findings: list[Finding] = []
    for (resource, datasource, datapoint), count in buckets.items():
        share = _pct(count, total) or 0
        if share >= crit_pct:
            sev, text = "critical", f"{share}% of alert volume in the window"
        elif share >= warn_pct:
            sev, text = "warn", f"{share}% of alert volume in the window"
        else:
            continue
        findings.append(
            Finding(
                "alert_noise",
                sev,
                str(resource or "unknown"),
                text,
                f"No single signature should exceed {warn_pct}% (warn) / {crit_pct}% (critical) of window volume",
                {"datasource": datasource, "datapoint": datapoint, "alerts": count, "share_pct": share},
            )
        )
    findings.sort(key=lambda f: f.details.get("alerts", 0), reverse=True)
    return CheckResult(
        "alert_noise",
        "Alert noise concentration",
        "health",
        findings,
        {
            "alerts_in_window": len(snap.alerts),
            "window_hours": standards["alert_window_hours"],
            "distinct_signatures": len(buckets),
        },
        "A noisy signature usually needs a threshold or SDT review, not more collectors.",
    )


def check_notification_routing(snap: PortalSnapshot, standards: dict[str, Any], now: float) -> CheckResult:
    chain_by_id = {c.get("id"): c for c in snap.chains}
    findings: list[Finding] = []
    priorities: dict[Any, list[str]] = {}
    for rule in snap.alert_rules:
        name = str(rule.get("name") or rule.get("id"))
        priorities.setdefault(rule.get("priority"), []).append(name)
        chain = chain_by_id.get(rule.get("escalatingChainId"))
        interval = rule.get("escalationInterval")
        if not chain:
            sev, text = "critical", "Alert rule has no valid escalation chain"
        else:
            destinations = chain.get("destinations") or []
            if not destinations:
                sev, text = "critical", f"Chain '{chain.get('name')}' has no destinations"
            elif not (rule.get("devices") or rule.get("deviceGroups")):
                sev, text = "warn", "Rule matches no named resources or groups"
            elif interval == 0:
                sev, text = "warn", "Escalation interval is 0; only the first stage is ever notified"
            else:
                sev, text = "ok", f"Routes to chain '{chain.get('name')}'"
        findings.append(
            Finding(
                "notification_routing",
                sev,
                name,
                text,
                "Every alert rule must bind a chain with destinations; priorities must be unique; interval 0 is a deviation",
                {
                    "priority": rule.get("priority"),
                    "rule_id": rule.get("id"),
                    "escalation_interval": interval,
                },
            )
        )
    for priority, names in priorities.items():
        if priority in (None, "") or len(names) < 2:
            continue
        findings.append(
            Finding(
                "notification_routing",
                "warn",
                f"priority {priority}",
                "Duplicate alert-rule priority: " + ", ".join(names),
                "Alert rule priorities must be unique so routing order is deterministic",
            )
        )
    result = CheckResult(
        "notification_routing",
        "Alert rule and escalation chain routing",
        "deviation",
        findings,
        {"alert_rules": len(snap.alert_rules), "escalation_chains": len(snap.chains)},
        "Routing gaps are the usual reason an alert fires but nobody is paged.",
    )
    result.sort()
    return result


def check_sdt_audit(snap: PortalSnapshot, standards: dict[str, Any], now: float) -> CheckResult:
    long_days = int(standards["sdt_long_days"])
    findings: list[Finding] = []
    for sdt in snap.sdts:
        end = sdt.get("endDateTime")
        end_age = _age_days(end, now)
        target = (
            sdt.get("deviceDisplayName")
            or sdt.get("deviceGroupFullPath")
            or sdt.get("websiteName")
            or sdt.get("collectorDescription")
            or "unspecified"
        )
        if end and end_age is not None and end_age > 0:
            continue
        if not end or int(end or 0) == 0:
            sev, text = "critical", "Open-ended downtime with no end date"
        elif end_age is not None and end_age < -long_days:
            sev, text = "warn", f"Runs for another {abs(int(end_age))} days"
        else:
            continue
        findings.append(
            Finding(
                "sdt_audit",
                sev,
                str(target),
                text,
                f"SDT windows must be bounded; windows longer than {long_days} days need review",
                {"sdt_id": sdt.get("id"), "raised_by": sdt.get("admin")},
            )
        )
    result = CheckResult(
        "sdt_audit",
        "Scheduled downtime hygiene",
        "deviation",
        findings,
        {"sdt_entries": len(snap.sdts), "flagged": len(findings)},
        "Long or open-ended downtime windows silence monitoring without anyone noticing.",
    )
    result.sort()
    return result


def check_api_token_hygiene(snap: PortalSnapshot, standards: dict[str, Any], now: float) -> CheckResult:
    idle = int(standards["token_idle_days"])
    findings: list[Finding] = []
    if not snap.tokens:
        findings.append(
            Finding(
                "api_token_hygiene",
                "info",
                "api tokens",
                "Token inventory was empty or the role cannot list tokens",
                f"Enabled tokens unused for {idle} days should be reviewed",
            )
        )
        return CheckResult(
            "api_token_hygiene",
            "API token hygiene",
            "deviation",
            findings,
            {"tokens": 0},
            "Needs a role with user-management permission. Access keys are never printed.",
        )
    for token in snap.tokens:
        last_used = _age_days(token.get("lastUsedOn"), now)
        enabled = str(token.get("status", "")).lower() in ("active", "1", "true", "enabled")
        access = str(token.get("accessId") or "")
        truncated = (access[:8] + "…") if access else ""
        if not enabled:
            sev, text = "info", "Disabled"
        elif last_used is None:
            sev, text = "warn", "Never used"
        elif last_used > idle:
            sev, text = "warn", f"Unused for {int(last_used)} days"
        else:
            sev, text = "ok", f"Last used {int(last_used)} days ago"
        findings.append(
            Finding(
                "api_token_hygiene",
                sev,
                str(token.get("adminName") or truncated or token.get("id")),
                text,
                f"Enabled tokens unused for {idle} days should be rotated or disabled",
                {"access_id": truncated, "token_id": token.get("id")},
            )
        )
    result = CheckResult(
        "api_token_hygiene",
        "API token hygiene",
        "deviation",
        findings,
        {"tokens": len(snap.tokens), "idle_threshold_days": idle},
        "Access keys are truncated and never stored.",
    )
    result.sort()
    return result


HEALTH_CHECKS: list[CheckFn] = [
    check_api_health,
    check_collector_status,
    check_collector_failover,
    check_collector_capacity,
    check_collector_versions,
    check_device_status,
    check_alert_ack_sla,
    check_alert_noise,
]

DEVIATION_CHECKS: list[CheckFn] = [
    check_property_coverage,
    check_category_credentials,
    check_ungrouped_devices,
    check_duplicates,
    check_alerting_disabled,
    check_stale_discovery,
    check_notification_routing,
    check_sdt_audit,
    check_api_token_hygiene,
]

ALL_CHECKS = HEALTH_CHECKS + DEVIATION_CHECKS


def run_checks(
    snapshot: PortalSnapshot,
    standards: dict[str, Any],
    suite: str = "all",
    now: float | None = None,
) -> list[CheckResult]:
    clock = time.time() if now is None else now
    if suite == "health":
        selected = HEALTH_CHECKS
    elif suite == "deviation":
        selected = DEVIATION_CHECKS
    else:
        selected = ALL_CHECKS
    return [fn(snapshot, standards, clock) for fn in selected]


def worst_severity(results: list[CheckResult]) -> str:
    worst = "ok"
    for result in results:
        for finding in result.findings:
            if SEV_ORDER.get(finding.severity, 9) < SEV_ORDER.get(worst, 9):
                worst = finding.severity
    return worst


def exit_code(results: list[CheckResult]) -> int:
    worst = worst_severity(results)
    if worst == "critical":
        return 1
    return 0


def print_report(results: list[CheckResult], portal: str, suite: str) -> None:
    print(f"LogicMonitor operations report  suite={suite}  portal={portal or '-'}")
    print("=" * 88)
    for result in results:
        counts = result.counts()
        print(
            f"\n[{result.check_id}] {result.name}  "
            f"critical={counts['critical']} warn={counts['warn']} "
            f"ok={counts['ok']} info={counts['info']}"
        )
        shown = 0
        for finding in result.findings:
            if finding.severity == "ok" and counts["ok"] > 8:
                continue
            print(f"  {finding.severity.upper():<8}  {finding.subject}: {finding.finding}")
            if finding.standard:
                print(f"           standard: {finding.standard}")
            shown += 1
            if shown >= 40:
                remaining = len(result.findings) - shown
                if remaining > 0:
                    print(f"  … {remaining} more finding(s) omitted")
                break
        if result.notes:
            print(f"  note: {result.notes}")
    print("\n" + "=" * 88)
    print(f"Overall worst severity: {worst_severity(results)}")


def results_to_json(results: list[CheckResult], portal: str, suite: str) -> dict[str, Any]:
    return {
        "portal": portal,
        "suite": suite,
        "worst_severity": worst_severity(results),
        "checks": [
            {
                "id": r.check_id,
                "name": r.name,
                "suite": r.suite,
                "summary": r.summary,
                "notes": r.notes,
                "counts": r.counts(),
                "findings": [f.as_dict() for f in r.findings],
            }
            for r in results
        ],
    }


def results_to_markdown(results: list[CheckResult], portal: str, suite: str) -> str:
    lines = [
        f"# LogicMonitor operations report",
        "",
        f"- Portal: `{portal or '-'}`",
        f"- Suite: `{suite}`",
        f"- Worst severity: **{worst_severity(results)}**",
        "",
    ]
    for result in results:
        counts = result.counts()
        lines.append(f"## {result.name} (`{result.check_id}`)")
        lines.append("")
        lines.append(
            f"critical={counts['critical']}, warn={counts['warn']}, "
            f"ok={counts['ok']}, info={counts['info']}"
        )
        lines.append("")
        lines.append("| Severity | Subject | Finding | Standard |")
        lines.append("|---|---|---|---|")
        interesting = [f for f in result.findings if f.severity != "ok"] or result.findings[:5]
        for finding in interesting[:30]:
            subject = str(finding.subject).replace("|", "/")
            text = str(finding.finding).replace("|", "/")
            standard = str(finding.standard).replace("|", "/")
            lines.append(f"| {finding.severity} | {subject} | {text} | {standard} |")
        lines.append("")
        if result.notes:
            lines.append(f"_{result.notes}_")
            lines.append("")
    return "\n".join(lines) + "\n"


def write_csv(results: list[CheckResult], path: Path) -> None:
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["check_id", "severity", "subject", "finding", "standard"],
        )
        writer.writeheader()
        for result in results:
            for finding in result.findings:
                writer.writerow(
                    {
                        "check_id": finding.check_id,
                        "severity": finding.severity,
                        "subject": finding.subject,
                        "finding": finding.finding,
                        "standard": finding.standard,
                    }
                )
