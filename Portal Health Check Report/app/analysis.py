"""Turn collected facts into scored health-check findings.

Status values follow the sample report: GREEN, AMBER, RED, UNKNOWN. UNKNOWN is
used when the REST API does not expose what is needed; such checks are listed
with the reason instead of failing the run.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from collect import Facts

GREEN, AMBER, RED, UNKNOWN = "GREEN", "AMBER", "RED", "UNKNOWN"
HEALTH_WORD = {GREEN: "Strong", AMBER: "Developing", RED: "At risk", UNKNOWN: "Unknown"}
COMPUTED, PARTIAL, NOT_AVAILABLE = "Computed", "Partial", "Not available from API"

# All scoring thresholds in one place so they can be reviewed and tuned.
THRESHOLDS: Dict[str, Any] = {
    "contract_util_green_max_pct": 95,
    "contract_util_amber_max_pct": 100,
    "dead_device_green_max_pct": 5,
    "dead_device_amber_max_pct": 15,
    "alerting_disabled_warn_pct": 10,
    "stale_discovery_days": 14,
    "instances_per_device_green_min": 20,
    "alerts_24h_per_device_green_max": 1.0,
    "alerts_24h_per_device_amber_max": 3.0,
    "open_alerts_per_device_green_max": 0.25,
    "open_alerts_per_device_amber_max": 0.5,
    "collector_capacity": {
        "nano": (25, 1500), "small": (100, 5000), "medium": (250, 15000),
        "large": (500, 30000), "extra_large": (1000, 60000),
    },
    "collector_capacity_critical_pct": 90,
    "collector_no_failover_major_pct": 25,
    "collector_imbalance_ratio": 3.0,
    "module_unused_warn_pct": 10,
    "config_devices_green_min_pct": 5,
    "audit_discovery_churn_warn_pct": 50,
    "inactive_user_days": 90,
    "inactive_user_warn_pct": 25,
}

CLONE_PATTERN = re.compile(r"(?i)(\bclone\b|_clone|\bcopy\b|_copy|\(\d+\)\s*$|_old$|_bak$|_backup$|_test$)")


@dataclass
class Check:
    key: str
    area: str
    status: str
    evidence: str
    coverage: str = COMPUTED
    coverage_note: str = ""
    capability: Optional[str] = None
    usage: Optional[str] = None
    motion: str = ""
    good: List[str] = field(default_factory=list)
    weak: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    actions: List[Tuple[int, str]] = field(default_factory=list)


@dataclass
class Assessment:
    facts: Facts
    checks: List[Check]
    overall: str
    overall_summary: str
    conclusion_title: str
    conclusion: str
    tiles: List[Tuple[str, str, str, str]]
    summary_paragraphs: List[str]
    direction: List[str]
    anchors: List[str]
    expansion: List[str]
    risks: List[str]
    readiness: List[Tuple[str, str, str]]
    readiness_status: str
    readiness_text: str
    use_cases: List[Tuple[str, str, str]]
    plan: List[Tuple[str, str, str]]
    top_alert_sources: List[Tuple[str, int]]
    alert_severity: Dict[str, int]
    module_rows: List[Tuple[str, str, str, str]]
    audit_rows: List[Tuple[str, int]]
    collector_rows: List[List[str]]
    usage_rows: List[Tuple[str, str]]

    def check(self, key: str) -> Check:
        return next(c for c in self.checks if c.key == key)


def n(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.1f}"
    return f"{int(value):,}"


def q(count: Any, singular: str, plural: Optional[str] = None) -> str:
    return f"{n(count)} {singular if count == 1 else (plural or singular + 's')}"


def are(count: Any) -> str:
    return "is" if count == 1 else "are"


def have(count: Any) -> str:
    return "has" if count == 1 else "have"


def pct(part: float, whole: float) -> Optional[float]:
    if not whole:
        return None
    return round(100.0 * part / whole, 1)


def p(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:.1f}%"


def _epoch_s(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number / 1000.0 if number > 1e11 else number


def _usage_pick(usage: Dict[str, Any], *needles: str) -> Optional[int]:
    """Find a /metrics/usage counter by key fragments, preferring combined totals."""
    skip = ("stopped", "terminated", "deleted")
    matches = []
    for key, value in usage.items():
        low = key.lower()
        if all(nd in low for nd in needles) and not any(s in low for s in skip):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                matches.append((0 if "combined" in low else 1, key, int(value)))
    if not matches:
        return None
    matches.sort()
    return matches[0][2]


def _worst(*statuses: str) -> str:
    order = [RED, AMBER, GREEN]
    known = [s for s in statuses if s in order]
    if not known:
        return UNKNOWN
    return min(known, key=order.index)


def _gap(facts: Facts, *keys: str) -> Optional[str]:
    for key in keys:
        if key in facts.gaps:
            return facts.gaps[key]
    return None


def _unavailable(key: str, area: str, reason: str, capability: Optional[str] = None,
                 motion: str = "") -> Check:
    text = f"Could not be assessed: {reason}."
    return Check(key, area, UNKNOWN, text, NOT_AVAILABLE, reason[:1].upper() + reason[1:] + ".", capability,
                 usage=text, motion=motion, missing=[f"{area}: {reason}."])


# --------------------------------------------------------------------------- checks


def _services_count(facts: Facts) -> Optional[int]:
    usage = facts.get("usage") or {}
    count = _usage_pick(usage, "service")
    if count is None and facts.has("devices"):
        count = sum(1 for d in facts.get("devices") if d.get("deviceType") == 6)
    return count


def _devices(facts: Facts) -> List[Dict[str, Any]]:
    return [d for d in facts.get("devices") or [] if d.get("deviceType") != 6]


def check_core_adoption(facts: Facts) -> Check:
    area = "Core resource adoption"
    usage = facts.get("usage") or {}
    monitored = _usage_pick(usage, "standard") if usage else None
    if monitored is None and facts.has("devices"):
        monitored = len(_devices(facts))
    if monitored is None:
        return _unavailable("core_adoption", area, _gap(facts, "usage", "devices") or "no resource count returned")
    contract = facts.contract.get("devices")
    if not contract:
        return Check(
            "core_adoption", area, UNKNOWN,
            f"{n(monitored)} monitored resources. Contracted resource volume and billed rolling MTD usage are "
            "not exposed by the REST API; rerun with --contract-devices to compute headroom.",
            PARTIAL, "Contract entitlement and billed usage are not in the API.", usage="",
            good=[f"{n(monitored)} resources are under monitoring."],
            missing=["Contracted resource entitlement and billed rolling MTD usage (Usage page / billing system)."],
        )
    util = pct(monitored, contract) or 0.0
    headroom = int(contract - monitored)
    t = THRESHOLDS
    status = GREEN if util <= t["contract_util_green_max_pct"] else AMBER if util <= t["contract_util_amber_max_pct"] else RED
    check = Check(
        "core_adoption", area, status,
        f"{n(monitored)} monitored resources against {n(contract)} contracted ({p(util)}), leaving "
        f"{n(headroom)} resources of headroom. Billed rolling MTD usage is not exposed by the API.",
        PARTIAL, "Contract supplied by operator; billed MTD usage not in the API.",
        capability="Core resources", usage=f"{p(util)} of contracted resources in use.",
    )
    if status == GREEN:
        check.good.append(f"Core resource entitlement has usable headroom ({n(headroom)} resources).")
    else:
        check.weak.append(f"Resource usage is at {p(util)} of the contracted {n(contract)}.")
        check.actions.append((0, "Review resource entitlement versus growth plans; retire stale resources before renewal."))
    return check


def check_device_footprint(facts: Facts) -> Check:
    area = "Device footprint"
    if not facts.has("devices"):
        return _unavailable("device_footprint", area, _gap(facts, "devices") or "no device data returned")
    t = THRESHOLDS
    devices = _devices(facts)
    total = len(devices)
    now = facts.get("now")
    dead = sum(1 for d in devices if "dead" in str(d.get("hostStatus", "")).lower())
    no_alerting = sum(1 for d in devices if d.get("disableAlerting") is True)
    stale = 0
    for d in devices:
        seen = _epoch_s(d.get("autoPropsUpdatedOn"))
        if seen and now - seen > t["stale_discovery_days"] * 86400:
            stale += 1
    dead_pct, off_pct, stale_pct = pct(dead, total), pct(no_alerting, total), pct(stale, total)
    if total == 0:
        status = RED
    elif (dead_pct or 0) <= t["dead_device_green_max_pct"]:
        status = GREEN
    elif (dead_pct or 0) <= t["dead_device_amber_max_pct"]:
        status = AMBER
    else:
        status = RED
    if status == GREEN and (off_pct or 0) > t["alerting_disabled_warn_pct"]:
        status = AMBER
    contract = facts.contract.get("devices")
    lead = f"{n(total)} devices"
    if contract:
        lead += f" out of {n(contract)} contracted ({p(pct(total, contract))}), leaving {n(contract - total)} device headroom"
    check = Check(
        "device_footprint", area, status,
        f"{lead}. {n(dead)} ({p(dead_pct)}) are dead or have no data, {n(no_alerting)} ({p(off_pct)}) have alerting "
        f"disabled, and {n(stale)} ({p(stale_pct)}) have not refreshed auto-properties in "
        f"{t['stale_discovery_days']} days.",
    )
    check.usage = f"{q(total, 'device')}, {p(dead_pct)} dead or not reporting."
    if status == GREEN:
        check.good.append(f"{n(total)} devices are monitored with a low dead-device rate ({p(dead_pct)}).")
    if dead:
        check.weak.append(f"{q(dead, 'device')} {are(dead)} dead or not reporting ({p(dead_pct)} of the estate).")
        check.actions.append((0 if status == RED else 1, f"Remediate or retire {n(dead)} dead devices and confirm collector assignment."))
    if (off_pct or 0) > t["alerting_disabled_warn_pct"]:
        check.weak.append(f"Alerting is disabled on {p(off_pct)} of devices.")
        check.actions.append((1, "Review devices with alerting disabled and re-enable or document the exception."))
    if stale:
        check.actions.append((1, f"Investigate {n(stale)} devices whose auto-properties have not refreshed (stale discovery)."))
    return check


def check_telemetry_depth(facts: Facts) -> Check:
    area = "Telemetry depth"
    if not facts.has("collectors"):
        return _unavailable("telemetry_depth", area, _gap(facts, "collectors") or "no collector data returned")
    instances = sum(int(c.get("numberOfInstances") or 0) for c in facts.get("collectors"))
    devices = len(_devices(facts)) if facts.has("devices") else None
    per_device = (instances / devices) if devices else None
    status = GREEN if per_device and per_device >= THRESHOLDS["instances_per_device_green_min"] else AMBER
    per_text = f", approximately {n(round(per_device))} per device" if per_device else ""
    check = Check(
        "telemetry_depth", area, status,
        f"{n(instances)} monitored instances{per_text}. Total and complex datapoint counts are not exposed "
        "by the REST API.",
        PARTIAL, "Instance totals come from collector counters; datapoint totals are not in the API.",
        missing=["Total and complex datapoint counts (Portal Information page)."],
    )
    check.usage = f"{n(instances)} instances" + (f" (about {n(round(per_device))} per device)." if per_device else ".")
    facts.values["_instances"] = instances
    facts.values["_instances_per_device"] = per_device
    if status == GREEN:
        check.good.append(f"Telemetry is deep: about {n(round(per_device))} instances per device.")
    else:
        check.weak.append("Instance depth per device is shallow; coverage of key modules may be incomplete.")
        check.actions.append((1, "Review DataSource coverage on critical device classes; confirm Active Discovery is finding instances."))
    return check


def check_datapoint_collection(facts: Facts) -> Check:
    return _unavailable(
        "datapoint_collection", "Monthly datapoint collection",
        "monthly datapoint collection and its entitlement are only shown on the portal Usage page")


def check_alerting(facts: Facts) -> Check:
    area = "Alerting health"
    if not facts.has("open_alerts") and not facts.has("alerts_24h"):
        return _unavailable("alerting", area, _gap(facts, "open_alerts", "alerts_24h") or "no alert data returned")
    t = THRESHOLDS
    devices = len(_devices(facts)) if facts.has("devices") else 0
    open_info = facts.get("open_alerts") or {}
    open_items = open_info.get("items") or []
    open_total = open_info.get("total")
    day = facts.get("alerts_24h") or {}
    day_total = day.get("total")
    statuses = []
    parts = []
    if day_total is not None:
        prefix = "at least " if day.get("capped") else ""
        parts.append(f"{prefix}{n(day_total)} alerts in the previous 24 hours, equivalent to "
                     f"{n(round(day_total / 24))} per hour or {day_total / 1440:.1f} per minute")
        if devices:
            ratio = day_total / devices
            statuses.append(GREEN if ratio <= t["alerts_24h_per_device_green_max"]
                            else AMBER if ratio <= t["alerts_24h_per_device_amber_max"] else RED)
    if open_total is not None:
        parts.append(f"{n(open_total)} open alerts")
        if devices:
            ratio = open_total / devices
            statuses.append(GREEN if ratio <= t["open_alerts_per_device_green_max"]
                            else AMBER if ratio <= t["open_alerts_per_device_amber_max"] else RED)
    crit_unacked = sum(1 for a in open_items if a.get("severity") == 4 and not a.get("acked") and not a.get("sdted"))
    acked = sum(1 for a in open_items if a.get("acked"))
    status = _worst(*statuses) if statuses else UNKNOWN
    evidence = (", with ".join(parts) + ". " if parts else "")
    if open_items:
        evidence += f"{n(crit_unacked)} open critical alerts are unacknowledged and not in SDT; {p(pct(acked, len(open_items)))} of open alerts are acknowledged. "
    evidence += "Dependent alert mapping and dynamic-threshold adoption are not exposed by the REST API."
    check = Check("alerting", area, status, evidence, PARTIAL,
                  "Alert volumes computed; dependent alert mapping and dynamic thresholds are not in the API.",
                  missing=["Dependent alert mapping and dynamic threshold adoption (verify in Settings)."])
    if status == GREEN:
        check.good.append("Alert volume is proportionate to the estate size.")
    elif status in (AMBER, RED):
        check.weak.append(f"Alert volume is high for the estate ({', with '.join(parts)}).")
        check.actions.append((0 if status == RED else 1,
                              "Classify the top alert sources and tune thresholds, alert rules and SDT for the noisiest modules."))
        check.actions.append((1, "Pilot dependent alert mapping and dynamic thresholds on suitable metrics."))
    if crit_unacked:
        check.weak.append(f"{q(crit_unacked, 'open critical alert')} {are(crit_unacked)} unacknowledged.")
        check.actions.append((0, f"Triage {q(crit_unacked, 'unacknowledged critical alert')} and confirm escalation routing."))
    return check


def check_dashboards(facts: Facts) -> Check:
    area = "Dashboards and reporting"
    if not facts.has("dashboards") and not facts.has("reports"):
        return _unavailable("dashboards", area, _gap(facts, "dashboards", "reports") or "no dashboard data returned")
    dashboards = facts.get("dashboards")
    widgets = facts.get("widgets")
    reports = facts.get("reports")
    now = facts.get("now")
    gen_24h = gen_30d = None
    if reports is not None:
        stamps = [_epoch_s(r.get("lastGenerateOn")) for r in reports]
        gen_24h = sum(1 for s in stamps if s and now - s <= 86400)
        gen_30d = sum(1 for s in stamps if s and now - s <= 30 * 86400)
    parts = []
    if dashboards is not None:
        parts.append(f"{n(dashboards)} dashboards")
    if widgets is not None:
        ratio = f" ({widgets / dashboards:.1f} per dashboard)" if dashboards else ""
        parts.append(f"{n(widgets)} widgets{ratio}")
    if reports is not None:
        parts.append(f"{n(len(reports))} reports, of which {n(gen_24h)} generated in the last 24 hours and {n(gen_30d)} in the last 30 days")
    have_dash = bool(dashboards)
    have_reports = bool(gen_30d)
    status = GREEN if have_dash and have_reports else RED if not have_dash and not reports else AMBER
    check = Check(
        "dashboards", area, status,
        ", ".join(parts) + ". Dashboard views and report recipients are not exposed by the REST API.",
        PARTIAL, "Counts computed; consumption (views, recipients) is not in the API.",
        capability="Dashboards / reports",
        missing=["Verified dashboard views, report recipients and user adoption — configuration count does not prove consumption."],
    )
    facts.values["_reports_24h"] = gen_24h
    if status == GREEN:
        check.good.append(f"Dashboards and scheduled reporting are in active use ({n(dashboards)} dashboards, {n(gen_30d)} reports run in 30 days).")
    else:
        check.weak.append("Dashboard or report usage is limited.")
    check.actions.append((2, "Rationalise dashboards by persona (NOC, service owner, delivery lead, leadership) and measure use."))
    return check


def check_services(facts: Facts) -> Check:
    area = "Service Insights"
    count = _services_count(facts)
    if count is None:
        return _unavailable("services", area, _gap(facts, "usage", "devices") or "no service count returned")
    if count == 0:
        check = Check("services", area, RED, "No Service Insights services are configured.",
                      motion="Model the top business services with owner, criticality and SLA context.")
        check.weak.append("Service Insights is not in use, so alerts lack business-service context.")
        check.actions.append((1, "Model the three to five most critical business services in Service Insights."))
        return check
    check = Check(
        "services", area, AMBER,
        f"{n(count)} services are configured. Ownership, criticality, service health and operational adoption "
        "are not exposed by the REST API and must be validated.",
        PARTIAL, "Service count computed; ownership and quality are not in the API.",
        good=[f"Service Insights provides a service-model foundation ({n(count)} services)."],
        missing=[f"Named ownership, criticality and service-level objectives for the {n(count)} services."],
    )
    check.actions.append((1, "Validate service ownership, criticality and SLA/SLO for every Service Insights service."))
    return check


def check_logs(facts: Facts) -> Check:
    area = "LM Logs"
    modules = facts.get("modules") or {}
    logsources = (modules.get("LogSources") or {}).get("total")
    contract = facts.contract.get("logs_gb")
    parts = []
    if logsources is not None:
        parts.append(f"{n(logsources)} LogSource modules are installed")
    if contract:
        parts.append(f"{n(contract)} GB contracted")
    text = (". ".join(parts) + ". ") if parts else ""
    text += ("Ingestion volume (MTD and projected), partitions, LogAlerts and tracked queries are not exposed by "
             "the REST API; check the portal Usage and Logs pages.")
    return Check(
        "logs", area, UNKNOWN, text, PARTIAL if logsources is not None else NOT_AVAILABLE,
        "Log ingestion, partitions, LogAlerts and tracked queries are not in the REST API.",
        usage=text,
        missing=["LM Logs ingestion versus entitlement, retention, partitions, LogAlerts and tracked queries."],
    )


def check_uptime(facts: Facts) -> Check:
    area = "LM Uptime"
    if not facts.has("websites"):
        return _unavailable("uptime", area, _gap(facts, "websites") or "no website data returned")
    sites = facts.get("websites")
    external = [s for s in sites if not s.get("isInternal")]
    contract = facts.contract.get("uptime_checks")
    contract_text = f" against {n(contract)} contracted checks" if contract else ""
    note = "Checks executed versus the Uptime entitlement are not exposed by the REST API."
    if not external:
        check = Check("uptime", area, RED,
                      f"No external synthetic checks are configured{contract_text}. {note}", PARTIAL, note)
        check.weak.append("No external synthetic (Uptime) checks are configured.")
        check.missing.append("External synthetic checks for critical customer journeys.")
        check.actions.append((0, "Activate three to five external Uptime checks for the most critical customer journeys."))
        return check
    check = Check("uptime", area, GREEN,
                  f"{n(len(external))} external synthetic checks are configured{contract_text}. {note}", PARTIAL, note,
                  good=[f"{n(len(external))} external synthetic checks give an outside-in view."])
    return check


def check_websites(facts: Facts) -> Check:
    area = "Websites"
    if not facts.has("websites"):
        return _unavailable("websites", area, _gap(facts, "websites") or "no website data returned")
    sites = facts.get("websites")
    total = len(sites)
    off = sum(1 for s in sites if s.get("stopMonitoring") or s.get("disableAlerting"))
    internal = sum(1 for s in sites if s.get("isInternal"))
    types = Counter(str(s.get("type") or "unknown") for s in sites)
    type_text = ", ".join(f"{n(v)} {k}" for k, v in types.most_common())
    if total == 0:
        status = AMBER
    elif (pct(off, total) or 0) > 20:
        status = AMBER
    else:
        status = GREEN
    check = Check(
        "websites", area, status,
        (f"{n(total)} website checks ({type_text}); {n(internal)} run from internal collectors and "
         f"{n(off)} have monitoring or alerting switched off. Business-journey coverage is not exposed by the API.")
        if total else "No website checks are configured.",
        PARTIAL, "Counts computed; journey coverage needs validation.",
    )
    if total and status == AMBER:
        check.weak.append(f"{n(off)} of {n(total)} website checks have monitoring or alerting switched off.")
        check.actions.append((1, "Review website checks with monitoring or alerting switched off."))
    return check


def _collector_major(build: Any) -> Optional[int]:
    try:
        number = int(str(build).split(".")[0])
    except (TypeError, ValueError):
        return None
    return number // 1000 if number >= 1000 else number


def check_collectors(facts: Facts) -> Check:
    area = "Collectors"
    if not facts.has("collectors"):
        return _unavailable("collectors", area, _gap(facts, "collectors") or "no collector data returned")
    t = THRESHOLDS
    cols = facts.get("collectors")
    total = len(cols)
    if total == 0:
        check = Check("collectors", area, RED, "No collectors are registered.")
        check.weak.append("No collectors are registered.")
        return check
    down = [c for c in cols if c.get("isDown")]
    loaded = [c for c in cols if int(c.get("numberOfHosts") or 0) > 0]
    no_failover = [c for c in loaded if not c.get("backupAgentId")]
    no_failback = [c for c in loaded if c.get("backupAgentId") and not c.get("enableFailBack")]
    ungrouped = [c for c in cols if str(c.get("collectorGroupName") or "").strip().lower() in ("", "@default", "default")]
    sizes = Counter(str(c.get("collectorSize") or "unknown").lower() for c in cols)
    inst = sorted(int(c.get("numberOfInstances") or 0) for c in loaded)
    imbalance = (inst[-1] / statistics.median(inst)) if inst and statistics.median(inst) > 0 else None
    over = []
    caps = t["collector_capacity"]
    for c in cols:
        size = str(c.get("collectorSize") or "").lower()
        if size in caps:
            dev_cap, inst_cap = caps[size]
            peak = max(pct(int(c.get("numberOfHosts") or 0), dev_cap) or 0,
                       pct(int(c.get("numberOfInstances") or 0), inst_cap) or 0)
            if peak >= t["collector_capacity_critical_pct"]:
                over.append(c)
    majors = sorted({m for m in (_collector_major(c.get("build")) for c in cols) if m is not None})
    nofo_pct = pct(len(no_failover), len(loaded)) or 0
    major_issues = sum([bool(down), nofo_pct > t["collector_no_failover_major_pct"], bool(over)])
    minor_issues = sum([bool(ungrouped), bool(imbalance and imbalance > t["collector_imbalance_ratio"]),
                        len(majors) > 2, bool(no_failback)])
    status = RED if major_issues >= 2 else AMBER if (major_issues or minor_issues) else GREEN
    size_text = ", ".join(f"{n(v)} {k}" for k, v in sizes.most_common())
    evidence = (
        f"{q(total, 'collector')} ({size_text}). {n(len(down))} down; {n(len(no_failover))} of {q(len(loaded), 'collector')} "
        f"with resources {have(len(no_failover))} no failover collector; {n(len(no_failback))} {have(len(no_failback))} "
        f"failover without fail-back; {n(len(ungrouped))} {are(len(ungrouped))} in the default group; "
        f"{n(len(over))} {are(len(over))} at or above "
        f"{t['collector_capacity_critical_pct']}% of LogicMonitor sizing guidance."
    )
    if imbalance:
        evidence += f" Instance load ranges from {n(inst[0])} to {n(inst[-1])} (busiest is {imbalance:.1f}x the median)."
    if majors:
        evidence += f" Collector major versions in use: {', '.join(str(m) for m in majors)}."
    check = Check("collectors", area, status, evidence, capability="Collectors / LogicModules",
                  usage=f"{q(total, 'collector')}: {n(len(down))} down, {n(len(no_failover))} without failover.")
    if status == GREEN:
        check.good.append(f"Collector estate is healthy: {n(total)} collectors, none down, failover in place.")
    if down:
        check.weak.append(f"{q(len(down), 'collector')} {are(len(down))} down.")
        check.actions.append((0, f"Restore {q(len(down), 'down collector')} and confirm affected resources failed over."))
    if no_failover:
        check.weak.append(f"{q(len(no_failover), 'collector')} with resources {have(len(no_failover))} no failover collector.")
        check.actions.append((0, "Assign failover collectors (with fail-back) to every collector that monitors resources."))
    if over:
        check.weak.append(f"{q(len(over), 'collector')} {are(len(over))} at or above {t['collector_capacity_critical_pct']}% of sizing guidance.")
        check.actions.append((0, "Rebalance or resize collectors running above LogicMonitor sizing guidance."))
    if imbalance and imbalance > t["collector_imbalance_ratio"]:
        check.weak.append(f"Collector load is uneven (busiest collector carries {imbalance:.1f}x the median instances).")
        check.actions.append((1, "Rebalance resources across collectors to even out instance load."))
    if ungrouped:
        check.actions.append((2, f"Move {q(len(ungrouped), 'collector')} out of the default group into named collector groups."))
    if len(majors) > 2:
        check.weak.append(f"Collectors run {len(majors)} different major versions.")
        check.actions.append((1, "Standardise collector versions on a supported release."))
    return check


def _module_stats(facts: Facts) -> Tuple[int, int, int, Dict[str, Dict[str, Any]]]:
    modules = facts.get("modules") or {}
    total = clones = unused = 0
    for info in modules.values():
        total += int(info.get("total") or 0)
        for m in info.get("items") or []:
            if CLONE_PATTERN.search(m.get("name") or "") or CLONE_PATTERN.search(m.get("displayName") or ""):
                clones += 1
            applies = str(m.get("appliesTo") or "").strip().lower()
            if applies in ("", "false", "false()"):
                unused += 1
    return total, clones, unused, modules


def check_logicmodules(facts: Facts) -> Check:
    area = "LogicModules"
    if not facts.has("modules"):
        return _unavailable("logicmodules", area, _gap(facts, "modules") or "no LogicModule data returned")
    total, clones, unused, modules = _module_stats(facts)
    detailed = sum(int(i.get("total") or 0) for i in modules.values() if i.get("items") is not None)
    unused_pct = pct(unused, detailed) or 0
    status = AMBER if clones or unused_pct > THRESHOLDS["module_unused_warn_pct"] else GREEN
    breakdown = ", ".join(f"{n(i.get('total'))} {k}" for k, i in modules.items() if i.get("total") is not None)
    check = Check(
        "logicmodules", area, status,
        f"{n(total)} LogicModules ({breakdown}). {n(clones)} look like clones or copies by name and {n(unused)} "
        f"DataSources/EventSources/ConfigSources have an empty or false AppliesTo (not applied to anything). "
        "Available LM Exchange updates are not exposed by the REST API.",
        PARTIAL, "Inventory, clone and unused heuristics computed; Exchange update status is not in the API.",
        missing=["LM Exchange update status for official modules (Modules > My Module Toolbox)."],
    )
    check.usage = f"{q(total, 'LogicModule')}: {n(clones)} likely clones, {n(unused)} not applied to any resource."
    if total:
        check.good.append(f"A substantial LogicModule estate ({n(total)} modules) underpins monitoring.")
    if clones:
        check.weak.append(f"{n(clones)} cloned or copied modules increase supportability and lifecycle risk.")
        check.actions.append((1, "Rationalise cloned modules: merge changes upstream or retire unused clones."))
    if unused:
        check.actions.append((1, f"Review {n(unused)} modules that are not applied to any resource and retire those not needed."))
    return check


def check_config(facts: Facts) -> Check:
    area = "Config monitoring"
    usage = facts.get("usage") or {}
    config_devices = _usage_pick(usage, "config") if usage else None
    modules = facts.get("modules") or {}
    configsources = (modules.get("ConfigSources") or {}).get("total")
    if config_devices is None:
        reason = _gap(facts, "usage") or "the config-device count was not returned by /metrics/usage"
        chk = _unavailable("config", area, reason)
        if configsources is not None:
            chk.evidence = f"{n(configsources)} ConfigSource modules installed; config-device count unavailable ({reason})."
            chk.usage = chk.evidence
            chk.coverage = PARTIAL
        return chk
    devices = len(_devices(facts)) if facts.has("devices") else 0
    share = pct(config_devices, devices)
    status = GREEN if share and share >= THRESHOLDS["config_devices_green_min_pct"] else AMBER
    check = Check(
        "config", area, status,
        f"{n(config_devices)} devices have configuration monitoring ({p(share)} of devices)"
        + (f"; {n(configsources)} ConfigSource modules installed." if configsources is not None else "."),
    )
    if status == AMBER:
        check.weak.append(f"Configuration monitoring covers only {n(config_devices)} devices.")
        check.actions.append((2, "Assess configuration backup, drift and compliance coverage across network infrastructure."))
    return check


def check_cloud(facts: Facts) -> Check:
    area = "Cloud / SaaS / Kubernetes"
    usage = facts.get("usage")
    if not usage:
        return _unavailable("cloud", area, _gap(facts, "usage") or "/metrics/usage returned no counters")
    counts = {
        "AWS": _usage_pick(usage, "aws"), "Azure": _usage_pick(usage, "azure"),
        "GCP": _usage_pick(usage, "gcp"), "Kubernetes": _usage_pick(usage, "kubernetes"),
        "SaaS": _usage_pick(usage, "saas"),
    }
    known = {k: v for k, v in counts.items() if v is not None}
    text = ", ".join(f"{n(v)} {k}" for k, v in known.items()) or "no cloud counters returned"
    if not known or not any(known.values()):
        check = Check(
            "cloud", area, UNKNOWN,
            f"Cloud, SaaS and Kubernetes resources: {text}. This may reflect deliberate scope separation, "
            "another portal or tool, or a genuine observability gap; it must be validated.",
            PARTIAL, "Counts computed; zero cannot be interpreted without customer context.",
            missing=["Confirmed cloud, SaaS, Kubernetes and OpenTelemetry scope."],
        )
        return check
    return Check("cloud", area, GREEN, f"Hybrid coverage present: {text} resources.",
                 good=[f"Hybrid observability is in place ({text})."])


def check_alert_routing(facts: Facts) -> Check:
    area = "Alert routing and escalation"
    if not facts.has("alert_rules"):
        return _unavailable("alert_routing", area, _gap(facts, "alert_rules") or "no alert-rule data returned")
    rules = facts.get("alert_rules")
    chains = facts.get("chains") or []
    chain_ids = {c.get("id") for c in chains}
    orphan = [r for r in rules if facts.has("chains") and r.get("escalatingChainId") not in chain_ids]
    dup = [prio for prio, cnt in Counter(r.get("priority") for r in rules).items() if cnt > 1]
    empty = [c for c in chains if not c.get("destinations")]
    unthrottled = [c for c in chains if not c.get("enableThrottling")]
    if not rules or orphan:
        status = RED
    elif dup or empty or (chains and len(unthrottled) > len(chains) / 2):
        status = AMBER
    else:
        status = GREEN
    evidence = (f"{q(len(rules), 'alert rule')} and {q(len(chains), 'escalation chain')}. {n(len(orphan))} "
                f"{'rule references' if len(orphan) == 1 else 'rules reference'} a missing chain, "
                f"{q(len(dup), 'priority value', 'priority values')} {are(len(dup))} shared by several rules, "
                f"{q(len(empty), 'chain')} {have(len(empty))} no destinations and {q(len(unthrottled), 'chain')} "
                f"{have(len(unthrottled))} no rate limiting (throttling).")
    if not facts.has("chains"):
        evidence += f" Escalation chains could not be read: {_gap(facts, 'chains')}."
    check = Check("alert_routing", area, status, evidence)
    if status == GREEN:
        check.good.append("Alert rules route to populated, rate-limited escalation chains.")
    if orphan or not rules:
        check.weak.append("Some alert rules do not route to a valid escalation chain.")
        check.actions.append((0, "Fix alert rules that reference missing escalation chains."))
    if dup or empty:
        check.actions.append((1, "Review alert-rule priorities and remove empty escalation chains."))
    if unthrottled:
        check.actions.append((1, "Enable escalation-chain rate limiting to protect ticketing during alert floods."))
    return check


AUDIT_CATEGORIES = (
    ("Active Discovery / instance changes", re.compile(r"(?i)active ?discovery|instance")),
    ("Logins and sessions", re.compile(r"(?i)log(ged)? ?in|log(ged)? ?out|session")),
    ("SDT and alert actions", re.compile(r"(?i)\bsdt\b|acknowledg|alert")),
    ("Resource and group changes", re.compile(r"(?i)device|resource|group|host")),
    ("Module and settings changes", re.compile(r"(?i)datasource|logicmodule|propertysource|setting|collector|role|user")),
)


def _audit_breakdown(items: List[Dict[str, Any]]) -> List[Tuple[str, int]]:
    counts: Counter = Counter()
    for entry in items:
        text = str(entry.get("description") or "")
        for label, pattern in AUDIT_CATEGORIES:
            if pattern.search(text):
                counts[label] += 1
                break
        else:
            counts["Other"] += 1
    return counts.most_common()


def check_audit(facts: Facts) -> Check:
    area = "Audit log and discovery churn"
    if not facts.has("audit"):
        return _unavailable("audit", area, _gap(facts, "audit") or "no audit data returned")
    audit = facts.get("audit")
    items = audit.get("items") or []
    breakdown = dict(_audit_breakdown(items))
    churn = breakdown.get(AUDIT_CATEGORIES[0][0], 0)
    churn_pct = pct(churn, len(items)) or 0
    status = AMBER if churn_pct > THRESHOLDS["audit_discovery_churn_warn_pct"] else GREEN
    sample_note = f" (classified from the latest {n(len(items))})" if audit.get("capped") else ""
    check = Check(
        "audit", area, status,
        f"{n(audit.get('total'))} audit-log entries in the last 24 hours{sample_note}; {p(churn_pct)} are Active "
        "Discovery or instance changes.",
    )
    if status == AMBER:
        check.weak.append(f"The audit log is dominated by Active Discovery instance changes ({p(churn_pct)}), indicating discovery churn that can weaken topology and correlation.")
        check.actions.append((1, "Investigate Active Discovery churn: tune discovery schedules and instance filters on the noisiest modules."))
    return check


def check_users(facts: Facts) -> Check:
    area = "Users and access"
    if not facts.has("users"):
        return _unavailable("users", area, _gap(facts, "users") or "no user data returned")
    t = THRESHOLDS
    users = facts.get("users")
    now = facts.get("now")
    people = [u for u in users if not u.get("apionly")]
    api_only = len(users) - len(people)
    recent = inactive = never = 0
    for u in people:
        last = _epoch_s(u.get("lastLoginOn"))
        if not last:
            never += 1
        elif now - last <= 30 * 86400:
            recent += 1
        elif now - last > t["inactive_user_days"] * 86400:
            inactive += 1
    stale_pct = pct(inactive + never, len(people)) or 0
    status = AMBER if stale_pct > t["inactive_user_warn_pct"] else GREEN
    check = Check(
        "users", area, status,
        f"{n(len(people))} interactive users and {n(api_only)} API-only users. {n(recent)} users logged in within "
        f"30 days; {n(inactive)} {have(inactive)} not logged in for over {t['inactive_user_days']} days and {n(never)} {have(never)} never logged in.",
    )
    if recent:
        check.good.append(f"{n(recent)} users actively log in to the portal (last 30 days).")
    if status == AMBER:
        check.weak.append(f"{p(stale_pct)} of interactive user accounts are inactive or never used.")
        check.actions.append((1, "Review inactive and never-used user accounts; disable or remove them."))
    return check


ORDER = [
    check_core_adoption, check_device_footprint, check_telemetry_depth, check_datapoint_collection,
    check_alerting, check_dashboards, check_services, check_logs, check_uptime, check_websites,
    check_collectors, check_logicmodules, check_config, check_cloud,
    check_alert_routing, check_audit, check_users,
]

CAPABILITIES = [
    ("Core resources", ["core_adoption", "device_footprint", "telemetry_depth"],
     "Protect the foundation, remove stale objects and improve coverage quality before adding more volume."),
    ("Dashboards / reports", ["dashboards"],
     "Rationalise by persona: NOC, service owner, delivery lead and leadership. Measure views and recipient use."),
    ("Service Insights", ["services"],
     "Attach owner, criticality, SLA/SLO and business impact. Use service context in incident prioritisation."),
    ("LM Logs", ["logs"],
     "Review top consumers, retention and partitions; build tracked investigations and high-value LogAlerts."),
    ("Edwin AI", [],
     "Proceed only through a gated proof of value with named use cases, signed baselines and a production decision."),
    ("LM Uptime", ["uptime"],
     "Activate external synthetic monitoring for the highest-value journeys and use results in SLA reporting."),
    ("Websites", ["websites"],
     "Validate journey coverage and add geographic, dependency and business-impact context."),
    ("Config monitoring", ["config"],
     "Assess configuration backup, drift, compliance and change-correlation coverage across network infrastructure."),
    ("Cloud / Kubernetes / OTel", ["cloud"],
     "Confirm whether scope exists elsewhere; extend hybrid observability and OpenTelemetry where appropriate."),
    ("Collectors / LogicModules", ["collectors", "logicmodules"],
     "Create a governance register, standardise sizing and failover, remediate discovery churn, retire unused clones."),
]


# --------------------------------------------------------------------------- assemble


def assess(facts: Facts) -> Assessment:
    checks = [fn(facts) for fn in ORDER]
    by_key = {c.key: c for c in checks}

    points = {GREEN: 2, AMBER: 1, RED: 0}
    known = [c for c in checks if c.status in points]
    score = (sum(points[c.status] for c in known) / (2 * len(known))) if known else 0
    reds = [c for c in checks if c.status == RED]
    if not known:
        overall = UNKNOWN
    elif score >= 0.75 and not reds:
        overall = GREEN
    elif score >= 0.45:
        overall = AMBER
    else:
        overall = RED
    counts = Counter(c.status for c in checks)
    overall_summary = (
        f"Overall condition: {counts[GREEN]} areas green, {counts[AMBER]} amber, {counts[RED]} red and "
        f"{counts[UNKNOWN]} not assessable from the API."
    )
    if reds:
        overall_summary += " Constrained by " + _join([c.area for c in reds]) + "."

    footprint_ok = all(by_key[k].status in (GREEN, UNKNOWN) for k in ("device_footprint", "telemetry_depth"))
    ops_names = {"alerting": "alert noise", "collectors": "collector health", "logicmodules": "LogicModule governance",
                 "alert_routing": "alert routing", "audit": "discovery churn"}
    ops_weak = [name for k, name in ops_names.items() if by_key[k].status in (RED, AMBER)]
    if footprint_ok and ops_weak:
        conclusion = ("This is not a monitoring-footprint problem. It is an operationalisation and governance problem. "
                      "The portal is collecting substantial telemetry, but " + _join(ops_weak) +
                      " need attention before that telemetry turns into lower effort and automated response.")
    elif not footprint_ok:
        conclusion = ("Monitoring coverage itself needs attention: device health or telemetry depth is below standard. "
                      "Fix the foundation before investing in higher-level capabilities.")
    else:
        conclusion = ("The portal foundation and operations are in good shape. Focus on extending service context, "
                      "external checks and AIOps readiness.")

    tiles = _tiles(facts, by_key)
    summary = _summary(facts, checks, by_key)
    actions = sorted(((prio, text) for c in checks for prio, text in c.actions), key=lambda a: a[0])
    seen = set()
    unique_actions = []
    for prio, text in actions:
        if text not in seen:
            seen.add(text)
            unique_actions.append((prio, text))
    direction = [text for _, text in unique_actions[:4]] or ["Maintain current operating standards and re-run this health check quarterly."]

    anchors, expansion, risks = _stickiness(facts, by_key)
    readiness, readiness_status, readiness_text = _readiness(facts, by_key)
    open_items = (facts.get("open_alerts") or {}).get("items") or []
    sources = Counter(str(a.get("resourceTemplateName") or a.get("type") or "Unknown") for a in open_items)
    top_sources = sources.most_common(10)
    severity = Counter({2: "Warning", 3: "Error", 4: "Critical"}.get(a.get("severity"), "Other") for a in open_items)
    use_cases = _use_cases(top_sources)
    plan = _plan(unique_actions)

    _, _, _, modules = _module_stats(facts)
    module_rows = []
    for label, info in modules.items():
        items = info.get("items")
        if items is None:
            module_rows.append((label, n(info.get("total")), "n/a", "n/a"))
            continue
        clones = sum(1 for m in items if CLONE_PATTERN.search(m.get("name") or "") or CLONE_PATTERN.search(m.get("displayName") or ""))
        unused = sum(1 for m in items if str(m.get("appliesTo") or "").strip().lower() in ("", "false", "false()"))
        module_rows.append((label, n(info.get("total")), n(clones), n(unused)))
    for key, reason in facts.gaps.items():
        if key.startswith("modules."):
            module_rows.append((key.split(".", 1)[1], "Not available", reason, ""))

    audit_rows = _audit_breakdown(((facts.get("audit") or {}).get("items")) or [])
    collector_rows = []
    for c in sorted(facts.get("collectors") or [], key=lambda c: -int(c.get("numberOfInstances") or 0))[:100]:
        collector_rows.append([
            str(c.get("description") or c.get("hostname") or c.get("id")),
            "Down" if c.get("isDown") else "Up",
            str(c.get("collectorSize") or "n/a"),
            str(c.get("build") or "n/a"),
            n(c.get("numberOfHosts") or 0),
            n(c.get("numberOfInstances") or 0),
            "Yes" if c.get("backupAgentId") else "No",
            str(c.get("collectorGroupName") or "n/a"),
        ])
    usage_rows = [(k, n(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v))
                  for k, v in sorted((facts.get("usage") or {}).items())]

    for cap, keys, motion in CAPABILITIES:
        for key in keys:
            by_key[key].capability = by_key[key].capability or cap

    return Assessment(
        facts=facts, checks=checks, overall=overall, overall_summary=overall_summary,
        conclusion_title="Primary conclusion", conclusion=conclusion, tiles=tiles,
        summary_paragraphs=summary, direction=direction, anchors=anchors, expansion=expansion,
        risks=risks, readiness=readiness, readiness_status=readiness_status, readiness_text=readiness_text,
        use_cases=use_cases, plan=plan, top_alert_sources=top_sources,
        alert_severity=dict(severity), module_rows=module_rows, audit_rows=audit_rows,
        collector_rows=collector_rows, usage_rows=usage_rows,
    )


def capability_rows(a: Assessment) -> List[Tuple[str, str, str, str]]:
    by_key = {c.key: c for c in a.checks}
    rows = []
    for cap, keys, motion in CAPABILITIES:
        if not keys:
            rows.append((cap, "Edwin AI usage is not exposed by the REST API. See the AIOps readiness section.",
                         HEALTH_WORD[UNKNOWN], motion))
            continue
        selected = [by_key[k] for k in keys]
        status = _worst(*(c.status for c in selected))
        texts = [c.usage if c.usage is not None else c.evidence.split(". ")[0].rstrip(".") + "." for c in selected]
        usage = " ".join(t for t in texts if t)
        rows.append((cap, usage, HEALTH_WORD[status], motion))
    return rows


def _join(items: List[str]) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _tiles(facts: Facts, by_key: Dict[str, Check]) -> List[Tuple[str, str, str, str]]:
    devices = len(_devices(facts)) if facts.has("devices") else None
    contract = facts.contract.get("devices")
    dev_sub = (f"{p(pct(devices, contract))} deployed; {n(contract - devices)} headroom"
               if contract and devices is not None else "Monitored devices")
    instances = facts.get("_instances")
    per = facts.get("_instances_per_device")
    day = (facts.get("alerts_24h") or {}).get("total")
    open_total = (facts.get("open_alerts") or {}).get("total")
    services = _services_count(facts)
    sites = facts.get("websites")
    external = sum(1 for s in sites if not s.get("isInternal")) if sites is not None else None
    uptime_contract = facts.contract.get("uptime_checks")
    return [
        (n(devices), f"/ {n(contract)}" if contract else "", "Devices", dev_sub),
        (n(instances), "", "Instances", f"Approximately {n(round(per))} per device" if per else "Collector-reported"),
        (n(day), "", "Alerts in prior day", f"Approximately {n(day * 30)} over 30 days" if day is not None else "Not available"),
        (n(open_total), "", "Open alerts", f"{p(pct(open_total, devices))} of device count" if open_total is not None and devices else "Current active alerts"),
        (n(services), "", "Services", "Service Insights footprint" if services else "Service Insights not in use"),
        (n(external), f"/ {n(uptime_contract)}" if uptime_contract else "", "External uptime checks",
         "Configured synthetic checks" if external else "No external checks configured"),
    ]


def _summary(facts: Facts, checks: List[Check], by_key: Dict[str, Check]) -> List[str]:
    devices = len(_devices(facts)) if facts.has("devices") else None
    bits = []
    if devices is not None:
        bits.append(f"{n(devices)} devices")
    if facts.get("_instances") is not None:
        bits.append(f"{n(facts.get('_instances'))} instances")
    if facts.has("dashboards"):
        bits.append(f"{n(facts.get('dashboards'))} dashboards")
    services = _services_count(facts)
    if services is not None:
        bits.append(f"{n(services)} services")
    module_total = _module_stats(facts)[0] if facts.has("modules") else None
    if module_total:
        bits.append(f"{n(module_total)} LogicModules")
    if facts.has("collectors"):
        bits.append(f"{n(len(facts.get('collectors')))} collectors")
    paragraphs = []
    if bits:
        paragraphs.append(
            f"The {facts.portal_name} portal ({facts.portal_host}) currently monitors " + _join(bits) +
            ". This report was generated automatically from read-only LogicMonitor REST API data.")
    reds = [c for c in checks if c.status == RED]
    ambers = [c for c in checks if c.status == AMBER]
    if reds or ambers:
        text = "Maturity is uneven. "
        if reds:
            text += "Red areas: " + _join([c.area for c in reds]) + ". "
        if ambers:
            text += "Amber areas: " + _join([c.area for c in ambers]) + ". "
        alert = by_key["alerting"]
        if alert.status in (RED, AMBER):
            text += alert.evidence.split(". Dependent")[0] + "."
        paragraphs.append(text.strip())
    else:
        paragraphs.append("All areas that could be assessed from the API are green.")
    unknown = [c.area for c in checks if c.status == UNKNOWN]
    if unknown:
        paragraphs.append(
            "Some areas cannot be assessed from the REST API alone (" + _join(unknown) + "). They are listed in "
            "Appendix B with the reason, and should be validated on the portal Usage and Portal Information pages "
            "or from the commercial system of record.")
    return paragraphs


def _stickiness(facts: Facts, by_key: Dict[str, Check]) -> Tuple[List[str], List[str], List[str]]:
    anchors = []
    if facts.get("_reports_24h"):
        anchors.append("Scheduled and custom reporting")
    total, clones, _, modules = _module_stats(facts)
    if total:
        anchors.append(f"LogicModule estate ({n(total)} modules)")
    if facts.has("collectors") and facts.get("collectors"):
        anchors.append("Collector and discovery architecture")
    services = _services_count(facts)
    if services:
        anchors.append(f"{n(services)} service models")
    if facts.get("dashboards"):
        anchors.append(f"{n(facts.get('dashboards'))} dashboards")
    if facts.has("chains") and facts.get("chains"):
        anchors.append("Escalation chains and alert routing")
    expansion = ["Edwin AI event intelligence and investigation", "LM Logs operationalisation"]
    if by_key["uptime"].status in (RED, UNKNOWN):
        expansion.append("LM Uptime for digital journeys")
    expansion.append("ITSM / CMDB enrichment")
    if by_key["services"].status != GREEN:
        expansion.append("Service Insights maturity")
    if by_key["config"].status != GREEN:
        expansion.append("Config monitoring expansion")
    if by_key["cloud"].status != GREEN:
        expansion.append("Cloud, Kubernetes and OpenTelemetry coverage")
    risks = [w for c in by_key.values() if c.status == RED for w in c.weak[:1]]
    risks += [w for c in by_key.values() if c.status == AMBER for w in c.weak[:1]]
    if not risks:
        risks = ["No material platform risks detected from API data."]
    return anchors or ["No strong anchors detected from API data."], expansion, risks[:6]


def _readiness(facts: Facts, by_key: Dict[str, Check]) -> Tuple[List[Tuple[str, str, str]], str, str]:
    rows = []
    day = (facts.get("alerts_24h") or {}).get("total")
    rows.append(("Alert baseline available",
                 GREEN if day is not None else UNKNOWN,
                 f"{n(day)} alerts in the last 24 hours can seed the baseline." if day is not None
                 else "Alert volume could not be read."))
    alert = by_key["alerting"]
    rows.append(("Alert noise under control", alert.status, alert.evidence.split(". Dependent")[0] + "."))
    col = by_key["collectors"]
    rows.append(("Collector stability", col.status, col.evidence.split(".")[0] + "; see Collectors in the scorecard."))
    svc = by_key["services"]
    rows.append(("Service context for correlation", GREEN if svc.status == AMBER else svc.status, svc.evidence.split(".")[0] + "."))
    aud = by_key["audit"]
    rows.append(("Topology and discovery stability", aud.status, aud.evidence))
    if facts.has("integrations"):
        integrations = facts.get("integrations")
        types = Counter(str(i.get("type") or "unknown").lower() for i in integrations)
        has_itsm = any("servicenow" in t or "service now" in t for t in types)
        status = GREEN if has_itsm else AMBER if integrations else RED
        text = (", ".join(f"{n(v)} {k}" for k, v in types.most_common()) + " integrations configured.") if integrations else "No integrations configured."
        if not has_itsm:
            text += " No ServiceNow integration detected."
        rows.append(("ITSM integration path", status, text))
    else:
        rows.append(("ITSM integration path", UNKNOWN, f"Integrations could not be read: {_gap(facts, 'integrations')}."))
    statuses = [s for _, s, _ in rows]
    red_count = statuses.count(RED)
    if all(s == GREEN for s in statuses):
        status, text = GREEN, ("Recommendation: proceed with a value-led Edwin AI proof of value. Technical entry criteria "
                               "visible in the API are met; agree business baselines and success criteria first.")
    elif red_count >= 2:
        status, text = RED, ("Recommendation: remediate the red entry criteria before starting an Edwin AI proof of value; "
                             "otherwise AI will be used to mask platform debt.")
    else:
        status, text = AMBER, ("Recommendation: proceed with a tightly scoped, value-led proof of value after the entry "
                               "criteria below are locked. The telemetry is a credible proving ground, but the baseline "
                               "and operating conditions are not yet fully controlled.")
    return rows, status, text


def _use_cases(top_sources: List[Tuple[str, int]]) -> List[Tuple[str, str, str]]:
    cases = []
    for name, count in top_sources[:3]:
        cases.append((
            f"{name} alert correlation",
            f"{n(count)} open alerts come from {name}. Group duplicate and related alerts into actionable situations "
            "while retaining every signal required for SLA reporting.",
            "Raw alerts vs grouped insights; duplicate-ticket reduction; NOC minutes saved; reporting integrity preserved.",
        ))
    cases.append((
        "ITSM incident enrichment and RCA",
        "Pass affected service, probable cause, related alerts and logs into the incident workflow.",
        "Ticket quality; handoff time; RCA consistency; escalation avoidance; restoration time.",
    ))
    return cases


def _plan(actions: List[Tuple[int, str]]) -> List[Tuple[str, str, str]]:
    def bucket(prio: int) -> List[str]:
        return [t for p_, t in actions if p_ == prio]

    first = ["Assign one accountable owner per health-check action and establish signed operational baselines."] + bucket(0)
    second = bucket(1) or ["Review alert-rule priorities, service ownership and module governance."]
    third = bucket(2) + ["Run a gated Edwin AI proof of value against the baseline; agree production scope, "
                         "operating model and ownership."]
    return [
        ("0-30 days", "\n".join(first), "Named owners and dates; baseline pack; first alert-noise and collector-health improvements."),
        ("31-60 days", "\n".join(second), "Governance register; service-quality map; approved proof-of-value charter."),
        ("61-90 days", "\n".join(third), "Validated business case; production decision; rollout plan."),
    ]
