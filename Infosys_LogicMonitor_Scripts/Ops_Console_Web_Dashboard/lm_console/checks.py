"""
Health and hygiene checks for a LogicMonitor estate.

Every check returns the same shape, so the GUI and the exporters never need to
know what a check does:

    CheckResult(columns=[...], rows=[{...}], summary={...}, notes="...")

Each row carries a `severity` of critical / warn / ok / info. The GUI colours
and filters on that field.

------------------------------------------------------------------------------
CONFIG — tune these to your estate, then leave them alone.
------------------------------------------------------------------------------
Collector capacity figures are starting points only. Confirm them against the
LogicMonitor collector capacity and sizing documentation for your collector
version before you rely on them for scaling decisions.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

import httpx

from .lm_client import LMApiError, LMAuthError, LMClient

CONFIG: Dict[str, Any] = {
    # Properties every onboarded resource should carry.
    "required_properties": ["location", "owner", "environment", "service.tier"],
    # Age in days after which auto-discovery data is treated as stale.
    "stale_discovery_days": 14,
    # Alert look-back window for noise and ageing analysis.
    "alert_window_hours": 24,
    "alert_max_records": 20000,
    # Unacknowledged alert age that counts as a breach, in minutes, by severity.
    "ack_sla_minutes": {"critical": 15, "error": 60, "warn": 240},
    # Collector utilisation thresholds, as a percentage of capacity.
    "collector_warn_pct": 75,
    "collector_critical_pct": 90,
    # Starting-point capacity per collector size: devices, instances.
    "collector_capacity": {
        "nano": {"devices": 25, "instances": 1500},
        "small": {"devices": 100, "instances": 5000},
        "medium": {"devices": 250, "instances": 15000},
        "large": {"devices": 500, "instances": 30000},
        "extra_large": {"devices": 1000, "instances": 60000},
    },
    # Collector versions older than this many minor releases behind the newest
    # collector in the portal are flagged.
    "collector_version_drift": 2,
    # API tokens unused for this many days are flagged.
    "token_idle_days": 90,
    # Probe integration endpoints from this laptop. Off by default: it makes
    # outbound calls to third-party systems.
    "probe_integrations": False,
    "probe_timeout_seconds": 6,
}

SEV_ORDER = {"critical": 0, "warn": 1, "info": 2, "ok": 3}
ALERT_SEVERITY = {4: "critical", 3: "error", 2: "warn", 1: "info"}

DEVICE_FIELDS = (
    "id,name,displayName,hostStatus,alertStatus,sdtStatus,deviceType,"
    "preferredCollectorId,currentCollectorId,autoPropsUpdatedOn,createdOn,"
    "updatedOn,hostGroupIds,link"
)
DEVICE_PROP_FIELDS = "id,displayName,customProperties,inheritedProperties,systemProperties"
COLLECTOR_FIELDS = (
    "id,description,hostname,isDown,collectorVersion,build,ea,platform,"
    "collectorSize,numberOfHosts,numberOfInstances,numberOfWebsites,"
    "backupAgentId,enableFailBack,collectorGroupId,collectorGroupName,uptime,"
    "specifiedCollectorDeviceGroupId,resendIval"
)
ALERT_FIELDS = (
    "id,severity,acked,cleared,startEpoch,endEpoch,monitorObjectId,"
    "monitorObjectName,resourceTemplateName,dataPointName,instanceName,sdted,ackedBy"
)


@dataclass
class CheckResult:
    columns: List[str]
    rows: List[Dict[str, Any]] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    def counts(self) -> Dict[str, int]:
        out = {"critical": 0, "warn": 0, "ok": 0, "info": 0}
        for row in self.rows:
            sev = row.get("severity", "info")
            out[sev] = out.get(sev, 0) + 1
        return out

    def sort_by_severity(self) -> None:
        self.rows.sort(key=lambda r: SEV_ORDER.get(r.get("severity", "info"), 9))


class Inventory:
    """Fetch-once cache shared by every check in a single run."""

    def __init__(self, client: LMClient):
        self.client = client
        self._cache: Dict[str, Any] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    async def _once(self, key: str, loader: Callable[[], Awaitable[Any]]) -> Any:
        if key in self._cache:
            return self._cache[key]
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if key not in self._cache:
                self._cache[key] = await loader()
        return self._cache[key]

    async def devices(self) -> List[Dict[str, Any]]:
        return await self._once(
            "devices", lambda: self.client.collect("/device/devices", fields=DEVICE_FIELDS)
        )

    async def device_properties(self) -> List[Dict[str, Any]]:
        return await self._once(
            "device_props",
            lambda: self.client.collect("/device/devices", fields=DEVICE_PROP_FIELDS),
        )

    async def collectors(self) -> List[Dict[str, Any]]:
        return await self._once(
            "collectors",
            lambda: self.client.collect("/setting/collector/collectors", fields=COLLECTOR_FIELDS),
        )

    async def alerts(self) -> List[Dict[str, Any]]:
        since = int(time.time()) - CONFIG["alert_window_hours"] * 3600

        async def load() -> List[Dict[str, Any]]:
            return await self.client.collect(
                "/alert/alerts",
                fields=ALERT_FIELDS,
                filter_=f"startEpoch>:{since}",
                max_items=CONFIG["alert_max_records"],
                needMessage="false",
            )

        return await self._once("alerts", load)

    async def sdts(self) -> List[Dict[str, Any]]:
        return await self._once("sdts", lambda: self.client.collect("/sdt/sdts"))

    async def integrations(self) -> List[Dict[str, Any]]:
        return await self._once(
            "integrations", lambda: self.client.collect("/setting/integrations")
        )

    async def alert_rules(self) -> List[Dict[str, Any]]:
        return await self._once(
            "rules", lambda: self.client.collect("/setting/alert/rules")
        )

    async def chains(self) -> List[Dict[str, Any]]:
        return await self._once(
            "chains", lambda: self.client.collect("/setting/alert/chains")
        )

    async def api_tokens(self) -> List[Dict[str, Any]]:
        return await self._once(
            "tokens", lambda: self.client.collect("/setting/admins/apitokens")
        )


# --------------------------------------------------------------------- utils

def _age_days(epoch: Optional[int]) -> Optional[float]:
    if not epoch:
        return None
    if epoch > 10_000_000_000:  # milliseconds
        epoch = epoch / 1000
    return round((time.time() - epoch) / 86400, 1)


def _age_minutes(epoch: Optional[int]) -> Optional[float]:
    if not epoch:
        return None
    if epoch > 10_000_000_000:
        epoch = epoch / 1000
    return round((time.time() - epoch) / 60, 1)


def _pct(part: float, whole: float) -> Optional[float]:
    if not whole:
        return None
    return round(part / whole * 100, 1)


def _version_tuple(version: Any) -> tuple:
    """Normalise a collector version to (major, minor).

    The API returns an integer such as 37300 for collector 37.300, but some
    endpoints return the dotted string. Both are handled.
    """
    try:
        raw = str(version or "0").strip()
        if "." in raw:
            parts = [int("".join(c for c in p if c.isdigit()) or 0) for p in raw.split(".")]
            return tuple(parts[:2]) if len(parts) > 1 else (parts[0], 0)
        number = int("".join(c for c in raw if c.isdigit()) or 0)
        if number >= 1000:  # packed form: 37300 -> 37.300
            return (number // 1000, number % 1000)
        return (number, 0)
    except Exception:
        return (0, 0)


def _version_label(version: Any) -> str:
    major, minor = _version_tuple(version)
    return f"{major}.{minor:03d}" if major else str(version or "unknown")


def _flatten_props(device: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for bucket in ("systemProperties", "inheritedProperties", "customProperties"):
        for prop in device.get(bucket) or []:
            name = prop.get("name")
            if name:
                out[name] = prop.get("value", "")
    return out


# -------------------------------------------------------------------- checks

async def check_device_status(inv: Inventory) -> CheckResult:
    devices = await inv.devices()
    rows = []
    for d in devices:
        host_status = (d.get("hostStatus") or "unknown").lower()
        if host_status.startswith("dead"):
            sev = "critical"
            finding = "Not reachable by its collector"
        elif host_status in ("normal", "normal-sdt"):
            sev = "ok"
            finding = "Reachable"
        else:
            sev = "warn"
            finding = f"Host status: {host_status}"
        rows.append(
            {
                "severity": sev,
                "resource": d.get("displayName") or d.get("name"),
                "host": d.get("name"),
                "finding": finding,
                "host_status": d.get("hostStatus"),
                "alert_status": d.get("alertStatus"),
                "in_sdt": "yes" if (d.get("sdtStatus") or "").upper() == "SDT" else "no",
                "collector_id": d.get("currentCollectorId") or d.get("preferredCollectorId"),
                "device_id": d.get("id"),
            }
        )
    res = CheckResult(
        columns=["severity", "resource", "host", "finding", "host_status",
                 "alert_status", "in_sdt", "collector_id", "device_id"],
        rows=rows,
        summary={"devices": len(devices)},
        notes="Dead status means the assigned collector cannot reach the resource. "
              "Confirm credentials and firewall paths before re-onboarding.",
    )
    res.sort_by_severity()
    return res


async def check_stale_discovery(inv: Inventory) -> CheckResult:
    devices = await inv.devices()
    limit = CONFIG["stale_discovery_days"]
    rows = []
    for d in devices:
        age = _age_days(d.get("autoPropsUpdatedOn"))
        if age is None:
            sev, finding = "warn", "No auto-discovery data recorded"
        elif age > limit:
            sev, finding = "critical", f"Auto properties last refreshed {age} days ago"
        elif age > limit / 2:
            sev, finding = "warn", f"Auto properties ageing ({age} days)"
        else:
            continue
        rows.append(
            {
                "severity": sev,
                "resource": d.get("displayName") or d.get("name"),
                "finding": finding,
                "auto_props_age_days": age,
                "created_days_ago": _age_days(d.get("createdOn")),
                "device_id": d.get("id"),
            }
        )
    res = CheckResult(
        columns=["severity", "resource", "finding", "auto_props_age_days",
                 "created_days_ago", "device_id"],
        rows=rows,
        summary={"devices_scanned": len(devices), "flagged": len(rows),
                 "threshold_days": limit},
        notes="Stale auto properties usually point to a credential or collector "
              "problem rather than a genuine configuration freeze.",
    )
    res.sort_by_severity()
    return res


async def check_duplicates(inv: Inventory) -> CheckResult:
    devices = await inv.devices()
    by_name: Dict[str, List[Dict[str, Any]]] = {}
    for d in devices:
        key = (d.get("name") or "").strip().lower()
        if key:
            by_name.setdefault(key, []).append(d)
    rows = []
    for key, group in by_name.items():
        if len(group) < 2:
            continue
        rows.append(
            {
                "severity": "warn",
                "host": key,
                "finding": f"{len(group)} resources share this address",
                "display_names": " | ".join(
                    str(g.get("displayName") or g.get("name")) for g in group
                ),
                "device_ids": ", ".join(str(g.get("id")) for g in group),
                "collector_ids": ", ".join(
                    str(g.get("currentCollectorId") or g.get("preferredCollectorId"))
                    for g in group
                ),
            }
        )
    return CheckResult(
        columns=["severity", "host", "finding", "display_names", "device_ids", "collector_ids"],
        rows=rows,
        summary={"devices_scanned": len(devices), "duplicate_sets": len(rows)},
        notes="Duplicates double-count licence usage and split alert history. "
              "Merge only after confirming which entry holds the longer data set.",
    )


async def check_property_coverage(inv: Inventory) -> CheckResult:
    devices = await inv.device_properties()
    required = CONFIG["required_properties"]
    rows = []
    for d in devices:
        props = _flatten_props(d)
        missing = [p for p in required if not props.get(p)]
        if not missing:
            continue
        rows.append(
            {
                "severity": "critical" if len(missing) == len(required) else "warn",
                "resource": d.get("displayName"),
                "finding": f"Missing {len(missing)} of {len(required)} required properties",
                "missing_properties": ", ".join(missing),
                "device_id": d.get("id"),
            }
        )
    res = CheckResult(
        columns=["severity", "resource", "finding", "missing_properties", "device_id"],
        rows=rows,
        summary={"devices_scanned": len(devices), "flagged": len(rows),
                 "required_properties": ", ".join(required)},
        notes="Property coverage drives dynamic grouping, alert routing and "
              "reporting. Fix this before tuning thresholds.",
    )
    res.sort_by_severity()
    return res


async def check_ungrouped_devices(inv: Inventory) -> CheckResult:
    devices = await inv.devices()
    rows = []
    for d in devices:
        groups = [g for g in str(d.get("hostGroupIds") or "").split(",") if g]
        if groups and groups != ["1"]:
            continue
        rows.append(
            {
                "severity": "warn",
                "resource": d.get("displayName") or d.get("name"),
                "finding": "Sits only at the root group",
                "group_ids": d.get("hostGroupIds"),
                "device_id": d.get("id"),
            }
        )
    return CheckResult(
        columns=["severity", "resource", "finding", "group_ids", "device_id"],
        rows=rows,
        summary={"devices_scanned": len(devices), "flagged": len(rows)},
        notes="Resources outside a group inherit no properties, no alert rules "
              "and no dashboards.",
    )


async def check_collector_status(inv: Inventory) -> CheckResult:
    collectors = await inv.collectors()
    rows = []
    for c in collectors:
        down = bool(c.get("isDown"))
        rows.append(
            {
                "severity": "critical" if down else "ok",
                "collector": c.get("description") or c.get("hostname"),
                "finding": "Down" if down else "Up",
                "hostname": c.get("hostname"),
                "platform": c.get("platform"),
                "version": _version_label(c.get("collectorVersion")),
                "size": c.get("collectorSize"),
                "devices": c.get("numberOfHosts"),
                "instances": c.get("numberOfInstances"),
                "group": c.get("collectorGroupName"),
                "collector_id": c.get("id"),
            }
        )
    res = CheckResult(
        columns=["severity", "collector", "finding", "hostname", "platform", "version",
                 "size", "devices", "instances", "group", "collector_id"],
        rows=rows,
        summary={"collectors": len(collectors),
                 "down": sum(1 for r in rows if r["severity"] == "critical")},
        notes="A down collector stops collection for every resource assigned to it. "
              "Check the failover results alongside this list.",
    )
    res.sort_by_severity()
    return res


async def check_collector_capacity(inv: Inventory) -> CheckResult:
    collectors = await inv.collectors()
    caps = CONFIG["collector_capacity"]
    warn, crit = CONFIG["collector_warn_pct"], CONFIG["collector_critical_pct"]
    rows = []
    for c in collectors:
        size = (c.get("collectorSize") or "medium").lower()
        cap = caps.get(size, caps["medium"])
        dev_pct = _pct(c.get("numberOfHosts") or 0, cap["devices"])
        inst_pct = _pct(c.get("numberOfInstances") or 0, cap["instances"])
        peak = max(x for x in (dev_pct or 0, inst_pct or 0))
        if peak >= crit:
            sev, finding = "critical", "At or above capacity — split the load"
        elif peak >= warn:
            sev, finding = "warn", "Approaching capacity — plan an additional collector"
        elif peak < 10 and (c.get("numberOfHosts") or 0) > 0:
            sev, finding = "info", "Lightly loaded — candidate for consolidation"
        else:
            sev, finding = "ok", "Within capacity"
        rows.append(
            {
                "severity": sev,
                "collector": c.get("description") or c.get("hostname"),
                "finding": finding,
                "size": size,
                "devices": c.get("numberOfHosts"),
                "device_capacity_pct": dev_pct,
                "instances": c.get("numberOfInstances"),
                "instance_capacity_pct": inst_pct,
                "websites": c.get("numberOfWebsites"),
                "collector_id": c.get("id"),
            }
        )
    res = CheckResult(
        columns=["severity", "collector", "finding", "size", "devices",
                 "device_capacity_pct", "instances", "instance_capacity_pct",
                 "websites", "collector_id"],
        rows=rows,
        summary={"collectors": len(collectors)},
        notes="Capacity figures come from the CONFIG block in checks.py. Confirm "
              "them against LogicMonitor collector sizing guidance for your "
              "collector version before acting on the percentages.",
    )
    res.sort_by_severity()
    return res


async def check_collector_versions(inv: Inventory) -> CheckResult:
    collectors = await inv.collectors()
    if not collectors:
        return CheckResult(columns=["severity", "collector"], rows=[],
                           summary={"collectors": 0})
    newest = max(_version_tuple(c.get("collectorVersion")) for c in collectors)
    drift = CONFIG["collector_version_drift"]
    rows = []
    for c in collectors:
        ver = _version_tuple(c.get("collectorVersion"))
        behind = newest[0] - ver[0]
        if behind > drift:
            sev = "critical"
            finding = f"{behind} major versions behind {newest[0]}.{newest[1]:03d}"
        elif behind > 0:
            sev = "warn"
            finding = f"{behind} major version behind {newest[0]}.{newest[1]:03d}"
        elif ver < newest:
            sev, finding = "warn", "Same major version, older build"
        else:
            sev, finding = "ok", "Newest version in this portal"
        rows.append(
            {
                "severity": sev,
                "collector": c.get("description") or c.get("hostname"),
                "finding": finding,
                "version": _version_label(c.get("collectorVersion")),
                "build": c.get("build"),
                "release_line": "EA" if c.get("ea") else "GD",
                "platform": c.get("platform"),
                "collector_id": c.get("id"),
            }
        )
    res = CheckResult(
        columns=["severity", "collector", "finding", "version", "build",
                 "release_line", "platform", "collector_id"],
        rows=rows,
        summary={"collectors": len(collectors),
                 "newest_in_portal": f"{newest[0]}.{newest[1]:03d}"},
        notes="Comparison is against the newest collector in this portal, not the "
              "newest published release. Check the release notes before upgrading.",
    )
    res.sort_by_severity()
    return res


async def check_collector_failover(inv: Inventory) -> CheckResult:
    collectors = await inv.collectors()
    rows = []
    for c in collectors:
        backup = c.get("backupAgentId") or 0
        devices = c.get("numberOfHosts") or 0
        if not backup and devices:
            sev = "critical" if devices > 50 else "warn"
            finding = "No failover collector assigned"
        elif not backup:
            sev, finding = "info", "No failover collector, no resources assigned"
        else:
            sev, finding = "ok", "Failover collector assigned"
        rows.append(
            {
                "severity": sev,
                "collector": c.get("description") or c.get("hostname"),
                "finding": finding,
                "backup_collector_id": backup or "",
                "fail_back_enabled": "yes" if c.get("enableFailBack") else "no",
                "devices_at_risk": devices,
                "group": c.get("collectorGroupName"),
                "collector_id": c.get("id"),
            }
        )
    res = CheckResult(
        columns=["severity", "collector", "finding", "backup_collector_id",
                 "fail_back_enabled", "devices_at_risk", "group", "collector_id"],
        rows=rows,
        summary={"collectors": len(collectors),
                 "without_failover": sum(1 for r in rows if not r["backup_collector_id"])},
        notes="Failover only helps when the backup collector has capacity and the "
              "same credential reach. Read this with the capacity results.",
    )
    res.sort_by_severity()
    return res


async def check_alert_noise(inv: Inventory) -> CheckResult:
    alerts = await inv.alerts()
    buckets: Dict[tuple, Dict[str, Any]] = {}
    for a in alerts:
        key = (a.get("monitorObjectName"), a.get("resourceTemplateName"), a.get("dataPointName"))
        b = buckets.setdefault(
            key,
            {"count": 0, "critical": 0, "acked": 0, "sdted": 0, "cleared": 0},
        )
        b["count"] += 1
        if ALERT_SEVERITY.get(a.get("severity")) == "critical":
            b["critical"] += 1
        if a.get("acked"):
            b["acked"] += 1
        if a.get("sdted"):
            b["sdted"] += 1
        if a.get("cleared"):
            b["cleared"] += 1

    total = len(alerts) or 1
    rows = []
    for (resource, datasource, datapoint), b in buckets.items():
        share = _pct(b["count"], total) or 0
        if share >= 10:
            sev, finding = "critical", "Dominates the alert volume for this window"
        elif share >= 3:
            sev, finding = "warn", "Contributes noticeable noise"
        else:
            continue
        rows.append(
            {
                "severity": sev,
                "resource": resource,
                "finding": finding,
                "datasource": datasource,
                "datapoint": datapoint,
                "alerts": b["count"],
                "share_pct": share,
                "critical_alerts": b["critical"],
                "acknowledged": b["acked"],
                "in_sdt": b["sdted"],
                "auto_cleared": b["cleared"],
            }
        )
    rows.sort(key=lambda r: r["alerts"], reverse=True)
    return CheckResult(
        columns=["severity", "resource", "finding", "datasource", "datapoint",
                 "alerts", "share_pct", "critical_alerts", "acknowledged",
                 "in_sdt", "auto_cleared"],
        rows=rows,
        summary={"alerts_in_window": len(alerts),
                 "window_hours": CONFIG["alert_window_hours"],
                 "distinct_signatures": len(buckets)},
        notes="A high auto-cleared count with few acknowledgements usually means "
              "the threshold is too tight, not that the resource is unhealthy.",
    )


async def check_alert_ack_sla(inv: Inventory) -> CheckResult:
    alerts = await inv.alerts()
    sla = CONFIG["ack_sla_minutes"]
    rows = []
    for a in alerts:
        if a.get("cleared") or a.get("acked") or a.get("sdted"):
            continue
        sev_name = ALERT_SEVERITY.get(a.get("severity"), "info")
        limit = sla.get(sev_name)
        age = _age_minutes(a.get("startEpoch"))
        if not limit or age is None:
            continue
        if age < limit:
            continue
        rows.append(
            {
                "severity": "critical" if age > limit * 3 else "warn",
                "resource": a.get("monitorObjectName"),
                "finding": f"Open and unacknowledged for {int(age)} minutes",
                "alert_severity": sev_name,
                "target_minutes": limit,
                "age_minutes": int(age),
                "datasource": a.get("resourceTemplateName"),
                "datapoint": a.get("dataPointName"),
                "instance": a.get("instanceName"),
                "alert_id": a.get("id"),
            }
        )
    res = CheckResult(
        columns=["severity", "resource", "finding", "alert_severity",
                 "target_minutes", "age_minutes", "datasource", "datapoint",
                 "instance", "alert_id"],
        rows=rows,
        summary={"alerts_in_window": len(alerts), "breaches": len(rows),
                 "window_hours": CONFIG["alert_window_hours"]},
        notes="Ageing unacknowledged alerts point at routing gaps. Cross-check the "
              "notification routing results for the same resources.",
    )
    res.sort_by_severity()
    return res


async def check_sdt_audit(inv: Inventory) -> CheckResult:
    sdts = await inv.sdts()
    rows = []
    for s in sdts:
        end = s.get("endDateTime")
        start = s.get("startDateTime")
        end_age = _age_days(end)
        target = (
            s.get("deviceDisplayName")
            or s.get("deviceGroupFullPath")
            or s.get("websiteName")
            or s.get("collectorDescription")
            or "unspecified"
        )
        if end and end_age is not None and end_age > 0:
            sev, finding = "info", "Expired"
        elif not end or int(end or 0) == 0:
            sev, finding = "critical", "Open-ended downtime with no end date"
        elif end_age is not None and end_age < -30:
            sev, finding = "warn", f"Runs for another {abs(int(end_age))} days"
        else:
            sev, finding = "ok", "Active and bounded"
        rows.append(
            {
                "severity": sev,
                "target": target,
                "finding": finding,
                "sdt_type": s.get("type") or s.get("sdtType"),
                "starts": start,
                "ends": end,
                "raised_by": s.get("admin"),
                "comment": (s.get("comment") or "")[:120],
                "sdt_id": s.get("id"),
            }
        )
    res = CheckResult(
        columns=["severity", "target", "finding", "sdt_type", "starts", "ends",
                 "raised_by", "comment", "sdt_id"],
        rows=rows,
        summary={"sdt_entries": len(sdts)},
        notes="Long or open-ended downtime windows silence monitoring without "
              "anyone noticing. Review these monthly.",
    )
    res.sort_by_severity()
    return res


async def check_integration_inventory(inv: Inventory) -> CheckResult:
    integrations = await inv.integrations()
    chains_blob = json.dumps(await inv.chains()).lower()
    rows = []
    probes: Dict[int, str] = {}

    if CONFIG["probe_integrations"]:
        probes = await _probe_endpoints(integrations)

    for i in integrations:
        name = i.get("name") or ""
        referenced = name.lower() in chains_blob or str(i.get("id")) in chains_blob
        url = i.get("url") or (i.get("alertData") or {}).get("url") or ""
        probe = probes.get(i.get("id"), "not probed")
        if not referenced:
            sev, finding = "warn", "Not referenced by any escalation chain"
        elif probe.startswith("unreachable"):
            sev, finding = "critical", "Endpoint did not respond"
        else:
            sev, finding = "ok", "Configured and referenced"
        rows.append(
            {
                "severity": sev,
                "integration": name,
                "finding": finding,
                "type": i.get("type"),
                "endpoint": url,
                "endpoint_check": probe,
                "description": (i.get("description") or "")[:120],
                "integration_id": i.get("id"),
            }
        )
    res = CheckResult(
        columns=["severity", "integration", "finding", "type", "endpoint",
                 "endpoint_check", "description", "integration_id"],
        rows=rows,
        summary={"integrations": len(integrations),
                 "endpoint_probe": "on" if CONFIG["probe_integrations"] else "off"},
        notes="Reference detection matches integration names and IDs inside "
              "escalation chain definitions. Treat it as a strong hint, not proof. "
              "Set probe_integrations to True in CONFIG to test endpoints.",
    )
    res.sort_by_severity()
    return res


async def _probe_endpoints(integrations: List[Dict[str, Any]]) -> Dict[int, str]:
    """Best-effort reachability test of HTTP integration endpoints."""
    out: Dict[int, str] = {}
    targets = []
    for i in integrations:
        url = i.get("url") or (i.get("alertData") or {}).get("url") or ""
        if url.startswith("http"):
            targets.append((i.get("id"), url))
    if not targets:
        return out
    timeout = CONFIG["probe_timeout_seconds"]
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        async def one(iid: int, url: str) -> None:
            try:
                resp = await client.head(url)
                out[iid] = f"responded {resp.status_code}"
            except Exception as exc:
                out[iid] = f"unreachable: {type(exc).__name__}"

        await asyncio.gather(*(one(i, u) for i, u in targets))
    return out


async def check_notification_routing(inv: Inventory) -> CheckResult:
    rules, chains = await asyncio.gather(inv.alert_rules(), inv.chains())
    chain_by_id = {c.get("id"): c for c in chains}
    rows = []
    for r in rules:
        chain_id = r.get("escalatingChainId")
        chain = chain_by_id.get(chain_id)
        if not chain:
            sev, finding = "critical", "Points at an escalation chain that no longer exists"
            chain_name, dest_count = "", 0
        else:
            chain_name = chain.get("name") or ""
            destinations = chain.get("destinations") or []
            dest_count = sum(len(d.get("stages") or []) or 1 for d in destinations)
            if not destinations:
                sev, finding = "critical", "Escalation chain has no destinations"
            elif not (r.get("devices") or r.get("deviceGroups")):
                sev, finding = "warn", "Rule matches no resources or groups"
            else:
                sev, finding = "ok", "Routes to a chain with destinations"
        rows.append(
            {
                "severity": sev,
                "alert_rule": r.get("name"),
                "finding": finding,
                "priority": r.get("priority"),
                "levels": r.get("levelStr"),
                "escalation_chain": chain_name,
                "destination_stages": dest_count,
                "datasource": r.get("datasource"),
                "rule_id": r.get("id"),
            }
        )
    orphan_chains = [
        c for c in chains
        if c.get("id") not in {r.get("escalatingChainId") for r in rules}
    ]
    for c in orphan_chains:
        rows.append(
            {
                "severity": "info",
                "alert_rule": "",
                "finding": "Escalation chain not used by any alert rule",
                "priority": "",
                "levels": "",
                "escalation_chain": c.get("name"),
                "destination_stages": len(c.get("destinations") or []),
                "datasource": "",
                "rule_id": "",
            }
        )
    res = CheckResult(
        columns=["severity", "alert_rule", "finding", "priority", "levels",
                 "escalation_chain", "destination_stages", "datasource", "rule_id"],
        rows=rows,
        summary={"alert_rules": len(rules), "escalation_chains": len(chains),
                 "unused_chains": len(orphan_chains)},
        notes="Routing gaps are the usual reason an alert fires but nobody is "
              "paged. Fix these before tuning thresholds.",
    )
    res.sort_by_severity()
    return res


async def check_api_token_hygiene(inv: Inventory) -> CheckResult:
    tokens = await inv.api_tokens()
    idle = CONFIG["token_idle_days"]
    rows = []
    for t in tokens:
        last_used = _age_days(t.get("lastUsedOn"))
        enabled = str(t.get("status", "")).lower() in ("active", "1", "true")
        if last_used is None:
            sev, finding = "warn", "Never used"
        elif last_used > idle:
            sev, finding = "warn", f"Unused for {int(last_used)} days"
        else:
            sev, finding = "ok", f"Last used {int(last_used)} days ago"
        if not enabled:
            sev, finding = "info", "Disabled"
        rows.append(
            {
                "severity": sev,
                "owner": t.get("adminName"),
                "finding": finding,
                "access_id": (t.get("accessId") or "")[:8] + "…",
                "status": t.get("status"),
                "created_days_ago": _age_days(t.get("createdOn")),
                "last_used_days_ago": last_used,
                "note": (t.get("note") or "")[:100],
                "token_id": t.get("id"),
            }
        )
    res = CheckResult(
        columns=["severity", "owner", "finding", "access_id", "status",
                 "created_days_ago", "last_used_days_ago", "note", "token_id"],
        rows=rows,
        summary={"tokens": len(tokens), "idle_threshold_days": idle},
        notes="Access keys are shown truncated and are never stored by this tool. "
              "This check needs a role with user management permission.",
    )
    res.sort_by_severity()
    return res


# ------------------------------------------------------------------ registry

@dataclass(frozen=True)
class CheckSpec:
    id: str
    name: str
    group: str
    description: str
    runner: Callable[[Inventory], Awaitable[CheckResult]]


REGISTRY: List[CheckSpec] = [
    CheckSpec("device_status", "Resource reachability", "Resources",
              "Which resources the collectors can and cannot reach.",
              check_device_status),
    CheckSpec("stale_discovery", "Stale discovery data", "Resources",
              "Resources whose auto properties have stopped refreshing.",
              check_stale_discovery),
    CheckSpec("duplicates", "Duplicate resources", "Resources",
              "Resources added more than once under different names.",
              check_duplicates),
    CheckSpec("property_coverage", "Property coverage", "Resources",
              "Resources missing the properties your grouping and routing rely on.",
              check_property_coverage),
    CheckSpec("ungrouped", "Ungrouped resources", "Resources",
              "Resources that sit only at the root group.",
              check_ungrouped_devices),
    CheckSpec("collector_status", "Collector availability", "Collectors",
              "Up and down state, version and load for every collector.",
              check_collector_status),
    CheckSpec("collector_capacity", "Collector capacity", "Collectors",
              "Load against configured capacity, with consolidation candidates.",
              check_collector_capacity),
    CheckSpec("collector_versions", "Collector version drift", "Collectors",
              "Collectors running behind the newest version in the portal.",
              check_collector_versions),
    CheckSpec("collector_failover", "Collector failover", "Collectors",
              "Collectors with no backup and the resources exposed by that.",
              check_collector_failover),
    CheckSpec("alert_noise", "Alert noise", "Alerting",
              "Resources and datapoints generating a disproportionate share of alerts.",
              check_alert_noise),
    CheckSpec("alert_ack_sla", "Unacknowledged alerts", "Alerting",
              "Open alerts past your acknowledgement targets.",
              check_alert_ack_sla),
    CheckSpec("sdt_audit", "Downtime windows", "Alerting",
              "Scheduled downtime that is open-ended, long running or expired.",
              check_sdt_audit),
    CheckSpec("integrations", "Integration health", "Integrations",
              "Configured integrations, whether anything routes to them, and "
              "optionally whether the endpoint answers.",
              check_integration_inventory),
    CheckSpec("notification_routing", "Notification routing", "Integrations",
              "Alert rules and escalation chains that would drop a notification.",
              check_notification_routing),
    CheckSpec("api_tokens", "API token hygiene", "Governance",
              "Access keys that are unused, never used or disabled.",
              check_api_token_hygiene),
]

BY_ID = {spec.id: spec for spec in REGISTRY}


async def run_check(spec: CheckSpec, inv: Inventory) -> CheckResult:
    """Run one check, turning permission and API problems into a readable row."""
    try:
        return await spec.runner(inv)
    except (LMAuthError, LMApiError) as exc:
        return CheckResult(
            columns=["severity", "finding"],
            rows=[{"severity": "info", "finding": str(exc)}],
            summary={"status": "not completed"},
            notes="This check did not complete. The most common cause is a role "
                  "without permission for the endpoint it needs.",
        )
    except Exception as exc:  # pragma: no cover - defensive
        return CheckResult(
            columns=["severity", "finding"],
            rows=[{"severity": "info", "finding": f"{type(exc).__name__}: {exc}"}],
            summary={"status": "not completed"},
        )
