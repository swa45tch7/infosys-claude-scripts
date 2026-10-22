#!/usr/bin/env python3
"""Turn collected portal data into the statuses, findings and narrative of the report.

Numbers come straight from the REST API. Statuses and findings are rule-based
(report_rules.json plus the shared Operations Portal Maintenance standards).
Anything the API cannot show is written as a clearly marked consultant
placeholder rather than guessed.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
OPS_DIR = HERE.parent / "Operations Portal Maintenance"
for _path in (HERE, OPS_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import engine  # noqa: E402
from engine import PortalSnapshot, _boolish, _version_label  # noqa: E402
from portal_data import Dataset, PortalData  # noqa: E402

RULES_PATH = HERE / "report_rules.json"
IST = timezone(timedelta(hours=5, minutes=30), "IST")

DEVICE_TYPES = {0: "Standard", 2: "AWS", 4: "Azure", 6: "Service", 7: "GCP"}
SEVERITY = {4: "critical", 3: "error", 2: "warn", 1: "info"}
STATUS_SCORE = {"GREEN": 0.0, "GREEN / AMBER": 0.5, "AMBER": 1.0, "AMBER / RED": 1.5, "RED": 2.0}
HEALTH_FROM_STATUS = {
    "GREEN": "Strong",
    "GREEN / AMBER": "Developing",
    "AMBER": "Developing",
    "AMBER / RED": "At risk",
    "RED": "At risk",
    "UNKNOWN": "Unknown",
}
MODULE_DATASETS = (
    ("datasources", "DataSources"),
    ("eventsources", "EventSources"),
    ("configsources", "ConfigSources"),
    ("propertysources", "PropertySources"),
    ("topologysources", "TopologySources"),
    ("jobmonitors", "JobMonitors"),
    ("logsources", "LogSources"),
    ("appliesto_functions", "AppliesTo functions"),
    ("sysoid_maps", "SNMP SysOID maps"),
)
ITSM_TYPES = ("servicenow", "jira", "autotask", "connectwise", "bmc", "remedy", "cherwell")
DISCOVERY_PATTERN = re.compile(
    r"active\s*discovery|\binstances?\b.*\b(added|deleted|removed)\b|\b(added|deleted|removed)\b.*\binstances?\b",
    re.IGNORECASE,
)
WIRELESS_PATTERN = re.compile(r"wireless|wlan|wifi|access.?point|\bap\b|meraki|aruba|ruckus", re.IGNORECASE)
NETWORK_PATTERN = re.compile(
    r"interface|ping|bgp|ospf|snmp|cisco|juniper|network|vpn|tunnel|switch|router|firewall|palo|fortinet",
    re.IGNORECASE,
)


def placeholder(text: str) -> str:
    return f"[Consultant to complete: {text}]"


def load_rules(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or RULES_PATH).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #
def num(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.1f}"
    return f"{int(value):,}"


def pct(part: Any, whole: Any) -> float | None:
    if part is None or not whole:
        return None
    return round(float(part) / float(whole) * 100, 1)


def pct_text(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}%"


def join_words(items: list[str]) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def agree(value: Any, singular: str, plural: str) -> str:
    return singular if value == 1 else plural


def count_of(value: Any, singular: str, plural: str | None = None) -> str:
    return f"{num(value)} {agree(value, singular, plural or singular + 's')}"


def short_number(value: float) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}m"
    if value >= 10_000:
        return f"{value / 1000:.0f}k"
    return num(round(value))


MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def long_date(moment: datetime) -> str:
    return f"{moment.day} {MONTH_NAMES[moment.month - 1]} {moment.year}"


def unavailable(*datasets: Dataset) -> str:
    reasons = [f"{d.label}: {d.error}" for d in datasets if not d.ok]
    return "Data unavailable: " + "; ".join(reasons)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _epoch_seconds(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number / 1000 if number > 10_000_000_000 else number


def device_type(device: dict[str, Any]) -> str:
    return DEVICE_TYPES.get(_int(device.get("deviceType")), "Other")


# --------------------------------------------------------------------------- #
# Report structures
# --------------------------------------------------------------------------- #
@dataclass
class ReportContext:
    customer: str
    portal: str
    generated_at: datetime
    prepared_by: str = ""
    subtitle: str = ""
    contracted_resources: int | None = None

    @property
    def date_text(self) -> str:
        return long_date(self.generated_at)


@dataclass
class ScoreRow:
    key: str
    area: str
    status: str
    evidence: str


@dataclass
class ModuleRow:
    capability: str
    observed: str
    health: str
    motion: str


@dataclass
class Assessment:
    context: ReportContext
    metrics: dict[str, Any]
    overall_status: str
    overall_condition: str
    conclusion_title: str
    conclusion_text: str
    cover_facts: list[tuple[str, str]]
    summary_paragraphs: list[tuple[str, str]]
    kpi_tiles: list[tuple[str, str, str]]
    immediate_direction: list[str]
    scorecard: list[ScoreRow]
    interpretation: str
    good: list[str]
    weak: list[str]
    missing: list[str]
    anchors: list[str]
    expansion: list[str]
    risks: list[str]
    modules: list[ModuleRow]
    edwin_status: str
    edwin_subtitle: str
    edwin_text: str
    credible: list[str]
    entry_criteria: list[str]
    use_cases: list[tuple[str, str, str]]
    execution: list[tuple[str, str, str]]
    success_tiles: list[tuple[str, str, str]]
    plan: list[tuple[str, str, str]]
    decisions: list[str]
    positioning: str
    sources: list[tuple[str, str, str]]
    validation: list[str]
    collection_log: list[tuple[str, str, str, str]]
    top_sources: list[tuple[str, int, float, str]] = field(default_factory=list)
    collector_rows: list[dict[str, Any]] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
def portal_snapshot(data: PortalData) -> PortalSnapshot:
    def items(name: str) -> list[dict[str, Any]]:
        dataset = data.get(name)
        return dataset.items if dataset.ok else []

    collectors = [
        dict(c, collectorVersion=c.get("collectorVersion") or c.get("build"))
        for c in items("collectors")
    ]
    return PortalSnapshot(
        devices=[d for d in items("devices") if device_type(d) != "Service"],
        device_properties=[],
        collectors=collectors,
        alerts=items("alerts_24h"),
        alert_rules=items("alert_rules"),
        chains=items("escalation_chains"),
        sdts=items("sdts"),
        tokens=items("api_tokens"),
        api_ok=True,
        portal=data.portal,
    )


def compute_metrics(
    data: PortalData, rules: dict[str, Any], standards: dict[str, Any]
) -> dict[str, Any]:
    now = data.collected_at
    snap = portal_snapshot(data)
    m: dict[str, Any] = {"now": now}
    _device_metrics(m, data, snap, rules, standards, now)
    _collector_metrics(m, data, snap, rules, standards, now)
    _alert_metrics(m, data, snap, standards, now)
    _routing_metrics(m, data, snap, standards, now)
    _module_metrics(m, data, rules)
    _adoption_metrics(m, data, now)
    _governance_metrics(m, data, rules, standards, now)
    return m


def _device_metrics(m, data, snap, rules, standards, now) -> None:
    devices = data.get("devices")
    if not devices.ok:
        return
    items = devices.items
    resources = [d for d in items if device_type(d) != "Service"]
    m["resources"] = len(resources)
    m["services"] = sum(1 for d in items if device_type(d) == "Service")
    types = Counter(device_type(d) for d in resources)
    m["resource_types"] = dict(types)
    root = None
    groups = data.get("device_groups")
    if groups.ok:
        m["device_groups"] = groups.count
        root = next((g for g in groups.items if str(g.get("id")) == "1"), None)

    def group_count(key: str) -> int:
        return _int(root.get(key)) if root else 0

    m["cloud"] = {
        "AWS": max(types.get("AWS", 0), group_count("numOfAWSDevices")),
        "Azure": max(types.get("Azure", 0), group_count("numOfAzureDevices")),
        "GCP": max(types.get("GCP", 0), group_count("numOfGcpDevices")),
    }
    m["cloud_total"] = sum(m["cloud"].values())
    m["kubernetes"] = (
        _int(root.get("numOfKubernetesDevices")) if root and "numOfKubernetesDevices" in root else None
    )
    dead = [d for d in resources if str(d.get("hostStatus") or "").lower().startswith("dead")]
    m["dead"] = len(dead)
    m["dead_pct"] = pct(len(dead), len(resources))
    m["alerting_disabled"] = sum(1 for d in resources if _boolish(d.get("disableAlerting")))
    m["in_sdt"] = sum(
        1
        for d in resources
        if d.get("sdtStatus") and not str(d.get("sdtStatus")).lower().startswith("none")
    )
    status_counts = Counter(
        "Dead" if str(d.get("hostStatus") or "").lower().startswith("dead") else "Normal"
        for d in resources
    )
    m["resource_status"] = dict(status_counts)
    ungrouped = engine.check_ungrouped_devices(snap, standards, now)
    m["ungrouped"] = len(ungrouped.findings)
    m["ungrouped_pct"] = pct(m["ungrouped"], len(resources))
    stale = engine.check_stale_discovery(snap, standards, now)
    m["stale_discovery"] = stale.counts()["critical"]
    m["stale_days"] = int(standards["stale_discovery_days"])
    duplicates = engine.check_duplicates(snap, standards, now)
    m["duplicate_sets"] = len(duplicates.findings)


def _collector_metrics(m, data, snap, rules, standards, now) -> None:
    collectors = data.get("collectors")
    if not collectors.ok:
        return
    cols = snap.collectors
    m["collectors"] = len(cols)
    down = [c for c in cols if _boolish(c.get("isDown"))]
    m["collectors_down"] = len(down)
    m["collectors_down_with_resources"] = sum(1 for c in down if _int(c.get("numberOfHosts")) > 0)
    groups = data.get("collector_groups")
    abcg = (
        {str(g.get("id")) for g in groups.items if _boolish(g.get("autoBalance"))}
        if groups.ok
        else set()
    )
    m["collectors_abcg"] = sum(1 for c in cols if str(c.get("collectorGroupId")) in abcg)
    standalone = [c for c in cols if str(c.get("collectorGroupId")) not in abcg]
    failover = engine.check_collector_failover(replace(snap, collectors=standalone), standards, now)
    m["collectors_no_failover"] = int(failover.summary.get("without_failover", 0))
    with_resources = sum(1 for c in cols if _int(c.get("numberOfHosts")) > 0)
    m["collectors_no_failover_pct"] = pct(m["collectors_no_failover"], with_resources)
    capacity = engine.check_collector_capacity(snap, standards, now)
    m["collectors_capacity_critical"] = capacity.counts()["critical"]
    m["collectors_capacity_warn"] = capacity.counts()["warn"]
    m["collector_critical_pct"] = standards["collector_critical_pct"]
    versions = engine.check_collector_versions(snap, standards, now)
    m["collector_newest"] = versions.summary.get("newest_in_portal", "n/a")
    m["collectors_version_critical"] = versions.counts()["critical"]
    m["collectors_behind"] = versions.counts()["critical"] + versions.counts()["warn"]
    m["collectors_ungrouped"] = sum(
        1
        for c in cols
        if str(c.get("collectorGroupName") or "").strip().lower()
        in ("", "@default", "default", "ungrouped")
    )
    sizes = Counter(str(c.get("collectorSize") or "unknown").lower() for c in cols)
    m["collector_sizes"] = dict(sizes)
    m["collectors_custom_size"] = sizes.get("custom", 0)
    instances = [_int(c.get("numberOfInstances")) for c in cols]
    m["instances"] = sum(instances)
    if m.get("resources"):
        m["instances_per_resource"] = round(m["instances"] / m["resources"], 1)
    loaded = sorted(
        _int(c.get("numberOfInstances"))
        for c in cols
        if not _boolish(c.get("isDown")) and _int(c.get("numberOfHosts")) > 0
    )
    m["collectors_idle"] = sum(
        1 for c in cols if not _boolish(c.get("isDown")) and _int(c.get("numberOfHosts")) == 0
    )
    if loaded:
        median = statistics.median(loaded)
        m["collector_load_min"] = loaded[0]
        m["collector_load_max"] = loaded[-1]
        m["collector_imbalance"] = round(loaded[-1] / median, 1) if median else None
    sev_by_id: dict[str, list[str]] = {}
    for result in (capacity, versions, failover):
        for finding in result.findings:
            if finding.severity in ("critical", "warn"):
                key = str(finding.details.get("collector_id"))
                sev_by_id.setdefault(key, []).append(finding.finding)
    rows = []
    for c in cols:
        cid = str(c.get("id"))
        issues = list(sev_by_id.get(cid, []))
        is_down = _boolish(c.get("isDown"))
        if is_down:
            issues.insert(0, "Collector is down")
        rows.append(
            {
                "id": cid,
                "name": str(c.get("description") or c.get("hostname") or cid),
                "down": is_down,
                "size": str(c.get("collectorSize") or "unknown"),
                "hosts": _int(c.get("numberOfHosts")),
                "instances": _int(c.get("numberOfInstances")),
                "failover": bool(c.get("backupAgentId")) or str(c.get("collectorGroupId")) in abcg,
                "version": _version_label(c.get("collectorVersion")),
                "group": str(c.get("collectorGroupName") or ""),
                "issues": issues,
            }
        )
    rows.sort(key=lambda r: r["instances"], reverse=True)
    m["collector_rows"] = rows


def _source_name(alert: dict[str, Any]) -> str:
    # Multi-instance DataSource names end in "-" by LogicMonitor convention (e.g. "BGP-").
    name = str(alert.get("resourceTemplateName") or alert.get("type") or "").rstrip("-").strip()
    return name or "Unknown"


def _alert_metrics(m, data, snap, standards, now) -> None:
    active = data.get("alerts_active")
    if active.ok:
        items = active.items
        m["open_alerts"] = active.count
        m["open_alerts_partial"] = active.partial
        m["open_by_severity"] = dict(Counter(SEVERITY.get(_int(a.get("severity")), "info") for a in items))
        m["open_acked_pct"] = pct(sum(1 for a in items if _boolish(a.get("acked"))), len(items))
        m["open_in_sdt"] = sum(1 for a in items if _boolish(a.get("sdted")))
    day = data.get("alerts_24h")
    if day.ok:
        items = day.items
        total = len(items)
        m["alerts_24h"] = total
        m["alerts_24h_partial"] = day.partial
        m["alerts_per_hour"] = round(total / 24)
        m["alerts_per_minute"] = round(total / 1440, 1)
        if m.get("resources"):
            m["alerts_per_resource"] = round(total / m["resources"], 2)
        m["day_by_severity"] = dict(Counter(SEVERITY.get(_int(a.get("severity")), "info") for a in items))
        by_source = Counter(_source_name(a) for a in items)
        top = []
        for name, count in by_source.most_common(10):
            devices = Counter(str(a.get("monitorObjectName") or "") for a in items if _source_name(a) == name)
            top_device = devices.most_common(1)[0][0] if devices else ""
            top.append((name, count, pct(count, total) or 0.0, top_device))
        m["top_sources"] = top
        m["top_devices"] = Counter(str(a.get("monitorObjectName") or "") for a in items).most_common(10)
        hourly = {sev: [0] * 24 for sev in ("warn", "error", "critical")}
        start = now - 86400
        for alert in items:
            epoch = _epoch_seconds(alert.get("startEpoch"))
            if epoch is None:
                continue
            slot = min(23, max(0, int((epoch - start) // 3600)))
            sev = SEVERITY.get(_int(alert.get("severity")), "warn")
            hourly.setdefault(sev, [0] * 24)[slot] += 1
        m["alerts_hourly"] = hourly
        noise = engine.check_alert_noise(snap, standards, now)
        m["noise_signatures"] = len(noise.findings)
        m["noise_critical"] = noise.counts()["critical"]
        m["noise_warn_pct"] = standards["noise_share_warn_pct"]
        ack = engine.check_alert_ack_sla(snap, standards, now)
        m["ack_breaches"] = len(ack.findings)
        if any("rule" in a for a in items):
            m["alerts_unrouted"] = sum(1 for a in items if not a.get("rule"))
    both = (active.items if active.ok else []) + (day.items if day.ok else [])
    if any("dependencyRole" in a or "dependencyRoutingState" in a for a in both):
        m["dependent_alerts"] = sum(
            1
            for a in both
            if str(a.get("dependencyRole") or "").strip().lower() not in ("", "none", "null")
        )
    if any("adAlert" in a for a in both):
        m["dynamic_threshold_alerts"] = sum(1 for a in both if _boolish(a.get("adAlert")))


def _routing_metrics(m, data, snap, standards, now) -> None:
    rules_ds = data.get("alert_rules")
    chains_ds = data.get("escalation_chains")
    if rules_ds.ok:
        m["alert_rules"] = rules_ds.count
        m["catch_all_rule"] = any(
            ("*" in (r.get("devices") or []) or "*" in (r.get("deviceGroups") or []))
            and str(r.get("datasource") or "*") == "*"
            and str(r.get("levelStr") or "All").lower() == "all"
            for r in rules_ds.items
        )
    if chains_ds.ok:
        m["chains"] = chains_ds.count
        m["chains_throttled"] = sum(1 for c in chains_ds.items if _boolish(c.get("enableThrottling")))
        m["chains_empty"] = sum(1 for c in chains_ds.items if not c.get("destinations"))
    if rules_ds.ok and chains_ds.ok:
        routing = engine.check_notification_routing(snap, standards, now)
        m["rules_invalid_chain"] = sum(
            1 for f in routing.findings if f.severity == "critical"
        )
        m["rules_duplicate_priority"] = sum(
            1 for f in routing.findings if str(f.subject).startswith("priority ")
        )
        used = {str(r.get("escalatingChainId")) for r in rules_ds.items}
        m["chains_unused"] = sum(1 for c in chains_ds.items if str(c.get("id")) not in used)
    integrations = data.get("integrations")
    if integrations.ok:
        types = Counter(str(i.get("type") or "unknown").lower() for i in integrations.items)
        m["integrations"] = integrations.count
        m["integration_types"] = dict(types)
        m["itsm_integrations"] = sorted(t for t in types if any(x in t for x in ITSM_TYPES))
    sdts = data.get("sdts")
    if sdts.ok:
        audit = engine.check_sdt_audit(snap, standards, now)
        m["sdts"] = sdts.count
        m["sdts_effective"] = sum(1 for s in sdts.items if _boolish(s.get("isEffective")))
        m["sdts_open_ended"] = audit.counts()["critical"]
        m["sdts_long"] = audit.counts()["warn"]
        m["sdt_long_days"] = int(standards["sdt_long_days"])


def _is_dynamic(datapoint: dict[str, Any]) -> bool:
    if _boolish(datapoint.get("adAdvSettingEnabled")):
        return True
    return any(
        str(datapoint.get(key) or "").strip()
        for key in ("warnAdAdvSetting", "errorAdAdvSetting", "criticalAdAdvSetting")
    )


def _module_metrics(m, data, rules) -> None:
    counts: dict[str, int] = {}
    missing: list[str] = []
    for name, label in MODULE_DATASETS:
        dataset = data.get(name)
        if dataset.ok and dataset.count is not None:
            counts[label] = dataset.count
        else:
            missing.append(label)
    m["module_counts"] = counts
    m["modules_missing"] = missing
    if counts:
        m["modules_total"] = sum(counts.values())
    clone_re = re.compile(rules["clone_name_pattern"])
    suspects: list[str] = []
    datasources = data.get("datasources")
    if datasources.ok:
        items = datasources.items
        if any("dataPoints" in d for d in items):
            points = [dp for d in items for dp in (d.get("dataPoints") or [])]
            m["datapoint_definitions"] = len(points)
            m["dynamic_threshold_datapoints"] = sum(1 for dp in points if _is_dynamic(dp))
        if any("installationMetadata" in d for d in items):
            def origin(d: dict[str, Any]) -> Any:
                meta = d.get("installationMetadata") or {}
                return (
                    meta.get("originRegistryId")
                    or meta.get("originLineageId")
                    or meta.get("originAuthorNamespace")
                )

            m["datasources_custom"] = sum(1 for d in items if not origin(d))
            m["datasources_modified"] = sum(
                1 for d in items if _boolish((d.get("installationMetadata") or {}).get("isChangedFromOrigin"))
            )
        suspects += [str(d.get("name")) for d in items if clone_re.search(str(d.get("name") or ""))]
    configsources = data.get("configsources")
    if configsources.ok:
        suspects += [
            str(d.get("name")) for d in configsources.items if clone_re.search(str(d.get("name") or ""))
        ]
        m["configsources"] = configsources.count
    logsources = data.get("logsources")
    if logsources.ok:
        m["logsources"] = logsources.count
    m["clone_suspects"] = len(suspects)
    m["clone_examples"] = suspects[:5]
    m["clones_checked"] = len(data.module_usage)
    m["clones_unused"] = sum(1 for count in data.module_usage.values() if count == 0)


def _adoption_metrics(m, data, now) -> None:
    dashboards = data.get("dashboards")
    if dashboards.ok:
        m["dashboards"] = dashboards.count
        m["dashboards_shared_pct"] = pct(
            sum(1 for d in dashboards.items if _boolish(d.get("sharable"))), len(dashboards.items)
        )
    widgets = data.get("widgets")
    if widgets.ok:
        m["widgets"] = widgets.count
        if m.get("dashboards"):
            m["widgets_per_dashboard"] = round((widgets.count or 0) / m["dashboards"], 1)
    reports = data.get("reports")
    if reports.ok:
        m["reports"] = reports.count
        m["reports_24h"] = sum(
            1
            for r in reports.items
            if (_epoch_seconds(r.get("lastGenerateOn")) or 0) >= now - 86400
        )
        m["reports_scheduled"] = sum(
            1 for r in reports.items if str(r.get("schedule") or "").strip() not in ("", "0")
        )
        m["reports_with_recipients"] = sum(1 for r in reports.items if r.get("recipients"))
    websites = data.get("websites")
    if websites.ok:
        items = websites.items
        m["websites"] = websites.count
        m["websites_by_type"] = dict(Counter(str(w.get("type") or "unknown").lower() for w in items))
        m["websites_external"] = sum(1 for w in items if not _boolish(w.get("isInternal")))
        m["websites_internal"] = sum(1 for w in items if _boolish(w.get("isInternal")))
        m["websites_stopped"] = sum(1 for w in items if _boolish(w.get("stopMonitoring")))
        m["websites_alerting"] = sum(
            1
            for w in items
            if str(w.get("overallAlertLevel") or "").lower() in ("warn", "error", "critical")
        )
        m["websites_failing_pct"] = pct(m["websites_alerting"], len(items))


def _governance_metrics(m, data, rules, standards, now) -> None:
    users = data.get("users")
    inactive_days = int(rules["user_inactive_days"])
    m["user_inactive_days"] = inactive_days
    if users.ok:
        items = users.items
        people = [u for u in items if not _boolish(u.get("apionly"))]
        active = [u for u in people if str(u.get("status") or "active").lower() == "active"]
        m["users"] = users.count
        m["users_active"] = len(active)
        m["users_api_only"] = len(items) - len(people)
        m["users_inactive"] = sum(
            1
            for u in active
            if (_epoch_seconds(u.get("lastLoginOn")) or 0) < now - inactive_days * 86400
        )
        if any("twoFAEnabled" in u for u in items):
            m["users_no_2fa"] = sum(1 for u in active if not _boolish(u.get("twoFAEnabled")))
        m["users_admin"] = sum(
            1
            for u in active
            if any("admin" in str(r.get("name") or "").lower() for r in (u.get("roles") or []))
        )
    roles = data.get("roles")
    if roles.ok:
        m["roles"] = roles.count
    tokens = data.get("api_tokens")
    idle_days = int(standards["token_idle_days"])
    m["token_idle_days"] = idle_days
    if tokens.ok:
        enabled = [
            t
            for t in tokens.items
            if str(t.get("status")).strip().lower() in ("2", "active", "enabled", "true")
        ]
        m["api_tokens"] = tokens.count
        m["api_tokens_enabled"] = len(enabled)
        m["api_tokens_idle"] = sum(
            1
            for t in enabled
            if (_epoch_seconds(t.get("lastUsedOn")) or 0) < now - idle_days * 86400
        )
    netscans = data.get("netscans")
    if netscans.ok:
        m["netscans"] = netscans.count
        m["netscans_scheduled"] = sum(
            1
            for n in netscans.items
            if str((n.get("schedule") or {}).get("type") or "manual").lower() != "manual"
        )
    audit = data.get("audit_logs")
    if audit.ok:
        items = audit.items
        m["audit_entries"] = len(items)
        m["audit_partial"] = audit.partial
        epochs = [e for e in (_epoch_seconds(a.get("happenedOn")) for a in items) if e]
        m["audit_hours"] = round((now - min(epochs)) / 3600) if epochs else 0
        discovery = sum(1 for a in items if DISCOVERY_PATTERN.search(str(a.get("description") or "")))
        m["audit_discovery"] = discovery
        m["audit_discovery_pct"] = pct(discovery, len(items))
        m["audit_top_users"] = Counter(str(a.get("username") or "") for a in items).most_common(3)


# --------------------------------------------------------------------------- #
# Scorecard
# --------------------------------------------------------------------------- #
def _status_row(key: str, area: str, fn: Callable[[], tuple[str, str]]) -> ScoreRow:
    try:
        status, evidence = fn()
    except Exception as exc:  # a rule bug must not stop the report
        status, evidence = "UNKNOWN", f"Data unavailable: could not evaluate ({exc.__class__.__name__})"
    return ScoreRow(key, area, status, evidence)


def build_scorecard(
    data: PortalData, m: dict[str, Any], rules: dict[str, Any], ctx: ReportContext
) -> list[ScoreRow]:
    devices = data.get("devices")
    collectors = data.get("collectors")

    def core() -> tuple[str, str]:
        if not devices.ok:
            return "UNKNOWN", unavailable(devices)
        resources = m["resources"]
        if ctx.contracted_resources:
            used = pct(resources, ctx.contracted_resources) or 0
            headroom = ctx.contracted_resources - resources
            status = "GREEN" if used <= 95 else "AMBER" if used <= 100 else "RED"
            return status, (
                f"{num(resources)} monitored resources against {num(ctx.contracted_resources)} "
                f"contracted ({used:.1f}%), leaving {num(headroom)} resources of headroom. "
                "Rolling month-to-date billed usage is not exposed through the REST API; "
                "confirm it on the portal Usage page."
            )
        return "UNKNOWN", (
            f"{num(resources)} monitored resources. Contracted entitlement and rolling "
            "month-to-date billed usage are not exposed through the REST API. "
            + placeholder("contracted resources and billed usage from the portal Usage page.")
        )

    def footprint() -> tuple[str, str]:
        if not devices.ok:
            return "UNKNOWN", unavailable(devices)
        types = m.get("resource_types", {})
        mix = join_words(
            [
                f"{num(v)} {k.lower() if k in ('Standard', 'Other') else k}"
                for k, v in sorted(types.items(), key=lambda x: -x[1])
            ]
        )
        dead_pct = m.get("dead_pct") or 0
        ungrouped_pct = m.get("ungrouped_pct") or 0
        if dead_pct >= rules["dead_resource_pct_red"]:
            status, verdict = "RED", "A material share of the estate is not being collected."
        elif dead_pct >= rules["dead_resource_pct_amber"] or ungrouped_pct >= rules["ungrouped_resource_pct_amber"]:
            status, verdict = "AMBER", "Coverage hygiene needs attention before the data is used for correlation."
        else:
            status, verdict = "GREEN", "Coverage hygiene is in good order."
        return status, (
            f"{num(m['resources'])} resources ({mix}). {num(m['dead'])} {agree(m['dead'], 'is', 'are')} dead "
            f"({pct_text(m.get('dead_pct'))}), {num(m['ungrouped'])} {agree(m['ungrouped'], 'sits', 'sit')} "
            f"only in the root group, {num(m['alerting_disabled'])} {agree(m['alerting_disabled'], 'has', 'have')} "
            f"alerting disabled and {num(m['stale_discovery'])} {agree(m['stale_discovery'], 'has', 'have')} "
            f"auto-discovery data older than {m['stale_days']} days. {verdict}"
        )

    def telemetry() -> tuple[str, str]:
        if not collectors.ok:
            return "UNKNOWN", unavailable(collectors)
        per = m.get("instances_per_resource")
        status = "GREEN" if per is not None and per >= rules["instances_per_resource_min"] else "AMBER"
        per_text = f", approximately {per:.0f} per resource" if per is not None else ""
        dp = m.get("datapoint_definitions")
        dp_text = f" DataSource definitions hold {num(dp)} datapoints." if dp is not None else ""
        return status, (
            f"{num(m['instances'])} monitored instances{per_text}.{dp_text} Total and complex "
            "datapoint counts are not exposed through the REST API. "
            + placeholder("datapoint totals from Settings > Account Information.")
        )

    def datapoint_collection() -> tuple[str, str]:
        return "UNKNOWN", (
            "Monthly datapoint collection usage and entitlement are shown on the portal Usage "
            "page and are not exposed through the REST API. "
            + placeholder("collections used versus contracted.")
        )

    def alerting() -> tuple[str, str]:
        active, day = data.get("alerts_active"), data.get("alerts_24h")
        if not active.ok and not day.ok:
            return "UNKNOWN", unavailable(active, day)
        parts = []
        if day.ok:
            prefix = "At least " if m.get("alerts_24h_partial") else ""
            per_res = m.get("alerts_per_resource")
            per_res_text = f", {per_res:.1f} per resource" if per_res is not None else ""
            parts.append(
                f"{prefix}{num(m['alerts_24h'])} alerts in the previous day, equivalent to "
                f"{num(m['alerts_per_hour'])} per hour or {m['alerts_per_minute']:.1f} per minute{per_res_text}"
            )
        else:
            parts.append(f"Prior-day volume unavailable ({day.error})")
        if active.ok:
            parts.append(
                f"with {num(m['open_alerts'])} open alerts ({pct_text(m.get('open_acked_pct'))} acknowledged)"
            )
        text = ", ".join(parts) + "."
        dep = m.get("dependent_alerts")
        dyn = m.get("dynamic_threshold_datapoints")
        dyn_alerts = m.get("dynamic_threshold_alerts")
        if dep == 0:
            text += " No alerts carry a dependent alert mapping role"
        elif dep:
            text += f" {num(dep)} alerts carry a dependent alert mapping role"
        else:
            text += " Dependent alert mapping could not be evidenced from alert data"
        if dyn == 0 and not dyn_alerts:
            text += " and no dynamic thresholds are defined in DataSource definitions."
        elif dyn:
            text += f"; {num(dyn)} datapoints have dynamic thresholds defined."
        else:
            text += "."
        if m.get("noise_signatures"):
            text += (
                f" {count_of(m['noise_signatures'], 'alert signature')} "
                f"{agree(m['noise_signatures'], 'exceeds', 'each exceed')} "
                f"{m['noise_warn_pct']}% of prior-day volume."
            )
        per_res = m.get("alerts_per_resource") or 0
        open_per_res = (m.get("open_alerts") or 0) / m["resources"] if m.get("resources") else 0
        if per_res >= rules["alerts_per_resource_day_red"] or open_per_res >= rules["open_alerts_per_resource_red"]:
            status = "RED"
        elif per_res >= rules["alerts_per_resource_day_amber"] or m.get("noise_critical"):
            status = "AMBER"
        else:
            status = "GREEN"
        return status, text

    def dashboards() -> tuple[str, str]:
        dash, rep = data.get("dashboards"), data.get("reports")
        if not dash.ok and not rep.ok:
            return "UNKNOWN", unavailable(dash, rep)
        bits = []
        if dash.ok:
            wpd = m.get("widgets_per_dashboard")
            widget_text = (
                f", {num(m['widgets'])} widgets ({wpd:.1f} per dashboard)" if wpd is not None else ""
            )
            bits.append(f"{num(m['dashboards'])} dashboards{widget_text}")
        if rep.ok:
            bits.append(f"{num(m['reports_24h'])} of {num(m['reports'])} reports generated in the last 24 hours")
        text = join_words(bits) + ". Configuration is visible, but dashboard views, report recipients' use and decision usage are not exposed through the API."
        if not dash.ok or not rep.ok:
            text += " " + unavailable(dash, rep)
        if dash.ok and not m["dashboards"]:
            status = "RED"
        elif not rep.ok or not m.get("reports_24h"):
            status = "AMBER"
        else:
            status = "GREEN / AMBER"
        return status, text

    def services() -> tuple[str, str]:
        if not devices.ok:
            return "UNKNOWN", unavailable(devices)
        if m["services"]:
            return "AMBER", (
                f"{count_of(m['services'], 'service')} {agree(m['services'], 'is', 'are')} configured. "
                "Coverage, ownership, criticality, "
                "service health, topology quality and operational adoption are not visible through the API."
            )
        return "UNKNOWN", "No Service Insights services are configured. Confirm whether Service Insights is in scope."

    def logs() -> tuple[str, str]:
        logsources = data.get("logsources")
        configured = (
            f"{count_of(m['logsources'], 'LogSource')} {agree(m['logsources'], 'is', 'are')} configured. "
            if logsources.ok
            else unavailable(logsources) + ". "
        )
        return "UNKNOWN", (
            configured
            + "Logs ingestion volume, partitions, LogAlerts and tracked queries are not exposed "
            "through the REST API endpoints used. "
            + placeholder("Logs usage versus entitlement from the portal Usage page.")
        )

    def uptime() -> tuple[str, str]:
        websites = data.get("websites")
        checks = (
            f"{num(m['websites_external'])} external and {num(m['websites_internal'])} internal "
            f"website {agree(m['websites_external'] + m['websites_internal'], 'check is', 'checks are')} configured. "
            if websites.ok
            else unavailable(websites) + ". "
        )
        return "UNKNOWN", (
            "LM Uptime checks executed versus contracted are not exposed through the REST API. "
            + checks
            + placeholder("Uptime usage versus entitlement from the portal Usage page.")
        )

    def websites() -> tuple[str, str]:
        ds = data.get("websites")
        if not ds.ok:
            return "UNKNOWN", unavailable(ds)
        if not m["websites"]:
            return "AMBER", "No website checks are configured. Critical user journeys have no synthetic coverage."
        types = m["websites_by_type"]
        failing = m.get("websites_failing_pct") or 0
        status = (
            "AMBER"
            if failing >= rules["website_failing_pct_amber"] or m["websites_stopped"] or not m["websites_external"]
            else "GREEN"
        )
        return status, (
            f"{count_of(m['websites'], 'website check')} ({num(types.get('webcheck', 0))} web, "
            f"{num(types.get('pingcheck', 0))} ping; {num(m['websites_external'])} external). "
            f"{num(m['websites_stopped'])} {agree(m['websites_stopped'], 'has', 'have')} monitoring stopped and "
            f"{num(m['websites_alerting'])} {agree(m['websites_alerting'], 'is', 'are')} currently alerting. "
            "Business journeys and geographic coverage need validation."
        )

    def collector_row() -> tuple[str, str]:
        if not collectors.ok:
            return "UNKNOWN", unavailable(collectors)
        abcg = (
            f" ({num(m['collectors_abcg'])} protected by auto-balanced groups)" if m.get("collectors_abcg") else ""
        )
        text = (
            f"{count_of(m['collectors'], 'collector')}: {num(m['collectors_down'])} down, "
            f"{num(m['collectors_no_failover'])} carrying resources without failover{abcg}, "
            f"{num(m['collectors_capacity_critical'])} at or above {m['collector_critical_pct']}% of sizing capacity, "
            f"{num(m['collectors_behind'])} behind the newest version in the portal ({m['collector_newest']}), "
            f"{num(m['collectors_ungrouped'])} ungrouped and {num(m['collectors_custom_size'])} custom-sized."
        )
        imbalance = m.get("collector_imbalance")
        if imbalance:
            text += (
                f" Instance load ranges from {num(m['collector_load_min'])} to {num(m['collector_load_max'])} "
                f"per collector (busiest is {imbalance:.1f}x the median)."
            )
        if not data.get("collector_groups").ok:
            text += " " + unavailable(data.get("collector_groups")) + "."
        issues = [
            m["collectors_down_with_resources"] > 0,
            m["collectors_no_failover"] > 0,
            m["collectors_capacity_critical"] > 0,
            m["collectors_version_critical"] > 0,
            (imbalance or 0) >= rules["collector_imbalance_ratio"],
            m["collectors_ungrouped"] > 0,
            m["collectors_custom_size"] > 0,
        ]
        no_fo_pct = m.get("collectors_no_failover_pct") or 0
        if (
            m["collectors_down_with_resources"]
            or m["collectors_capacity_critical"]
            or no_fo_pct >= rules["collector_no_failover_pct_red"]
        ):
            status = "RED"
        elif sum(issues) >= 3:
            status = "AMBER / RED"
        elif any(issues):
            status = "AMBER"
        else:
            status = "GREEN"
        return status, text

    def modules() -> tuple[str, str]:
        counts = m.get("module_counts") or {}
        if not counts:
            return "UNKNOWN", "Data unavailable: no LogicModule endpoints could be read."
        top = sorted(counts.items(), key=lambda x: -x[1])[:4]
        text = (
            f"{num(m['modules_total'])} LogicModules ("
            + ", ".join(f"{num(v)} {k}" for k, v in top)
            + ")."
        )
        if m.get("datasources_custom") is not None:
            text += (
                f" {count_of(m['datasources_custom'], 'DataSource')} "
                f"{agree(m['datasources_custom'], 'is', 'are')} locally authored and "
                f"{num(m['datasources_modified'])} {agree(m['datasources_modified'], 'is', 'are')} "
                "modified from their Exchange origin."
            )
        text += (
            f" {count_of(m['clone_suspects'], 'module')} {agree(m['clone_suspects'], 'has a', 'have')} "
            f"clone-like {agree(m['clone_suspects'], 'name', 'names')}"
        )
        if m.get("clones_checked"):
            text += (
                f"; {num(m['clones_unused'])} of {num(m['clones_checked'])} checked "
                f"{agree(m['clones_unused'], 'is', 'are')} not applied to any resource"
            )
        text += ". Update availability is shown in the Modules Toolbox and is not exposed through the API."
        if m.get("modules_missing"):
            text += " Could not read: " + ", ".join(m["modules_missing"]) + "."
        datasource_total = counts.get("DataSources") or 0
        modified_pct = pct(m.get("datasources_modified") or 0, datasource_total) or 0
        status = "AMBER" if m["clone_suspects"] or modified_pct > 10 else "GREEN"
        return status, text

    def config() -> tuple[str, str]:
        ds = data.get("configsources")
        if not ds.ok:
            return "UNKNOWN", unavailable(ds)
        if not m["configsources"]:
            return "AMBER", "No ConfigSources are installed. Configuration backup, drift and compliance monitoring are not deployed."
        return "UNKNOWN", (
            f"{count_of(m['configsources'], 'ConfigSource')} {agree(m['configsources'], 'is', 'are')} "
            "installed. The number of resources with "
            "configuration collection is not exposed through the REST API. "
            + placeholder("Config Devices count and coverage of network infrastructure.")
        )

    def cloud() -> tuple[str, str]:
        if not devices.ok:
            return "UNKNOWN", unavailable(devices)
        k8s = m.get("kubernetes")
        if not m["cloud_total"] and not k8s:
            return "UNKNOWN", (
                "Cloud and Kubernetes resources show zero. This may reflect deliberate scope "
                "separation, another portal or tool, or a genuine observability gap; it must be "
                "validated. SaaS and OpenTelemetry scope are not distinguishable through the API."
            )
        cloud = m["cloud"]
        k8s_text = f" and {num(k8s)} Kubernetes resources" if k8s is not None else ""
        return "GREEN", (
            f"{num(m['cloud_total'])} cloud resources (AWS {num(cloud['AWS'])}, Azure {num(cloud['Azure'])}, "
            f"GCP {num(cloud['GCP'])}){k8s_text} are monitored. SaaS and OpenTelemetry scope must be validated."
        )

    def routing() -> tuple[str, str]:
        rules_ds, chains_ds = data.get("alert_rules"), data.get("escalation_chains")
        if not rules_ds.ok or not chains_ds.ok:
            return "UNKNOWN", unavailable(rules_ds, chains_ds)
        invalid, shared, throttled = m["rules_invalid_chain"], m["rules_duplicate_priority"], m["chains_throttled"]
        text = (
            f"{count_of(m['alert_rules'], 'alert rule')} and {count_of(m['chains'], 'escalation chain')}. "
            f"{count_of(invalid, 'rule')} {agree(invalid, 'routes', 'route')} to a missing chain or a chain "
            f"with no destinations, {count_of(shared, 'priority', 'priorities')} "
            f"{agree(shared, 'is', 'are')} shared by more than one rule and {num(throttled)} of "
            f"{count_of(m['chains'], 'chain')} {agree(throttled, 'uses', 'use')} rate limiting."
        )
        if not m.get("catch_all_rule"):
            text += " There is no catch-all rule, so unmatched alerts notify no one."
        if "itsm_integrations" in m:
            text += (
                f" ITSM integrations: {', '.join(m['itsm_integrations'])}."
                if m["itsm_integrations"]
                else " No ITSM integration is configured."
            )
        if m["rules_invalid_chain"]:
            status = "RED"
        elif m["rules_duplicate_priority"] or not m["chains_throttled"] or not m.get("catch_all_rule"):
            status = "AMBER"
        else:
            status = "GREEN"
        return status, text

    def sdt() -> tuple[str, str]:
        ds = data.get("sdts")
        if not ds.ok:
            return "UNKNOWN", unavailable(ds)
        status = "AMBER" if m["sdts_open_ended"] or m["sdts_long"] else "GREEN"
        return status, (
            f"{count_of(m['sdts'], 'SDT entry', 'SDT entries')} ({num(m['sdts_effective'])} active now). "
            f"{num(m['sdts_open_ended'])} {agree(m['sdts_open_ended'], 'is', 'are')} open-ended and "
            f"{num(m['sdts_long'])} {agree(m['sdts_long'], 'runs', 'run')} for more than "
            f"{m['sdt_long_days']} days. Long or open-ended downtime silences monitoring without review."
        )

    def access() -> tuple[str, str]:
        users, tokens = data.get("users"), data.get("api_tokens")
        if not users.ok and not tokens.ok:
            return "UNKNOWN", unavailable(users, tokens)
        bits = []
        issues = 0
        if users.ok:
            no2fa = m.get("users_no_2fa")
            no2fa_text = (
                f", {count_of(no2fa, 'active user')} {agree(no2fa, 'does', 'do')} not use two-factor authentication"
                if no2fa is not None
                else ""
            )
            inactive = m["users_inactive"]
            bits.append(
                f"{count_of(m['users'], 'user')} ({num(m['users_active'])} active, {num(m['users_api_only'])} API-only, "
                f"{num(m['users_admin'])} with administrator roles). {count_of(inactive, 'active user')} "
                f"{agree(inactive, 'has', 'have')} not logged in for {m['user_inactive_days']}+ days{no2fa_text}."
            )
            issues += m["users_inactive"] + (no2fa or 0)
        if m.get("roles") is not None:
            bits.append(f"{count_of(m['roles'], 'role')} {agree(m['roles'], 'is', 'are')} defined.")
        if tokens.ok:
            enabled, idle = m["api_tokens_enabled"], m["api_tokens_idle"]
            bits.append(
                f"{num(enabled)} of {count_of(m['api_tokens'], 'API token')} {agree(enabled, 'is', 'are')} "
                f"enabled and {count_of(idle, 'enabled token')} {agree(idle, 'has', 'have')} been unused for "
                f"{m['token_idle_days']}+ days."
            )
            issues += m["api_tokens_idle"]
        text = " ".join(bits)
        if not users.ok or not tokens.ok:
            text += " " + unavailable(users, tokens) + "."
        return ("AMBER" if issues else "GREEN"), text

    def discovery() -> tuple[str, str]:
        netscans, audit = data.get("netscans"), data.get("audit_logs")
        if not netscans.ok and not audit.ok:
            return "UNKNOWN", unavailable(netscans, audit)
        bits = []
        status = "GREEN"
        if netscans.ok:
            bits.append(f"{count_of(m['netscans'], 'NetScan')} ({num(m['netscans_scheduled'])} scheduled).")
        if audit.ok:
            share = m.get("audit_discovery_pct")
            bits.append(
                f"The audit log holds {num(m['audit_entries'])} entries over the last {num(m['audit_hours'])} hours; "
                f"{pct_text(share)} relate to ActiveDiscovery or instance changes."
            )
            if (share or 0) >= rules["audit_discovery_share_pct"]:
                status = "AMBER"
                bits.append("This indicates discovery churn that can weaken topology and correlation.")
        text = " ".join(bits)
        if not netscans.ok or not audit.ok:
            text += " " + unavailable(netscans, audit) + "."
        return status, text

    return [
        _status_row("core", "Core resource adoption", core),
        _status_row("footprint", "Device footprint", footprint),
        _status_row("telemetry", "Telemetry depth", telemetry),
        _status_row("datapoints", "Monthly datapoint collection", datapoint_collection),
        _status_row("alerting", "Alerting health", alerting),
        _status_row("dashboards", "Dashboards and reporting", dashboards),
        _status_row("services", "Service Insights", services),
        _status_row("logs", "LM Logs", logs),
        _status_row("uptime", "LM Uptime", uptime),
        _status_row("websites", "Websites", websites),
        _status_row("collectors", "Collectors", collector_row),
        _status_row("modules", "LogicModules", modules),
        _status_row("config", "Config monitoring", config),
        _status_row("cloud", "Cloud / SaaS / Kubernetes", cloud),
        _status_row("routing", "Alert routing and escalation", routing),
        _status_row("sdt", "Scheduled downtime (SDT)", sdt),
        _status_row("access", "Access governance", access),
        _status_row("discovery", "Discovery and change activity", discovery),
    ]


def overall_status(rows: list[ScoreRow]) -> str:
    scores = [STATUS_SCORE[r.status] for r in rows if r.status in STATUS_SCORE]
    if not scores:
        return "UNKNOWN"
    average = sum(scores) / len(scores)
    if average <= 0.5:
        return "GREEN"
    if average <= 1.2:
        return "AMBER"
    return "RED"


# --------------------------------------------------------------------------- #
# Narrative
# --------------------------------------------------------------------------- #
AREA_PHRASES = {
    "footprint": "monitoring hygiene",
    "telemetry": "telemetry depth",
    "alerting": "alert noise",
    "dashboards": "dashboard and report adoption",
    "services": "Service Insights maturity",
    "websites": "synthetic coverage",
    "collectors": "collector governance",
    "modules": "LogicModule governance",
    "config": "configuration monitoring",
    "routing": "alert routing",
    "sdt": "downtime hygiene",
    "access": "access governance",
    "discovery": "discovery churn",
    "core": "entitlement headroom",
}


def _weak_rows(rows: list[ScoreRow]) -> list[ScoreRow]:
    ranked = [r for r in rows if STATUS_SCORE.get(r.status, 0) >= 1.0]
    return sorted(ranked, key=lambda r: -STATUS_SCORE[r.status])


def build_assessment(
    data: PortalData,
    ctx: ReportContext,
    rules: dict[str, Any] | None = None,
    standards: dict[str, Any] | None = None,
) -> Assessment:
    rules = rules or load_rules()
    standards = standards or engine.load_standards()
    m = compute_metrics(data, rules, standards)
    rows = build_scorecard(data, m, rules, ctx)
    by_key = {r.key: r for r in rows}
    status = overall_status(rows)
    weak = _weak_rows(rows)
    weak_phrases = [AREA_PHRASES.get(r.key, r.area.lower()) for r in weak]
    customer = ctx.customer

    resources = m.get("resources")
    size_word = "an"
    if resources is not None:
        if resources >= rules["large_estate_resources"]:
            size_word = "a large, established"
        elif resources >= rules["mid_estate_resources"]:
            size_word = "a mid-sized"
        else:
            size_word = "a focused"
    footprint_ok = by_key["footprint"].status in ("GREEN", "GREEN / AMBER")
    strength = (
        f"Established platform footprint ({num(resources)} resources, {num(m.get('collectors'))} collectors)"
        if footprint_ok
        else f"Platform footprint of {num(resources)} resources with coverage gaps"
    )
    constraints = [p for r, p in zip(weak, weak_phrases) if footprint_ok or r.key != "footprint"]
    if constraints:
        condition = f"{strength}, constrained by {join_words(constraints[:4])}."
    else:
        condition = f"{strength} with no material constraints in the data reviewed."

    ops_keys = {"alerting", "collectors", "modules", "routing", "discovery", "access", "sdt"}
    ops_phrases = [AREA_PHRASES.get(r.key, r.area.lower()) for r in weak if r.key in ops_keys]
    if weak and weak[0].key == "footprint":
        conclusion = (
            "The first priority is monitoring hygiene. Dead, ungrouped or stale resources reduce "
            "confidence in coverage, so the foundation should be fixed before the data is used for "
            "correlation or AIOps."
        )
    elif ops_phrases:
        opening = (
            "This is not a monitoring-footprint problem. It is an operationalisation and governance problem."
            if footprint_ok
            else "This is primarily an operationalisation and governance problem, with monitoring hygiene "
            "to fix alongside it."
        )
        conclusion = (
            f"{opening} The portal is collecting substantial telemetry, but {join_words(ops_phrases[:3])} "
            f"{agree(len(ops_phrases[:3]), 'limits', 'limit')} how much of it turns into lower effort, "
            "better service context and automated response."
        )
    elif not footprint_ok:
        conclusion = (
            "The first priority is monitoring hygiene. Dead, ungrouped or stale resources reduce "
            "confidence in coverage, so the foundation should be fixed before the data is used for "
            "correlation or AIOps."
        )
    else:
        conclusion = (
            "The portal is in good operational health. The opportunity is to convert existing "
            "telemetry into service context and automation through Service Insights, Logs and Edwin AI."
        )

    cover_facts = [
        ("Assessment date", ctx.date_text),
        ("Operating model", f"{customer} environment managed by Infosys"),
        ("Portal", f"{ctx.portal} (read-only REST API v3 collection at {ctx.generated_at:%H:%M} IST)"),
    ]
    if ctx.prepared_by:
        cover_facts.append(("Prepared by", ctx.prepared_by))
    cover_facts.append(("Classification", "Internal account planning"))

    summary = _summary_paragraphs(m, by_key, weak, customer, size_word)
    tiles = _kpi_tiles(m, ctx)
    direction = _immediate_direction(m, by_key)
    if weak_phrases:
        interpretation = (
            ("Core monitoring is well embedded. " if footprint_ok else "Core coverage needs attention. ")
            + "The value leakage is concentrated in "
            + join_words([r.area.lower() if r.key not in AREA_PHRASES else AREA_PHRASES[r.key] for r in weak[:6]])
            + "."
        )
    else:
        interpretation = "Core monitoring is well embedded and no scorecard area is rated AMBER or worse."
    unknown_count = sum(1 for r in rows if r.status == "UNKNOWN")
    if unknown_count:
        interpretation += (
            f" {unknown_count} areas are UNKNOWN because the data is not exposed through the REST API "
            "or could not be read; complete them from the portal before customer use."
        )

    good, weak_list, missing = _good_weak_missing(m, by_key)
    anchors, expansion, risks = _stickiness(m, by_key, weak_list)
    modules = _module_rows(m, by_key, ctx)
    edwin_status, edwin_subtitle, edwin_text = _edwin(m, by_key)
    plan = _plan(m, by_key)
    sources, validation = _sources(data, m, ctx)
    log = _collection_log(data)

    return Assessment(
        context=ctx,
        metrics=m,
        overall_status=status,
        overall_condition=condition,
        conclusion_title="Primary conclusion",
        conclusion_text=conclusion,
        cover_facts=cover_facts,
        summary_paragraphs=summary,
        kpi_tiles=tiles,
        immediate_direction=direction,
        scorecard=rows,
        interpretation=interpretation,
        good=good,
        weak=weak_list,
        missing=missing,
        anchors=anchors,
        expansion=expansion,
        risks=risks,
        modules=modules,
        edwin_status=edwin_status,
        edwin_subtitle=edwin_subtitle,
        edwin_text=edwin_text,
        credible=_credible(m),
        entry_criteria=[
            "Reconcile alerts, incidents/tickets, duplicate handling, workforce effort, MTTD/MTTR, SLA performance and cost-per-ticket baselines.",
            f"Freeze two or three incident categories and define exactly which signals are required for {customer} SLA reporting versus operational response.",
            "Resolve critical collector, topology, metadata and ActiveDiscovery stability issues in the selected scope.",
            "Confirm data flow, RBAC, security, LogicMonitor access and the ITSM/CMDB integration path.",
            "Agree POV success criteria, exit criteria, executive sponsors and the production-commercial gate before activation.",
        ],
        use_cases=_use_cases(m),
        execution=[
            ("Week 0 - readiness", "Lock scope, baseline, technical access, RBAC, architecture, owners and exit criteria.", "Signed start checklist and approved POV charter."),
            ("Week 1 - connect", "Connect Edwin to the existing LM environment, validate data quality and select representative alert clusters.", "Verified data flow and accepted test set."),
            ("Week 2 - assess and tune", "Run event grouping and investigations, tune models and compare results with manual operations.", "Measured quality, time and workload evidence."),
            ("Week 3 - playback", f"Quantify workload, MTTR, incident quality and SLA impact; present findings to {customer} and Infosys.", "Production decision, scope and commercial gate."),
        ],
        success_tiles=[
            ("Alert-to-insight", "Noise reduction", "Raw events grouped without losing SLA evidence"),
            ("Minutes saved", "Triage efficiency", "Manual investigation time reclaimed"),
            ("MTTR / SLA", "Service performance", "Priority-level improvement and risk reduction"),
            ("Ticket quality", "ITSM enrichment", "Better context, handoff and RCA consistency"),
            ("Hours / FTE", "Capacity reclaimed", "Engineering effort redirected to higher-value work"),
            ("Go / no-go", "Production gate", "Evidence-based scope and commercial decision"),
        ],
        plan=plan,
        decisions=[
            "Which single business KPI will unlock Infosys funding: FTE capacity, cost per ticket, MTTR, SLA-risk reduction, or an agreed combination?",
            "Which two or three incident categories will be used for the Edwin POV?",
            f"Which alerts must remain for {customer} contractual reporting, and which are expected to drive operational action?",
            "Who owns each outstanding health-check recommendation, with committed completion dates?",
            "What technical readiness evidence is required before Edwin activation, including collector health, topology, metadata, RBAC and ITSM access?",
            "What is the production decision gate after the POV playback, and who has authority to approve it?",
        ],
        positioning=(
            f"LogicMonitor is already embedded in the {customer} service"
            + (f" across {num(resources)} monitored resources" if resources else "")
            + ". The opportunity is not to add more monitoring. It is to convert the telemetry already "
            "being collected into fewer actionable situations, faster investigation, measurable SLA "
            "improvement and lower cost-to-serve for Infosys."
        ),
        sources=sources,
        validation=validation,
        collection_log=log,
        top_sources=m.get("top_sources", []),
        collector_rows=m.get("collector_rows", []),
    )


def _summary_paragraphs(m, by_key, weak, customer, size_word) -> list[tuple[str, str]]:
    paragraphs: list[tuple[str, str]] = []
    if m.get("resources") is not None:
        parts = [count_of(m["resources"], "resource")]
        for key, label in (
            ("instances", "instance"),
            ("dashboards", "dashboard"),
            ("services", "service"),
            ("modules_total", "LogicModule"),
            ("collectors", "collector"),
        ):
            if m.get(key) is not None:
                parts.append(count_of(m[key], label))
        if m.get("reports_24h"):
            parts.append(f"{count_of(m['reports_24h'], 'report')} generated in the last 24 hours")
        paragraphs.append(
            (
                f"{customer} has {size_word} LM Envision deployment within the Infosys-managed service. "
                f"Core adoption: {join_words(parts)}. This creates meaningful platform "
                "dependency and a credible base for service-level AIOps.",
                "[1]",
            )
        )
    else:
        paragraphs.append(
            (f"{customer} LM Envision footprint: Data unavailable ({by_key['footprint'].evidence}).", "[1]")
        )
    maturity = "uneven" if weak else "consistent"
    text = f"Maturity is {maturity}."
    if m.get("alerts_24h") is not None:
        prefix = "at least " if m.get("alerts_24h_partial") else ""
        text += f" The portal generated {prefix}{num(m['alerts_24h'])} alerts in the previous day"
        if m.get("open_alerts") is not None:
            text += f" and holds {num(m['open_alerts'])} open alerts"
        text += "."
    dep, dyn = m.get("dependent_alerts"), m.get("dynamic_threshold_datapoints")
    unused = []
    if dep == 0:
        unused.append("dependent alert mapping")
    if dyn == 0 and not m.get("dynamic_threshold_alerts"):
        unused.append("dynamic thresholds")
    if unused:
        text += f" {join_words(unused).capitalize()} {'is' if len(unused) == 1 else 'are'} not evidenced."
    if m.get("collectors"):
        down, no_fo = m["collectors_down"], m["collectors_no_failover"]
        text += (
            f" {num(down)} of {count_of(m['collectors'], 'collector')} {agree(down, 'is', 'are')} down and "
            f"{num(no_fo)} {agree(no_fo, 'carries', 'carry')} resources without failover."
        )
    text += (
        " LM Logs and LM Uptime consumption are not exposed through the REST API and must be "
        "confirmed from the portal Usage page."
    )
    if weak:
        text += " The environment is telemetry-rich but insight- and automation-poor."
    paragraphs.append((text, "[1]"))
    paragraphs.append(
        (
            placeholder(
                f"strategic context - {customer}'s expectations, the Infosys commercial position and how "
                "LogicMonitor should be positioned (operational data and AIOps foundation, not another monitoring tool)."
            ),
            "[2]",
        )
    )
    if weak:
        risk = (
            f"The principal platform risk evidenced by portal data is {AREA_PHRASES.get(weak[0].key, weak[0].area.lower())} "
            f"({weak[0].area}: {weak[0].status}). "
        )
    else:
        risk = "Portal data shows no material platform risk. "
    paragraphs.append(
        (
            risk + placeholder("account and execution risks, such as outstanding health-check actions or POV status."),
            "[1,3]",
        )
    )
    return paragraphs


def _kpi_tiles(m, ctx) -> list[tuple[str, str, str]]:
    tiles = []
    resources = m.get("resources")
    if resources is None:
        tiles.append(("n/a", "Devices", "Data unavailable"))
    elif ctx.contracted_resources:
        used = pct(resources, ctx.contracted_resources) or 0
        tiles.append(
            (
                f"{num(resources)} / {num(ctx.contracted_resources)}",
                "Devices",
                f"{used:.1f}% deployed; {num(ctx.contracted_resources - resources)} headroom",
            )
        )
    else:
        tiles.append((num(resources), "Devices", f"{num(m.get('dead'))} dead; {num(m.get('ungrouped'))} ungrouped"))
    per = m.get("instances_per_resource")
    tiles.append(
        (
            num(m.get("instances")),
            "Instances",
            f"Approximately {per:.0f} per device" if per is not None else "Data unavailable",
        )
    )
    if m.get("alerts_24h") is not None:
        tiles.append(
            (
                ("≥" if m.get("alerts_24h_partial") else "") + num(m["alerts_24h"]),
                "Alerts in prior day",
                f"Approximately {short_number(m['alerts_24h'] * 30)} over 30 days",
            )
        )
    else:
        tiles.append(("n/a", "Alerts in prior day", "Data unavailable"))
    if m.get("open_alerts") is not None:
        tiles.append(
            (num(m["open_alerts"]), "Open alerts", f"{pct_text(m.get('open_acked_pct'))} acknowledged")
        )
    else:
        tiles.append(("n/a", "Open alerts", "Data unavailable"))
    services = m.get("services")
    tiles.append(
        (
            num(services),
            "Services",
            "Service Insights footprint present" if services else "No services modelled",
        )
    )
    if m.get("collectors") is not None:
        tiles.append(
            (
                num(m["collectors"]),
                "Collectors",
                f"{num(m['collectors_down'])} down; {num(m['collectors_no_failover'])} without failover",
            )
        )
    else:
        tiles.append(("n/a", "Collectors", "Data unavailable"))
    return tiles


def _immediate_direction(m, by_key) -> list[str]:
    fixes = []
    if by_key["collectors"].status not in ("GREEN", "UNKNOWN"):
        fixes.append("collector")
    if by_key["alerting"].status not in ("GREEN", "UNKNOWN"):
        fixes.append("alert-hygiene")
    if by_key["routing"].status not in ("GREEN", "UNKNOWN"):
        fixes.append("alert-routing")
    if by_key["footprint"].status not in ("GREEN", "UNKNOWN") or by_key["discovery"].status == "AMBER":
        fixes.append("topology and discovery")
    if by_key["modules"].status not in ("GREEN", "UNKNOWN"):
        fixes.append("LogicModule governance")
    if by_key["access"].status not in ("GREEN", "UNKNOWN"):
        fixes.append("access")
    first = (
        f"Stabilise the foundation: close the {join_words(fixes)} backlog before using AI to mask platform debt."
        if fixes
        else "Protect the foundation: keep collector, alert and LogicModule governance at the current standard as the estate grows."
    )
    return [
        first,
        "Lock the Edwin business case: select two or three incident categories and agree baseline metrics for alert reduction, triage effort, MTTR, SLA risk and engineering capacity.",
        "Operationalise existing entitlements: turn Logs, Service Insights and LM Uptime into repeatable workflows rather than passive data stores or unused capacity.",
        "Create an Infosys operating model: named owners, dated actions, executive checkpoints and a clear production decision following the POV playback.",
    ]


def _good_weak_missing(m, by_key) -> tuple[list[str], list[str], list[str]]:
    good: list[str] = []
    weak: list[str] = []
    missing: list[str] = []
    anchors = []
    for key, label in (
        ("resources", "resources"),
        ("dashboards", "dashboards"),
        ("reports", "reports"),
        ("services", "services"),
        ("collectors", "collectors"),
    ):
        if m.get(key):
            anchors.append(f"{num(m[key])} {label}")
    if anchors:
        good.append(f"LogicMonitor is embedded in the managed service through {join_words(anchors)}.")
    if m.get("datasources_custom"):
        good.append(
            f"{num(m['datasources_custom'])} locally authored DataSources create process dependency and meaningful switching friction."
        )
    if by_key["footprint"].status == "GREEN":
        good.append(f"Coverage hygiene is good: {pct_text(m.get('dead_pct'))} of resources are dead.")
    if by_key["collectors"].status == "GREEN":
        good.append("The collector estate is up, protected by failover and within sizing guidance.")
    if by_key["routing"].status == "GREEN":
        good.append("Every alert rule routes to an escalation chain with destinations, with rate limiting in place.")
    if m.get("itsm_integrations"):
        good.append(f"An ITSM integration ({', '.join(m['itsm_integrations'])}) gives a path for incident enrichment.")
    if m.get("dynamic_threshold_datapoints"):
        good.append(f"Dynamic thresholds are defined on {num(m['dynamic_threshold_datapoints'])} datapoints.")
    if m.get("dependent_alerts"):
        good.append("Dependent alert mapping is active, reducing alert floods during upstream outages.")
    if by_key["telemetry"].status == "GREEN":
        good.append(
            f"Telemetry depth of {num(m.get('instances'))} instances supports cross-domain investigation and correlation."
        )
    if by_key["core"].status == "GREEN":
        good.append("Core resource entitlement has usable headroom, reducing near-term capacity pressure.")

    if by_key["alerting"].status in ("RED", "AMBER / RED"):
        weak.append(
            f"Alert volume is operationally unsustainable: {num(m.get('alerts_24h'))} alerts in the previous day "
            f"({m.get('alerts_per_resource') or 0:.1f} per resource) and {num(m.get('open_alerts'))} open."
        )
    elif by_key["alerting"].status == "AMBER":
        weak.append(f"Alert volume needs tuning: {num(m.get('alerts_24h'))} alerts in the previous day.")
    unused = []
    if m.get("dependent_alerts") == 0:
        unused.append("Dependent alert mapping")
    if m.get("dynamic_threshold_datapoints") == 0 and not m.get("dynamic_threshold_alerts"):
        unused.append("dynamic thresholds")
    if unused:
        weak.append(f"{join_words(unused)} {'is' if len(unused) == 1 else 'are'} not evidenced in the portal data.")
    if by_key["collectors"].status not in ("GREEN", "UNKNOWN"):
        weak.append(
            f"Collector health, sizing, failover and load distribution need governance: {num(m.get('collectors_down'))} down, "
            f"{num(m.get('collectors_no_failover'))} without failover, {num(m.get('collectors_capacity_critical'))} over capacity."
        )
    if m.get("clone_suspects"):
        weak.append(
            f"{count_of(m['clone_suspects'], 'LogicModule')} {agree(m['clone_suspects'], 'has a', 'have')} "
            f"clone-like {agree(m['clone_suspects'], 'name', 'names')}, adding supportability and lifecycle risk."
        )
    if by_key["routing"].status in ("RED", "AMBER"):
        gaps = []
        if m.get("rules_invalid_chain"):
            gaps.append(f"{count_of(m['rules_invalid_chain'], 'rule')} without a valid escalation chain")
        if m.get("rules_duplicate_priority"):
            gaps.append(f"{count_of(m['rules_duplicate_priority'], 'priority', 'priorities')} shared between rules")
        if not m.get("chains_throttled"):
            gaps.append("no escalation chain uses rate limiting")
        if not m.get("catch_all_rule"):
            gaps.append("no catch-all rule")
        if gaps:
            weak.append(f"Alert routing gaps: {join_words(gaps)}.")
    if by_key["discovery"].status == "AMBER":
        weak.append(
            f"The audit log is dominated by ActiveDiscovery instance changes ({pct_text(m.get('audit_discovery_pct'))}), "
            "indicating discovery churn that can weaken topology and correlation."
        )
    if by_key["footprint"].status in ("RED", "AMBER"):
        weak.append(
            f"{num(m.get('dead'))} dead and {num(m.get('ungrouped'))} ungrouped resources reduce confidence in coverage."
        )
    if by_key["access"].status == "AMBER":
        weak.append(
            f"Access hygiene: {num(m.get('users_inactive'))} inactive users, {num(m.get('users_no_2fa'))} without 2FA, "
            f"{num(m.get('api_tokens_idle'))} idle API tokens."
        )
    if by_key["sdt"].status == "AMBER":
        weak.append(f"{num(m.get('sdts_open_ended'))} open-ended and {num(m.get('sdts_long'))} long SDTs silence monitoring.")

    missing.append("Verified dashboard, report and user adoption data - configuration count does not prove consumption.")
    if m.get("services"):
        missing.append(
            f"Named ownership, criticality and service-level objectives for the {num(m['services'])} Service Insights services."
        )
    missing.append("LM Logs usage, tracked investigations, standard query packs, retention governance and cost controls.")
    if not m.get("websites_external"):
        missing.append("External synthetic checks for critical customer journeys through LM Uptime.")
    else:
        missing.append("Confirmed LM Uptime usage against the contracted check entitlement.")
    missing.append("A reconciled baseline for alerts, tickets, workforce, MTTD/MTTR, SLA and cost per incident.")
    if not m.get("cloud_total") and not m.get("kubernetes"):
        missing.append("Confirmed cloud, SaaS, Kubernetes, OpenTelemetry and configuration-monitoring scope.")
    if not good:
        good.append(placeholder("strengths observed during the review."))
    if not weak:
        weak.append("No material weaknesses were found in the portal data reviewed.")
    return good[:6], weak[:6], missing[:6]


def _stickiness(m, by_key, weak) -> tuple[list[str], list[str], list[str]]:
    anchors = []
    if m.get("reports"):
        anchors.append(f"SLA and custom reporting ({num(m['reports'])} reports)")
    if m.get("datasources_custom"):
        anchors.append(f"Locally authored LogicModules ({num(m['datasources_custom'])})")
    elif m.get("modules_total"):
        anchors.append(f"LogicModule estate ({num(m['modules_total'])})")
    if m.get("collectors"):
        netscans = f", {num(m['netscans'])} NetScans" if m.get("netscans") else ""
        anchors.append(f"Collector and discovery architecture ({num(m['collectors'])} collectors{netscans})")
    if m.get("services"):
        anchors.append(f"{num(m['services'])} service models")
    if m.get("dashboards") or m.get("chains"):
        anchors.append(
            f"Dashboard and escalation workflows ({num(m.get('dashboards'))} dashboards, {num(m.get('chains'))} chains)"
        )
    if m.get("itsm_integrations"):
        anchors.append(f"ITSM integration ({', '.join(m['itsm_integrations'])})")
    expansion = ["Edwin Event Intelligence and investigation", "Logs operationalisation", "LM Uptime for digital journeys"]
    expansion.append("ServiceNow/CMDB enrichment" if not m.get("itsm_integrations") else "ITSM/CMDB enrichment depth")
    expansion.append("Service Insights maturity" if m.get("services") else "Service Insights adoption")
    expansion.append("Config and OpenTelemetry expansion")
    risks = ["Unimplemented health-check actions"]
    if by_key["alerting"].status in ("RED", "AMBER / RED"):
        risks.append("Alert fatigue eroding trust in the platform")
    if by_key["collectors"].status not in ("GREEN", "UNKNOWN") or m.get("clone_suspects"):
        risks.append("Collector and module supportability debt")
    risks.append("Logs or Uptime spend without value proof")
    risks.append("AI promise without measurable criteria")
    risks.append(placeholder("stakeholder, commercial and delivery risks"))
    return anchors[:6] or [placeholder("existing anchors")], expansion[:6], risks[:6]


def _module_rows(m, by_key, ctx) -> list[ModuleRow]:
    def health(*keys: str) -> str:
        statuses = [by_key[k].status for k in keys]
        known = [s for s in statuses if s in STATUS_SCORE]
        if not known:
            return "Unknown"
        return HEALTH_FROM_STATUS[max(known, key=lambda s: STATUS_SCORE[s])]

    contracted = ""
    if ctx.contracted_resources and m.get("resources") is not None:
        contracted = f" and {pct(m['resources'], ctx.contracted_resources):.0f}% resource utilisation"
    rows = [
        ModuleRow(
            "Core resources",
            f"{num(m.get('resources'))} devices and {num(m.get('instances'))} instances{contracted}.",
            health("footprint", "telemetry"),
            "Protect the foundation, remove stale objects and improve coverage quality before adding more volume.",
        ),
        ModuleRow(
            "Dashboards / reports",
            f"{num(m.get('dashboards'))} dashboards, {num(m.get('widgets'))} widgets and {num(m.get('reports_24h'))} reports in the previous 24 hours.",
            health("dashboards"),
            "Rationalise by persona: NOC, service owner, Infosys delivery executive, SLA governance and customer leadership. Measure views and recipient use.",
        ),
        ModuleRow(
            "Service Insights",
            f"{num(m.get('services'))} services configured; service quality and operational usage are not exposed through the API.",
            health("services"),
            "Attach owner, criticality, SLA/SLO, business impact and change control. Use service context in incident prioritisation and Edwin investigations.",
        ),
        ModuleRow(
            "LM Logs",
            f"{num(m.get('logsources'))} LogSources configured; ingestion volume, LogAlerts and tracked queries: "
            + placeholder("from the portal Usage and Logs pages."),
            "Unknown",
            "Review top consumers, retention and partitions; build tracked investigations and high-value alerts; link logs to service and incident workflows.",
        ),
        ModuleRow(
            "Edwin AI",
            placeholder("current Edwin AI usage or POV status."),
            "Unknown",
            "Proceed only through a gated POV with two or three use cases, signed baselines, technical readiness checks and a production decision.",
        ),
        ModuleRow(
            "LM Uptime",
            "Checks executed versus contracted are not exposed through the API. " + placeholder("Uptime usage."),
            "Unknown",
            "Activate external synthetic monitoring for the highest-value digital journeys and use results in service and SLA reporting.",
        ),
        ModuleRow(
            "Websites",
            f"{num(m.get('websites'))} website checks ({num(m.get('websites_external'))} external, {num(m.get('websites_alerting'))} alerting).",
            health("websites"),
            "Validate journey coverage and add geographic, dependency and business-impact context. Complement internal checks with Uptime.",
        ),
        ModuleRow(
            "Config monitoring",
            f"{num(m.get('configsources'))} ConfigSources installed; Config Devices count: " + placeholder("from Settings > Account Information."),
            health("config"),
            "Assess configuration backup, drift, compliance and change-correlation coverage across network infrastructure.",
        ),
        ModuleRow(
            "Cloud / Kubernetes / OTel",
            f"{num(m.get('cloud_total'))} cloud and {num(m.get('kubernetes'))} Kubernetes resources; OpenTelemetry scope is not evidenced.",
            health("cloud"),
            "Confirm whether scope exists elsewhere. Where appropriate, extend hybrid observability and use OpenTelemetry to enrich services and Edwin context.",
        ),
        ModuleRow(
            "Collectors / LogicModules",
            f"{count_of(m.get('collectors'), 'collector')} and {count_of(m.get('modules_total'), 'LogicModule')}; "
            f"{num(m.get('collectors_down'))} down, {count_of(m.get('clone_suspects'), 'clone-like module')}.",
            health("collectors", "modules"),
            "Create a formal governance register, standardise sizing and failover, remediate discovery churn, and retire unsupported or unused clones.",
        ),
    ]
    return rows


def _edwin(m, by_key) -> tuple[str, str, str]:
    broken = (
        (m.get("collectors_down_with_resources") or 0) > 0
        or (m.get("rules_invalid_chain") or 0) > 0
        or by_key["footprint"].status == "RED"
    )
    if broken:
        return (
            "RED",
            "Defer - stabilise the monitoring foundation first",
            "Recommendation: Do not start a POV until down collectors, broken alert routing and dead "
            "resources in scope are remediated. AI correlation on an unstable foundation will mask "
            "platform debt rather than prove value.",
        )
    weak = [r for r in by_key.values() if r.status in ("RED", "AMBER / RED", "AMBER") and r.key != "core"]
    if weak:
        ingredients = ["existing telemetry"]
        if m.get("alerts_24h"):
            ingredients.append("alert volume")
        if m.get("logsources"):
            ingredients.append("Logs footprint")
        if m.get("services"):
            ingredients.append("Service Insights estate")
        return (
            "AMBER",
            "Proceed conditionally - do not launch an open-ended AI exercise",
            "Recommendation: Proceed with a tightly scoped, value-led POV after the entry criteria are "
            f"locked. The {join_words(ingredients)} provide a credible proving ground, but the baseline "
            "and operating conditions are not yet sufficiently controlled.",
        )
    return (
        "GREEN",
        "Proceed - the foundation is ready for a scoped POV",
        "Recommendation: Proceed with a value-led POV. The monitoring foundation is stable; lock the "
        "baseline and success criteria so the outcome is measurable.",
    )


def _credible(m) -> list[str]:
    bullets = []
    if m.get("alerts_24h"):
        bullets.append(
            f"The portal generated {num(m['alerts_24h'])} alerts in the previous day across {num(m.get('resources'))} "
            "resources, a measurable baseline for event grouping and noise reduction."
        )
    if m.get("instances"):
        services = f" and {num(m['services'])} services" if m.get("services") else ""
        bullets.append(
            f"Telemetry depth of {num(m['instances'])} instances{services} supports a meaningful investigation and correlation exercise."
        )
    if m.get("itsm_integrations"):
        bullets.append(
            f"An ITSM integration ({', '.join(m['itsm_integrations'])}) already exists, giving a path for incident enrichment."
        )
    bullets.append(
        placeholder("the customer's mandate for AI-enabled service improvement and the Infosys economic incentive (L0/L1 effort, SLA performance, margin).")
    )
    return bullets


def _use_cases(m) -> list[tuple[str, str, str]]:
    top = m.get("top_sources") or []

    def evidence_for(pattern: re.Pattern) -> str:
        hits = [(n, c, s) for n, c, s, _ in top if pattern.search(n)]
        if not hits:
            return ""
        count = sum(c for _, c, _ in hits)
        share = sum(s for _, _, s in hits)
        return f" Portal evidence: {num(count)} prior-day alerts ({share:.0f}%) from {join_words([n for n, _, _ in hits[:3]])}."

    cases = []
    wireless = evidence_for(WIRELESS_PATTERN)
    if wireless:
        cases.append(
            (
                "Wireless / AP alert correlation",
                "Retain every signal required for SLA reporting while grouping duplicate and related warnings into operationally meaningful situations." + wireless,
                "Raw alerts vs grouped insights; duplicate-ticket reduction; NOC minutes saved; reporting integrity preserved.",
            )
        )
    cases.append(
        (
            "Network and collector incident correlation",
            "Associate parent infrastructure, collector, interface, tunnel and downstream symptoms to reduce floods and shorten initial triage." + evidence_for(NETWORK_PATTERN),
            "Related-event grouping; engineers engaged; time to probable cause; escalation rate; MTTD/MTTR.",
        )
    )
    itsm = m.get("itsm_integrations") or []
    itsm_name = "ServiceNow" if any("servicenow" in t for t in itsm) else "ITSM"
    cases.append(
        (
            f"{itsm_name} incident enrichment and RCA",
            "Pass affected service, probable cause, related alerts, logs and investigation context into the incident workflow."
            + ("" if itsm else " No ITSM integration is configured yet; this is a prerequisite."),
            "Ticket quality; handoff time; RCA consistency; escalation avoidance; restoration time.",
        )
    )
    if len(cases) < 3 and top:
        name, count, share, _ = top[0]
        cases.append(
            (
                f"Noise reduction for {name}",
                f"The top alert source generated {num(count)} prior-day alerts ({share:.0f}%). Group and suppress duplicates without losing actionable signals.",
                "Raw vs grouped alerts; tickets avoided; threshold changes agreed; NOC minutes saved.",
            )
        )
    return cases[:3]


def _plan(m, by_key) -> list[tuple[str, str, str]]:
    first = ["Assign one accountable Infosys owner per health-check action", "establish signed operational baselines"]
    top = m.get("top_sources") or []
    if by_key["alerting"].status not in ("GREEN", "UNKNOWN"):
        names = join_words([n for n, *_ in top[:2]])
        first.append("classify the top alert sources" + (f" ({names})" if names else ""))
    if by_key["collectors"].status not in ("GREEN", "UNKNOWN"):
        first.append("remediate collector down states, failover gaps and sizing exceptions")
    if by_key["routing"].status == "RED":
        first.append("fix alert rules that route to missing or empty escalation chains")
    if m.get("dependent_alerts") == 0:
        first.append("pilot dependent alert mapping")
    if m.get("dynamic_threshold_datapoints") == 0:
        first.append("pilot dynamic thresholds on suitable metrics")
    if m.get("sdts_open_ended"):
        first.append("close open-ended SDTs")
    first.append("confirm Logs and Uptime usage against entitlement")
    second = []
    if m.get("clone_suspects"):
        second.append("Rationalise cloned and unused LogicModules")
    if by_key["routing"].status in ("AMBER", "RED"):
        second.append("review alert-rule priorities and escalation rate limiting")
    if m.get("services"):
        second.append("validate service ownership and criticality")
    second.append("rationalise dashboards by persona")
    if by_key["access"].status == "AMBER":
        second.append("remove inactive users and idle API tokens")
    second.append("complete ITSM/CMDB readiness")
    second.append("freeze Edwin scope and success criteria")
    second[0] = second[0][0].upper() + second[0][1:]
    return [
        (
            "0-30 days",
            "; ".join(first) + ".",
            "Named owners and dates; baseline pack; first alert-noise and collector-health improvements; entitlement usage confirmed.",
        ),
        (
            "31-60 days",
            "; ".join(second) + ".",
            "Governance register; persona dashboard catalogue; service-quality map; approved POV charter and technical readiness.",
        ),
        (
            "61-90 days",
            "Run the three-week Edwin POV; compare evidence with baseline; produce executive value playback; agree production scope, operating model, enablement and commercial ownership; package the outcome as an Infosys repeatable proposition.",
            "Validated business case; production decision; rollout plan; reusable Infosys reference architecture and value model.",
        ),
    ]


def _sources(data: PortalData, m, ctx) -> tuple[list[tuple[str, str, str]], list[str]]:
    collected = [d.label for d in data.datasets.values() if d.ok]
    failed = [d for d in data.datasets.values() if not d.ok]
    desc = (
        f"Read-only collection on {ctx.date_text} at {ctx.generated_at:%H:%M} IST covering "
        f"{len(collected)} of {len(data.datasets)} endpoints (devices, collectors, alerts, alert rules, "
        "escalation chains, LogicModules, dashboards, reports, websites, users, API tokens, NetScans, "
        "SDTs and audit log). Metrics are point-in-time; real-time usage may differ from billed usage."
    )
    if failed:
        desc += " Unavailable: " + "; ".join(f"{d.label} ({d.error})" for d in failed) + "."
    sources = [
        ("[1]", f"LM Envision REST API v3 - {ctx.portal}", desc),
        ("[2]", placeholder("customer account plan or executive presentation"), placeholder("title, owner and date of the source document.")),
        ("[3]", placeholder("internal account updates"), placeholder("channel or document, period covered and key points.")),
    ]
    validation = []
    if m.get("alerts_24h_partial"):
        validation.append(
            f"Prior-day alert volume was {m['alerts_24h_partial']}; the true figure is higher. Use the portal Alerts page for the exact count."
        )
    validation.append(
        "A configuration count does not prove active adoption. Dashboard views, report recipients, service usage, query execution and user activity are not exposed through the API."
    )
    if not m.get("cloud_total") and not m.get("kubernetes"):
        validation.append(
            "Zero cloud, SaaS or Kubernetes resources in this portal does not prove the customer lacks those workloads; they may be monitored elsewhere or excluded from scope."
        )
    validation.append(
        "Statuses and findings are rule-based (report_rules.json and the Operations Portal Maintenance standards). Review every RAG rating and consultant placeholder before customer use."
    )
    if failed:
        validation.append(
            "Some data could not be collected ("
            + ", ".join(d.label for d in failed)
            + "). Re-run with a token whose role can view those areas, or complete the affected rows manually."
        )
    validation.append(
        "The portal metrics are a current operational snapshot. Contract, billing and renewal decisions should use the formal usage reports and commercial system of record."
    )
    return sources, validation


def _collection_log(data: PortalData) -> list[tuple[str, str, str, str]]:
    rows = []
    for dataset in data.datasets.values():
        if not dataset.ok:
            status = f"Unavailable: {dataset.error}"
            records = "-"
        else:
            status = f"Partial: {dataset.partial}" if dataset.partial else "OK"
            records = num(dataset.count)
        rows.append((dataset.label, dataset.endpoint, records, status))
    return rows
