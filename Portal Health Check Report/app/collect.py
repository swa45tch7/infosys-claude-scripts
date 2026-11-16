"""Collect portal facts for the health check using read-only GET calls.

Every area is collected independently. If one endpoint is refused (for example
the token's role lacks View on that area) the area is recorded as a gap with a
plain-language reason and the run continues. Only HTTP 401 stops the run,
because nothing else can succeed with rejected credentials.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from lm_client import ApiError, LmClient, unwrap

log = logging.getLogger("lm_health")

OPEN_ALERT_CAP = 20000
ALERT_24H_COUNT_CAP = 50000
AUDIT_CAP = 5000
DAY = 86400

DEVICE_FIELDS = (
    "id,displayName,deviceType,hostStatus,disableAlerting,currentCollectorId,"
    "preferredCollectorId,hostGroupIds,createdOn,autoPropsUpdatedOn,sdtStatus"
)
COLLECTOR_FIELDS = (
    "id,description,hostname,platform,status,isDown,build,collectorSize,"
    "backupAgentId,enableFailBack,collectorGroupId,collectorGroupName,"
    "numberOfHosts,numberOfInstances,updatedOn"
)
ALERT_FIELDS = "id,severity,acked,sdted,startEpoch,monitorObjectName,resourceTemplateName,type"
RULE_FIELDS = "id,name,priority,escalatingChainId,escalationInterval,levelStr"
CHAIN_FIELDS = "id,name,destinations,enableThrottling,throttlingAlerts,throttlingPeriod"
MODULE_FIELDS = "id,name,displayName,appliesTo,group"
MODULE_TYPES = {
    "DataSources": ("/setting/datasources", True),
    "EventSources": ("/setting/eventsources", True),
    "ConfigSources": ("/setting/configsources", True),
    "PropertySources": ("/setting/propertyrules", False),
    "TopologySources": ("/setting/topologysources", False),
    "LogSources": ("/setting/logsources", False),
    "JobMonitors": ("/setting/batchjobs", False),
}


class AuthError(RuntimeError):
    """Credentials were rejected (HTTP 401)."""


class PortalUnreachable(RuntimeError):
    """The very first request could not reach the portal at all."""


@dataclass
class Facts:
    portal_name: str
    portal_host: str
    collected_at: datetime
    values: Dict[str, Any] = field(default_factory=dict)
    gaps: Dict[str, str] = field(default_factory=dict)
    endpoints: List[str] = field(default_factory=list)
    request_count: int = 0
    throttle_events: int = 0
    wait_seconds: float = 0.0
    contract: Dict[str, Optional[float]] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def has(self, key: str) -> bool:
        return key in self.values and self.values[key] is not None


def explain(exc: ApiError) -> str:
    if exc.status == 403:
        return "the API token's role does not have View permission for this area"
    if exc.status == 404:
        return "this endpoint is not available on this portal or API version"
    if exc.status == 429:
        return "LogicMonitor kept rate-limiting this request after several retries"
    if exc.status == 0:
        return f"the request could not be completed ({exc})"
    return f"the API returned HTTP {exc.status}"


def collect_facts(
    client: LmClient,
    portal_name: str,
    contract: Optional[Dict[str, Optional[float]]] = None,
    now: Optional[float] = None,
    progress: Callable[[str], None] = lambda message: None,
) -> Facts:
    now = time.time() if now is None else now
    facts = Facts(
        portal_name=portal_name,
        portal_host=client.portal_host,
        collected_at=datetime.fromtimestamp(now, tz=timezone.utc),
        contract=dict(contract or {}),
    )
    facts.values["now"] = now

    steps = [
        ("Checking API access and usage summary", "usage", lambda: _usage(client)),
        ("Reading resources (devices)", "devices", lambda: client.collect("/device/devices", {"fields": DEVICE_FIELDS})),
        ("Reading collectors", "collectors", lambda: client.collect("/setting/collector/collectors", {"fields": COLLECTOR_FIELDS})),
        ("Reading collector groups", "collector_groups", lambda: client.total("/setting/collector/groups")),
        ("Reading open alerts", "open_alerts", lambda: _open_alerts(client)),
        ("Counting alerts raised in the last 24 hours", "alerts_24h", lambda: _alerts_since(client, int(now) - DAY)),
        ("Reading alert rules", "alert_rules", lambda: client.collect("/setting/alert/rules", {"fields": RULE_FIELDS})),
        ("Reading escalation chains", "chains", lambda: client.collect("/setting/alert/chains", {"fields": CHAIN_FIELDS})),
        ("Counting dashboards", "dashboards", lambda: client.total("/dashboard/dashboards")),
        ("Counting dashboard widgets", "widgets", lambda: client.total("/dashboard/widgets")),
        ("Reading reports", "reports", lambda: client.collect("/report/reports", {"fields": "id,name,type,lastGenerateOn"})),
        ("Reading websites / uptime checks", "websites", lambda: client.collect(
            "/website/websites", {"fields": "id,name,type,isInternal,disableAlerting,stopMonitoring,overallAlertLevel"})),
        ("Reading LogicModules", "modules", lambda: _modules(client, facts)),
        ("Reading audit log (last 24 hours)", "audit", lambda: _audit(client, int(now) - DAY)),
        ("Reading users", "users", lambda: client.collect("/setting/admins", {"fields": "id,username,status,lastLoginOn,apionly"})),
        ("Reading integrations", "integrations", lambda: client.collect("/setting/integrations", {"fields": "id,name,type"})),
    ]

    reached = False
    for label, key, fn in steps:
        progress(label)
        try:
            facts.values[key] = fn()
            reached = True
        except ApiError as exc:
            if exc.status == 401:
                raise AuthError(str(exc)) from None
            if exc.status == 0 and not reached:
                raise PortalUnreachable(str(exc)) from None
            reached = reached or exc.status != 0
            facts.gaps[key] = explain(exc)
            log.warning("%s: not available (%s)", label, facts.gaps[key])

    facts.endpoints = list(client.endpoints_used)
    facts.request_count = client.request_count
    facts.throttle_events = client.limiter.throttle_events
    facts.wait_seconds = client.limiter.total_wait
    return facts


def _usage(client: LmClient) -> Dict[str, Any]:
    payload = unwrap(client.get("/metrics/usage"))
    return payload if isinstance(payload, dict) else {}


def _open_alerts(client: LmClient) -> Dict[str, Any]:
    items = client.collect("/alert/alerts", {"fields": ALERT_FIELDS}, max_items=OPEN_ALERT_CAP)
    total = client.total("/alert/alerts") if len(items) >= OPEN_ALERT_CAP else len(items)
    return {"items": items, "total": total if total is not None else len(items),
            "capped": len(items) >= OPEN_ALERT_CAP}


def _alerts_since(client: LmClient, since: int) -> Dict[str, Any]:
    params = {"filter": f'cleared:"*",startEpoch>{since}'}
    total = client.total("/alert/alerts", params)
    if total is not None:
        return {"total": total, "capped": False}
    params["fields"] = "id"
    items = client.collect("/alert/alerts", params, max_items=ALERT_24H_COUNT_CAP)
    return {"total": len(items), "capped": len(items) >= ALERT_24H_COUNT_CAP}


def _modules(client: LmClient, facts: Facts) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for label, (path, detailed) in MODULE_TYPES.items():
        try:
            if detailed:
                items = client.collect(path, {"fields": MODULE_FIELDS})
                result[label] = {"total": len(items), "items": items}
            else:
                result[label] = {"total": client.total(path), "items": None}
        except ApiError as exc:
            if exc.status == 401:
                raise
            facts.gaps[f"modules.{label}"] = explain(exc)
    if not result:
        raise ApiError(403, "/setting/datasources", "no LogicModule type could be read")
    return result


def _audit(client: LmClient, since: int) -> Dict[str, Any]:
    params = {"filter": f"happenedOn>{since}", "fields": "id,happenedOn,username,description"}
    items = client.collect("/setting/accesslogs", params, max_items=AUDIT_CAP)
    total = len(items)
    if total >= AUDIT_CAP:
        reported = client.total("/setting/accesslogs", {"filter": f"happenedOn>{since}"})
        total = reported if reported is not None else total
    return {"items": items, "total": total, "capped": len(items) >= AUDIT_CAP}
