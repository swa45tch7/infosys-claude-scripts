"""Read-only LogicMonitor REST API v3 client.

* LMv1 HMAC-SHA256 signing (query string is not part of the signature).
* GET only. Any other verb is refused before a request is built.
* Paces itself with the X-Rate-Limit-Limit / -Remaining / -Window headers that
  LogicMonitor returns on every response, backs off on HTTP 429 using
  Retry-After or the rate-limit window, and retries transient 5xx and network
  errors with capped exponential backoff plus jitter.
* Requests are strictly sequential; there is no parallelism.
* Secrets are never logged. The access key only ever feeds the HMAC.
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import logging
import os
import random
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests

log = logging.getLogger("lm_health")

API_VERSION = "3"
MAX_PAGE_SIZE = 1000
REQUEST_TIMEOUT = 60
MAX_RETRIES = 6
NETWORK_RETRIES = 3
BACKOFF_BASE = 2.0
BACKOFF_CAP = 60.0
RETRYABLE_STATUS = (500, 502, 503, 504)
# Below this share of the window's quota, requests are spread across the window.
PACE_FROM_FRACTION = 0.5
ENV_KEYS = ("LM_ACCOUNT", "LM_ACCESS_ID", "LM_ACCESS_KEY")
USER_AGENT = "infosys-lm-health-check/1.0"


class ConfigError(RuntimeError):
    """Credentials are missing or invalid."""


class ApiError(RuntimeError):
    """A LogicMonitor API request failed."""

    def __init__(self, status: int, path: str, message: str) -> None:
        self.status = status
        self.path = path
        super().__init__(f"HTTP {status} on GET {path}: {message}")


def load_dotenv(folder: Optional[Path] = None) -> None:
    """Load KEY=VALUE pairs from .env files. Variables already set win."""
    here = Path(__file__).resolve().parent
    candidates = [Path.cwd() / ".env", here.parent / ".env", here / ".env"]
    if folder:
        candidates.insert(0, folder / ".env")
    seen = set()
    for path in candidates:
        path = path.resolve()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.lower().startswith("export "):
                line = line[7:]
            key, value = line.split("=", 1)
            value = value.strip().strip('"').strip("'")
            if value:
                os.environ.setdefault(key.strip(), value)


@dataclass(frozen=True)
class LmConfig:
    account: str
    access_id: str
    access_key: str = field(repr=False)
    domain: str = "logicmonitor.com"

    @property
    def base_url(self) -> str:
        return f"https://{self.account}.{self.domain}/santaba/rest"

    @classmethod
    def from_env(cls, interactive: bool = True) -> "LmConfig":
        values = {key: os.environ.get(key, "").strip() for key in ENV_KEYS}
        missing = [key for key, value in values.items() if not value]
        if missing and interactive and sys.stdin is not None and sys.stdin.isatty():
            print("LogicMonitor credentials not found in the environment or .env.")
            print("Enter them below (the access key is hidden while you type).")
            for key in missing:
                if key == "LM_ACCESS_KEY":
                    values[key] = getpass.getpass(f"{key}: ").strip()
                else:
                    values[key] = input(f"{key}: ").strip()
            missing = [key for key, value in values.items() if not value]
        if missing:
            raise ConfigError(
                "Missing " + ", ".join(missing) + ". Set them as environment "
                "variables or in a .env file next to the script (see .env.example)."
            )
        account = values["LM_ACCOUNT"]
        for prefix in ("https://", "http://"):
            if account.lower().startswith(prefix):
                account = account[len(prefix):]
        account = account.split(".")[0].split("/")[0]
        domain = os.environ.get("LM_DOMAIN", "").strip() or "logicmonitor.com"
        return cls(account, values["LM_ACCESS_ID"], values["LM_ACCESS_KEY"], domain)


def sign(access_key: str, verb: str, epoch_ms: str, body: str, resource_path: str) -> str:
    message = f"{verb}{epoch_ms}{body}{resource_path}"
    digest = hmac.new(access_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256)
    return base64.b64encode(digest.hexdigest().encode("utf-8")).decode("utf-8")


def _header_number(headers: Any, name: str) -> Optional[float]:
    value = headers.get(name) if headers is not None else None
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


class RateLimiter:
    """Tracks LogicMonitor's per-endpoint rate-limit headers and paces calls."""

    def __init__(self, sleep: Callable[[float], None], clock: Callable[[], float]) -> None:
        self._sleep = sleep
        self._clock = clock
        self._state: Dict[str, Dict[str, float]] = {}
        self.total_wait = 0.0
        self.throttle_events = 0

    @staticmethod
    def bucket(path: str) -> str:
        # LogicMonitor applies limits per API family, e.g. /device/devices.
        parts = [p for p in path.split("/") if p]
        return "/" + "/".join(parts[:2])

    def before_request(self, path: str) -> None:
        state = self._state.get(self.bucket(path))
        if not state:
            return
        wait = state.get("not_before", 0.0) - self._clock()
        if wait > 0:
            self._wait(wait, f"pacing {self.bucket(path)}")

    def after_response(self, path: str, headers: Any) -> None:
        limit = _header_number(headers, "X-Rate-Limit-Limit")
        remaining = _header_number(headers, "X-Rate-Limit-Remaining")
        window = _header_number(headers, "X-Rate-Limit-Window")
        if limit is None or remaining is None or not window:
            return
        now = self._clock()
        if remaining <= 1:
            delay = window
        elif remaining < limit * PACE_FROM_FRACTION:
            delay = window / remaining
        else:
            delay = 0.0
        self._state[self.bucket(path)] = {"not_before": now + delay}

    def wait_after_429(self, path: str, headers: Any, attempt: int) -> float:
        hinted = _header_number(headers, "Retry-After")
        if hinted is None:
            hinted = _header_number(headers, "X-Rate-Limit-Window")
        backoff = min(BACKOFF_CAP, BACKOFF_BASE ** attempt)
        delay = min(BACKOFF_CAP, max(hinted or 0.0, backoff)) + random.uniform(0, 1.0)
        self.throttle_events += 1
        self._wait(delay, f"HTTP 429 on {self.bucket(path)}")
        return delay

    def wait_backoff(self, reason: str, attempt: int) -> float:
        delay = min(BACKOFF_CAP, BACKOFF_BASE ** attempt) + random.uniform(0, 1.0)
        self._wait(delay, reason)
        return delay

    def _wait(self, seconds: float, reason: str) -> None:
        log.info("Waiting %.1fs (%s)", seconds, reason)
        self.total_wait += seconds
        self._sleep(seconds)


class LmClient:
    def __init__(
        self,
        config: LmConfig,
        session: Optional[requests.Session] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.session = session or requests.Session()
        self.limiter = RateLimiter(sleep, clock)
        self.request_count = 0
        self.endpoints_used: List[str] = []

    @property
    def portal_host(self) -> str:
        return f"{self.config.account}.{self.config.domain}"

    def get(self, resource_path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        return self._request("GET", resource_path, params)

    def total(self, resource_path: str, params: Optional[Dict[str, Any]] = None) -> Optional[int]:
        """Return the list endpoint's reported total using a one-item page."""
        query = dict(params or {})
        query.update({"size": 1, "offset": 0})
        query.setdefault("fields", "id")
        total = _total(self.get(resource_path, query))
        return total if total is not None and total >= 0 else None

    def collect(
        self,
        resource_path: str,
        params: Optional[Dict[str, Any]] = None,
        max_items: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Page through a list endpoint at the maximum page size."""
        items: List[Dict[str, Any]] = []
        offset = 0
        while True:
            size = MAX_PAGE_SIZE
            if max_items is not None:
                size = max(1, min(MAX_PAGE_SIZE, max_items - len(items)))
            query = dict(params or {})
            query.update({"size": size, "offset": offset})
            payload = self.get(resource_path, query)
            batch = _items(payload)
            items.extend(batch)
            if max_items is not None and len(items) >= max_items:
                return items[:max_items]
            total = _total(payload)
            if len(batch) < size or not batch:
                break
            if total is not None and total >= 0 and len(items) >= total:
                break
            offset += len(batch)
        return items

    def _request(self, verb: str, resource_path: str, params: Optional[Dict[str, Any]]) -> Any:
        if verb != "GET":
            raise ApiError(0, resource_path, f"refused {verb}: this tool is read-only")
        if not resource_path.startswith("/"):
            resource_path = "/" + resource_path
        if resource_path not in self.endpoints_used:
            self.endpoints_used.append(resource_path)
        url = self.config.base_url + resource_path
        attempt = 0
        while True:
            attempt += 1
            self.limiter.before_request(resource_path)
            epoch = str(int(time.time() * 1000))
            signature = sign(self.config.access_key, verb, epoch, "", resource_path)
            headers = {
                "Authorization": f"LMv1 {self.config.access_id}:{signature}:{epoch}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-Version": API_VERSION,
                "User-Agent": USER_AGENT,
            }
            try:
                self.request_count += 1
                response = self.session.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt >= NETWORK_RETRIES:
                    raise ApiError(0, resource_path, f"network error: {type(exc).__name__}") from None
                self.limiter.wait_backoff(f"network error on {resource_path}", attempt)
                continue

            status = response.status_code
            if status != 429:
                self.limiter.after_response(resource_path, response.headers)
            if status == 429:
                if attempt >= MAX_RETRIES:
                    raise ApiError(429, resource_path, "rate limit still exceeded after retries")
                self.limiter.wait_after_429(resource_path, response.headers, attempt)
                continue
            if status in RETRYABLE_STATUS:
                if attempt >= MAX_RETRIES:
                    raise ApiError(status, resource_path, "server error persisted after retries")
                self.limiter.wait_backoff(f"HTTP {status} on {resource_path}", attempt)
                continue
            if status >= 400:
                raise ApiError(status, resource_path, _error_message(response))
            if not response.content:
                return {}
            try:
                payload = response.json()
            except ValueError:
                raise ApiError(status, resource_path, "response was not JSON") from None
            # API v1/v2 style bodies carry an application-level status code.
            if isinstance(payload, dict) and isinstance(payload.get("status"), int) and "data" in payload:
                inner = payload["status"]
                if inner not in (200, 0) and payload.get("data") is None:
                    raise ApiError(inner, resource_path, str(payload.get("errmsg", "error")))
            return payload


def _error_message(response: Any) -> str:
    try:
        body = response.json()
    except ValueError:
        return (response.text or "")[:200]
    if isinstance(body, dict):
        for key in ("errorMessage", "errmsg", "message"):
            if body.get(key):
                return str(body[key])[:200]
    return str(body)[:200]


def _unwrap(payload: Any) -> Any:
    if isinstance(payload, dict) and "data" in payload and isinstance(payload["data"], (dict, list)):
        return payload["data"]
    return payload


def _items(payload: Any) -> List[Dict[str, Any]]:
    data = _unwrap(payload)
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return data["items"]
    if isinstance(data, list):
        return data
    return []


def _total(payload: Any) -> Optional[int]:
    data = _unwrap(payload)
    if isinstance(data, dict) and "total" in data:
        try:
            return int(data["total"])
        except (TypeError, ValueError):
            return None
    return None


def unwrap(payload: Any) -> Any:
    return _unwrap(payload)
