#!/usr/bin/env python3
"""Read-only collection of the LogicMonitor data behind the health check report.

Every endpoint is a GET against REST API v3. A failing endpoint (for example a
403 because the token's role cannot view users) is recorded on its dataset and
collection carries on, so the report can still be produced with that section
marked as unavailable.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from engine import ALERT_FIELDS, COLLECTOR_FIELDS, DEVICE_FIELDS, RULE_FIELDS  # noqa: E402
from lm_api import PAGE_SIZE, ApiError, LmClient, _items, _total  # noqa: E402

DAY_SECONDS = 86400
REPORT_ALERT_FIELDS = ALERT_FIELDS + ",type,rule,chain,dependencyRole,dependencyRoutingState,adAlert"


@dataclass(frozen=True)
class EndpointSpec:
    name: str
    label: str
    path: str
    fields: str = ""
    query: tuple[tuple[str, str], ...] = ()
    mode: str = "list"  # "list" pages through items, "count" only reads the total
    max_items: int | None = None


@dataclass
class Dataset:
    name: str
    label: str
    endpoint: str
    items: list[dict[str, Any]] = field(default_factory=list)
    total: int | None = None
    error: str = ""
    partial: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def count(self) -> int | None:
        if not self.ok:
            return None
        if self.total is not None and self.total >= 0 and self.total >= len(self.items):
            return self.total
        return len(self.items)


@dataclass
class PortalData:
    portal: str
    collected_at: float
    datasets: dict[str, Dataset]
    module_usage: dict[str, int] = field(default_factory=dict)

    def get(self, name: str) -> Dataset:
        return self.datasets.get(name) or Dataset(name, name, "", error="not collected")


def endpoint_specs(now: float, rules: dict[str, Any]) -> list[EndpointSpec]:
    day_ago = int(now) - DAY_SECONDS
    audit_since = int(now) - int(rules["audit_window_days"]) * DAY_SECONDS
    return [
        EndpointSpec("devices", "Devices", "/device/devices", DEVICE_FIELDS),
        EndpointSpec(
            "device_groups",
            "Device groups",
            "/device/groups",
            "id,name,fullPath,parentId,groupType,numOfHosts,numOfDirectDevices,"
            "numOfAWSDevices,numOfAzureDevices,numOfGcpDevices,numOfKubernetesDevices",
        ),
        EndpointSpec(
            "collectors",
            "Collectors",
            "/setting/collector/collectors",
            COLLECTOR_FIELDS + ",upTime,escalatingChainId",
        ),
        EndpointSpec(
            "collector_groups",
            "Collector groups",
            "/setting/collector/groups",
            "id,name,description,autoBalance,numOfCollectors",
        ),
        EndpointSpec(
            "alerts_active",
            "Open alerts",
            "/alert/alerts",
            REPORT_ALERT_FIELDS,
            (("needMessage", "false"),),
            max_items=int(rules["alert_max_records_open"]),
        ),
        EndpointSpec(
            "alerts_24h",
            "Alerts in prior 24 h",
            "/alert/alerts",
            REPORT_ALERT_FIELDS,
            (("filter", f'startEpoch>:{day_ago},cleared:"*"'), ("needMessage", "false")),
            max_items=int(rules["alert_max_records_24h"]),
        ),
        EndpointSpec("alert_rules", "Alert rules", "/setting/alert/rules", RULE_FIELDS),
        EndpointSpec("escalation_chains", "Escalation chains", "/setting/alert/chains"),
        EndpointSpec("integrations", "Integrations", "/setting/integrations", "id,name,type"),
        EndpointSpec(
            "datasources",
            "DataSources",
            "/setting/datasources",
            "id,name,displayName,group,collectMethod,hasMultiInstances,installationMetadata,dataPoints",
        ),
        EndpointSpec("eventsources", "EventSources", "/setting/eventsources", mode="count"),
        EndpointSpec(
            "configsources", "ConfigSources", "/setting/configsources", "id,name,displayName,group"
        ),
        EndpointSpec("propertysources", "PropertySources", "/setting/propertyrules", mode="count"),
        EndpointSpec("topologysources", "TopologySources", "/setting/topologysources", mode="count"),
        EndpointSpec("jobmonitors", "JobMonitors", "/setting/batchjobs", mode="count"),
        EndpointSpec("logsources", "LogSources", "/setting/logsources", mode="count"),
        EndpointSpec("appliesto_functions", "AppliesTo functions", "/setting/functions", mode="count"),
        EndpointSpec("sysoid_maps", "SNMP SysOID maps", "/setting/oids", mode="count"),
        EndpointSpec(
            "dashboards", "Dashboards", "/dashboard/dashboards", "id,name,groupName,owner,sharable"
        ),
        EndpointSpec("widgets", "Dashboard widgets", "/dashboard/widgets", mode="count"),
        EndpointSpec(
            "reports",
            "Reports",
            "/report/reports",
            "id,name,type,format,lastGenerateOn,schedule,recipients",
        ),
        EndpointSpec(
            "websites",
            "Websites",
            "/website/websites",
            "id,name,type,isInternal,status,stopMonitoring,disableAlerting,overallAlertLevel",
        ),
        EndpointSpec(
            "users",
            "Users",
            "/setting/admins",
            "id,username,status,roles,lastLoginOn,twoFAEnabled,apionly",
        ),
        EndpointSpec("roles", "Roles", "/setting/roles", "id,name"),
        EndpointSpec(
            "api_tokens",
            "API tokens",
            "/setting/admins/apitokens",
            "id,adminId,adminName,status,lastUsedOn,createdOn",
        ),
        EndpointSpec(
            "netscans", "NetScans", "/setting/netscans", "id,name,method,collector,schedule"
        ),
        EndpointSpec("sdts", "SDTs", "/sdt/sdts"),
        EndpointSpec(
            "audit_logs",
            "Audit log",
            "/setting/accesslogs",
            "id,username,happenedOn,description",
            (("filter", f"happenedOn>:{audit_since}"), ("sort", "-happenedOn")),
            max_items=int(rules["audit_max_records"]),
        ),
    ]


def describe_error(exc: BaseException) -> str:
    """Short, secret-free reason suitable for the report and the console."""
    if isinstance(exc, ApiError):
        status = exc.status
        message = _api_message(exc.body)
        if status == 401:
            return "HTTP 401: authentication failed"
        if status == 403:
            return "HTTP 403: the API token's role does not have view permission for this area"
        if status == 404:
            return "HTTP 404: endpoint not available on this portal"
        if status == 429:
            return "HTTP 429: rate limited after retries"
        if status == 0:
            if message.startswith("Non-JSON response"):
                return "the portal returned a non-JSON response"
            return f"network error: {message}" if message else "network error"
        return f"HTTP {status}: {message}" if message else f"HTTP {status}"
    text = str(exc).strip() or exc.__class__.__name__
    return text[:200]


def _api_message(body: str) -> str:
    body = (body or "").strip()
    try:
        parsed = json.loads(body)
    except (TypeError, ValueError):
        return body[:200]
    if isinstance(parsed, dict):
        for key in ("errorMessage", "message", "errmsg"):
            if parsed.get(key):
                return str(parsed[key])[:200]
    return body[:200]


class LiveSource:
    """Reads datasets from a portal through the shared read-only LmClient."""

    def __init__(self, client: LmClient) -> None:
        self.client = client

    @property
    def portal(self) -> str:
        return f"{self.client.config.account}.{self.client.config.domain}"

    def fetch(self, spec: EndpointSpec) -> Dataset:
        dataset = Dataset(spec.name, spec.label, spec.path)
        query = dict(spec.query)
        if spec.fields or spec.mode == "count":
            query["fields"] = spec.fields or "id"
        try:
            try:
                self._load(spec, query, dataset)
            except ApiError as exc:
                if exc.status != 400 or "fields" not in query:
                    raise
                query.pop("fields")
                self._load(spec, query, dataset)
        except Exception as exc:  # one endpoint must never stop the report
            dataset.items, dataset.total = [], None
            dataset.error = describe_error(exc)
        return dataset

    def _load(self, spec: EndpointSpec, query: dict[str, str], dataset: Dataset) -> None:
        if spec.mode == "count":
            query = dict(query, size="1", offset="0")
            payload = self.client.get(spec.path, query)
            total = _total(payload)
            if total is not None and total < 0:
                dataset.partial = f"API reported at least {abs(total):,} records"
                total = abs(total)
            dataset.total = total if total is not None else len(_items(payload))
            return
        items: list[dict[str, Any]] = []
        offset = 0
        total: int | None = None
        while True:
            page = dict(query, size=str(PAGE_SIZE), offset=str(offset))
            try:
                payload = self.client.get(spec.path, page)
            except ApiError as exc:
                if not items:
                    raise
                dataset.partial = f"stopped after {len(items):,} records ({describe_error(exc)})"
                break
            batch = _items(payload)
            page_total = _total(payload)
            if page_total is not None:
                total = page_total
            items.extend(batch)
            if spec.max_items is not None and len(items) >= spec.max_items:
                items = items[: spec.max_items]
                dataset.partial = f"capped at {spec.max_items:,} records"
                break
            if len(batch) < PAGE_SIZE:
                break
            if total is not None and total >= 0 and len(items) >= total:
                break
            offset += PAGE_SIZE
        dataset.items = items
        dataset.total = total if total is not None and total >= 0 else None

    def module_device_count(self, datasource_id: Any) -> int | None:
        try:
            payload = self.client.get(
                f"/setting/datasources/{int(datasource_id)}/devices",
                {"size": "1", "offset": "0", "fields": "id"},
            )
        except Exception:
            return None
        total = _total(payload)
        return abs(total) if total is not None else len(_items(payload))


class FixtureSource:
    """Reads datasets from JSON files shaped like REST API v3 responses.

    Used by the tests and by --demo. One file per dataset name, plus an optional
    meta.json ({"portal": ..., "now": ...}) and datasource_devices.json
    ({"<datasource id>": <applied device count>}).
    """

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        meta_path = self.directory / "meta.json"
        self.meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
        usage_path = self.directory / "datasource_devices.json"
        self.usage = (
            json.loads(usage_path.read_text(encoding="utf-8")) if usage_path.is_file() else {}
        )

    @property
    def portal(self) -> str:
        return str(self.meta.get("portal") or "example.logicmonitor.com")

    def fetch(self, spec: EndpointSpec) -> Dataset:
        dataset = Dataset(spec.name, spec.label, spec.path)
        path = self.directory / f"{spec.name}.json"
        if not path.is_file():
            dataset.error = "no fixture data"
            return dataset
        payload = json.loads(path.read_text(encoding="utf-8"))
        items = _items(payload)
        total = _total(payload)
        if spec.mode == "count":
            dataset.total = total if total is not None else len(items)
            return dataset
        if spec.max_items is not None and len(items) > spec.max_items:
            items = items[: spec.max_items]
            dataset.partial = f"capped at {spec.max_items:,} records"
        dataset.items = items
        dataset.total = total if total is not None and total >= 0 else None
        return dataset

    def module_device_count(self, datasource_id: Any) -> int | None:
        value = self.usage.get(str(datasource_id))
        return int(value) if value is not None else None


ProgressFn = Callable[[int, int, EndpointSpec, Dataset], None]


def collect_portal_data(
    source: LiveSource | FixtureSource,
    now: float,
    rules: dict[str, Any],
    progress: ProgressFn | None = None,
    clone_pattern: Callable[[str], bool] | None = None,
) -> PortalData:
    specs = endpoint_specs(now, rules)
    datasets: dict[str, Dataset] = {}
    for index, spec in enumerate(specs, start=1):
        dataset = source.fetch(spec)
        datasets[spec.name] = dataset
        if progress:
            progress(index, len(specs), spec, dataset)
    data = PortalData(portal=source.portal, collected_at=now, datasets=datasets)
    datasources = datasets.get("datasources")
    if clone_pattern and datasources and datasources.ok:
        limit = int(rules["module_usage_checks_max"])
        suspects = [
            ds for ds in datasources.items if clone_pattern(str(ds.get("name") or ""))
        ][:limit]
        for ds in suspects:
            count = source.module_device_count(ds.get("id"))
            if count is not None:
                data.module_usage[str(ds.get("id"))] = count
    return data
