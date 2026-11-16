"""A fictional LogicMonitor portal served from memory.

Used by ``--demo`` (to preview the report without credentials) and by the tests.
It mimics the parts of the REST API v3 the health check reads: offset/size
paging with a 1000-item maximum, ``total`` counts, the alert and audit filters,
LMv1 header checks and X-Rate-Limit-* headers. It can also inject HTTP 429,
5xx and permission errors. All names and numbers are invented.
"""

from __future__ import annotations

import json
import random
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from lm_client import sign

DEMO_ACCOUNT = "contoso-demo"
DEMO_NAME = "Contoso Manufacturing"
DEMO_ACCESS_ID = "demoAccessId"
DEMO_ACCESS_KEY = "demo-access-key-not-a-secret"


class FakeResponse:
    def __init__(self, status: int, body: Any, headers: Optional[Dict[str, str]] = None) -> None:
        self.status_code = status
        self.headers = headers or {}
        self._body = body
        self.content = json.dumps(body).encode("utf-8") if body is not None else b""
        self.text = self.content.decode("utf-8")

    def json(self) -> Any:
        return json.loads(self.text)


def build_portal(now: float, seed: int = 7) -> Dict[str, Any]:
    rng = random.Random(seed)
    day = 86400
    collectors = []
    sizes = ["large", "large", "medium", "medium", "medium", "medium", "small", "small",
             "medium", "large", "small", "medium", "extra_large", "nano"]
    instance_load = [21000, 18500, 9800, 9100, 8700, 8200, 2600, 2100, 7600, 14500, 1900, 6800, 29500, 400]
    for idx, size in enumerate(sizes, start=1):
        group = "@default" if idx in (13, 14) else ("Plant-EU" if idx <= 7 else "Plant-US")
        collectors.append({
            "id": idx, "description": f"ctso-col-{idx:02d}", "hostname": f"ctso-col-{idx:02d}.contoso.example",
            "platform": "linux" if idx % 3 else "windows", "status": 0, "isDown": idx == 11,
            "build": "36100" if idx < 10 else ("35200" if idx < 13 else "33004"),
            "collectorSize": size, "backupAgentId": 0 if idx in (3, 9, 13) else (idx % 14) + 1,
            "enableFailBack": idx != 5, "collectorGroupId": 1 if group == "@default" else 2,
            "collectorGroupName": group, "numberOfHosts": max(1, instance_load[idx - 1] // 95),
            "numberOfInstances": instance_load[idx - 1], "updatedOn": int(now) - 3600,
        })

    devices = []
    for idx in range(1, 1241):
        is_service = idx > 1222
        dead = (not is_service) and rng.random() < 0.042
        devices.append({
            "id": idx, "displayName": f"svc-{idx}" if is_service else f"ctso-dev-{idx:05d}",
            "deviceType": 6 if is_service else 0,
            "hostStatus": "dead" if dead else "normal",
            "disableAlerting": rng.random() < 0.05,
            "currentCollectorId": rng.randint(1, 14), "preferredCollectorId": rng.randint(1, 14),
            "hostGroupIds": "3", "createdOn": int(now) - rng.randint(30, 900) * day,
            "autoPropsUpdatedOn": int(now) - (rng.randint(15, 60) * day if rng.random() < 0.06 else rng.randint(0, 3) * day),
            "sdtStatus": "none-none-none",
        })

    sources = [("Wireless AP Status", 260), ("Interface Errors", 190), ("SNMP Host Down", 120),
               ("CPU Utilisation", 95), ("Disk Capacity", 80), ("BGP Peers", 45), ("IPSec Tunnels", 40),
               ("Windows Services", 35), ("Collector Data Collecting Tasks", 20), ("HTTPS Certificate", 15)]
    open_alerts = []
    aid = 1
    for name, count in sources:
        for _ in range(count):
            sev = rng.choices([2, 3, 4], [0.6, 0.3, 0.1])[0]
            open_alerts.append({
                "id": f"LMD{aid}", "severity": sev, "acked": rng.random() < 0.35, "sdted": rng.random() < 0.08,
                "startEpoch": int(now) - rng.randint(60, 20 * day), "monitorObjectName": f"ctso-dev-{rng.randint(1, 1222):05d}",
                "resourceTemplateName": name, "type": "dataSourceAlert",
            })
            aid += 1

    rules = [{"id": i, "name": f"Rule {i}", "priority": 100 + i * 10 if i != 6 else 150,
              "escalatingChainId": (i % 6) + 1, "escalationInterval": 15, "levelStr": "All"} for i in range(1, 13)]
    chains = [{"id": i, "name": f"Chain {i}",
               "destinations": [] if i == 4 else [{"type": "single", "stages": [[{"type": "email"}]]}],
               "enableThrottling": i not in (2, 5), "throttlingAlerts": 40, "throttlingPeriod": 30} for i in range(1, 7)]

    reports = []
    for i in range(1, 46):
        if i <= 14:
            last = int(now) - rng.randint(60, day - 60)
        elif i <= 38:
            last = int(now) - rng.randint(2, 25) * day
        else:
            last = 0
        reports.append({"id": i, "name": f"Report {i}", "type": "Alert", "lastGenerateOn": last})

    websites = [{"id": i, "name": f"site-{i}", "type": "webcheck" if i <= 16 else "pingcheck",
                 "isInternal": i in (3, 8, 17, 20), "disableAlerting": i == 12,
                 "stopMonitoring": i in (5, 19), "overallAlertLevel": "warn" if i == 7 else "normal"} for i in range(1, 23)]

    def modules(prefix: str, total: int, clones: int, unused: int) -> List[Dict[str, Any]]:
        items = []
        for i in range(1, total + 1):
            name = f"{prefix}_{i}"
            if i <= clones:
                name += "_clone"
            applies = "" if clones < i <= clones + unused else "isLinux()"
            items.append({"id": i, "name": name, "displayName": name, "appliesTo": applies, "group": prefix})
        return items

    audit = []
    for i in range(1, 1801):
        roll = rng.random()
        if roll < 0.62:
            text = f"Active Discovery added instance eth{i % 48} on ctso-dev-{rng.randint(1, 1222):05d}"
        elif roll < 0.80:
            text = "ops.user logged in"
        elif roll < 0.90:
            text = "Scheduled SDT for device group Plant-EU"
        else:
            text = "Updated device properties"
        audit.append({"id": i, "happenedOn": int(now) - rng.randint(0, day - 1), "username": "ops.user", "description": text})

    users = []
    for i in range(1, 49):
        api = i > 42
        if i <= 26:
            last = int(now) - rng.randint(0, 25) * day
        elif i <= 34:
            last = int(now) - rng.randint(40, 80) * day
        elif i <= 40:
            last = int(now) - rng.randint(100, 400) * day
        else:
            last = 0
        users.append({"id": i, "username": f"user{i}", "status": "active", "lastLoginOn": last, "apionly": api})

    usage = {
        "numberOfStandardDevices": 1222, "numOfServices": 18, "numOfConfigSourceDevices": 35,
        "numOfAWSDevices": 0, "numOfCombinedAzureDevices": 0, "numOfGcpDevices": 0,
        "numberOfKubernetesDevices": 0, "numOfWebsites": 22, "numOfStoppedAWSDevices": 0,
    }

    return {
        "/metrics/usage": usage,
        "/device/devices": devices,
        "/setting/collector/collectors": collectors,
        "/setting/collector/groups": [{"id": 1}, {"id": 2}, {"id": 3}],
        "/alert/alerts": open_alerts,
        "alerts_24h_total": 4130,
        "/setting/alert/rules": rules,
        "/setting/alert/chains": chains,
        "/dashboard/dashboards": [{"id": i} for i in range(1, 65)],
        "/dashboard/widgets": [{"id": i} for i in range(1, 611)],
        "/report/reports": reports,
        "/website/websites": websites,
        "/setting/datasources": modules("CTSO_DS", 820, 12, 40),
        "/setting/eventsources": modules("CTSO_ES", 40, 1, 3),
        "/setting/configsources": modules("CTSO_CS", 25, 0, 2),
        "/setting/propertyrules": [{"id": i} for i in range(1, 61)],
        "/setting/topologysources": [{"id": i} for i in range(1, 16)],
        "/setting/logsources": [{"id": i} for i in range(1, 9)],
        "/setting/batchjobs": [{"id": i} for i in range(1, 4)],
        "/setting/accesslogs": audit,
        "/setting/admins": users,
        "/setting/integrations": [{"id": 1, "name": "Teams", "type": "teams"}, {"id": 2, "name": "Email relay", "type": "email"}],
    }


class FakeSession:
    """Stands in for requests.Session. Only GET exists; anything else raises."""

    def __init__(
        self,
        now: Optional[float] = None,
        access_id: str = DEMO_ACCESS_ID,
        access_key: str = DEMO_ACCESS_KEY,
        rate_limit: int = 400,
        window: int = 60,
        throttle: Optional[Dict[str, int]] = None,
        errors: Optional[Dict[str, List[int]]] = None,
        retry_after: Optional[str] = "2",
    ) -> None:
        self.now = time.time() if now is None else now
        self.data = build_portal(self.now)
        self.access_id = access_id
        self.access_key = access_key
        self.rate_limit = rate_limit
        self.window = window
        self.remaining: Dict[str, int] = {}
        self.throttle = dict(throttle or {})
        self.errors = {k: list(v) for k, v in (errors or {}).items()}
        self.retry_after = retry_after
        self.calls: List[Dict[str, Any]] = []

    def _limit_headers(self, bucket: str) -> Dict[str, str]:
        remaining = self.remaining.get(bucket, self.rate_limit) - 1
        if remaining < 0:
            remaining = self.rate_limit - 1
        self.remaining[bucket] = remaining
        return {"X-Rate-Limit-Limit": str(self.rate_limit), "X-Rate-Limit-Remaining": str(remaining),
                "X-Rate-Limit-Window": str(self.window)}

    def get(self, url: str, params: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None,
            timeout: Any = None) -> FakeResponse:
        parsed = urlparse(url)
        path = parsed.path.split("/santaba/rest", 1)[-1]
        params = dict(params or {})
        self.calls.append({"method": "GET", "path": path, "params": params, "headers": dict(headers or {})})
        bucket = "/" + "/".join([p for p in path.split("/") if p][:2])
        limit_headers = self._limit_headers(bucket)

        if not self._authorised(path, headers or {}):
            return FakeResponse(401, {"errorMessage": "Authentication failed"}, limit_headers)
        if self.throttle.get(path, 0) > 0:
            self.throttle[path] -= 1
            hdrs = {"X-Rate-Limit-Limit": str(self.rate_limit), "X-Rate-Limit-Remaining": "0",
                    "X-Rate-Limit-Window": str(self.window)}
            if self.retry_after is not None:
                hdrs["Retry-After"] = self.retry_after
            return FakeResponse(429, {"errorMessage": "Too Many Requests"}, hdrs)
        if self.errors.get(path):
            status = self.errors[path].pop(0)
            return FakeResponse(status, {"errorMessage": f"Simulated HTTP {status}"}, limit_headers)
        if path not in self.data:
            return FakeResponse(404, {"errorMessage": "Not found"}, limit_headers)

        size = int(params.get("size", 50))
        offset = int(params.get("offset", 0))
        if size > 1000:
            return FakeResponse(400, {"errorMessage": "size must be <= 1000"}, limit_headers)
        body = self.data[path]
        if isinstance(body, dict):
            return FakeResponse(200, body, limit_headers)

        items = body
        total = len(items)
        flt = str(params.get("filter", ""))
        if path == "/alert/alerts" and "startEpoch>" in flt:
            total = self.data["alerts_24h_total"]
            items = [dict(open_alert, id=f"H{i}") for i, open_alert in
                     enumerate((self.data["/alert/alerts"] * 5)[:total])]
        elif path == "/setting/accesslogs" and "happenedOn>" in flt:
            since = int(re.search(r"happenedOn>(\d+)", flt).group(1))
            items = [e for e in items if e["happenedOn"] > since]
            total = len(items)
        page = items[offset:offset + size]
        return FakeResponse(200, {"total": total, "items": page, "searchId": None}, limit_headers)

    def _authorised(self, path: str, headers: Dict[str, str]) -> bool:
        auth = headers.get("Authorization", "")
        match = re.match(r"^LMv1 ([^:]+):([^:]+):(\d+)$", auth)
        if not match or match.group(1) != self.access_id:
            return False
        expected = sign(self.access_key, "GET", match.group(3), "", path)
        return expected == match.group(2)

    def __getattr__(self, name: str) -> Any:
        if name in ("post", "put", "patch", "delete", "request"):
            raise AssertionError(f"read-only tool attempted {name.upper()}")
        raise AttributeError(name)
