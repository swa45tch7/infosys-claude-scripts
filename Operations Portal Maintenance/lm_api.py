#!/usr/bin/env python3
"""Read-only LogicMonitor REST API v3 client (LMv1 signing).

Stdlib only. Query parameters are not part of the signature, per LogicMonitor
REST API authentication. This module never issues POST, PUT, PATCH, or DELETE.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

API_VERSION = "3"
ENV_KEYS = ("LM_ACCOUNT", "LM_ACCESS_ID", "LM_ACCESS_KEY")
PAGE_SIZE = 1000
REQUEST_TIMEOUT = 45
MAX_RETRIES = 4


class ConfigError(RuntimeError):
    """Missing or invalid LogicMonitor configuration."""


class ApiError(RuntimeError):
    """LogicMonitor API request failed."""

    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.body = body
        super().__init__(f"LogicMonitor API HTTP {status}: {body}")


def load_dotenv(path: Path | None = None) -> None:
    """Load KEY=VALUE pairs from .env if present. Existing env vars win."""
    candidates = []
    if path:
        candidates.append(path)
    else:
        cwd = Path.cwd()
        candidates.append(cwd / ".env")
        candidates.append(cwd.parent / ".env")
        candidates.append(Path(__file__).resolve().parent.parent / ".env")
    seen: set[Path] = set()
    for env_path in candidates:
        env_path = env_path.resolve()
        if env_path in seen or not env_path.is_file():
            continue
        seen.add(env_path)
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class LmConfig:
    account: str
    access_id: str
    access_key: str
    domain: str = "logicmonitor.com"

    @property
    def base_url(self) -> str:
        return f"https://{self.account}.{self.domain}/santaba/rest"

    @classmethod
    def from_env(cls) -> LmConfig:
        missing = [key for key in ENV_KEYS if not os.environ.get(key, "").strip()]
        if missing:
            raise ConfigError(
                "Missing "
                + ", ".join(missing)
                + ". Set them in the environment or a local .env file "
                "(see the repository .env.example). Do not paste API keys into chat."
            )
        return cls(
            account=os.environ["LM_ACCOUNT"].strip(),
            access_id=os.environ["LM_ACCESS_ID"].strip(),
            access_key=os.environ["LM_ACCESS_KEY"].strip(),
            domain=os.environ.get("LM_DOMAIN", "logicmonitor.com").strip()
            or "logicmonitor.com",
        )


def _epoch_ms() -> str:
    return str(int(time.time() * 1000))


def sign(access_key: str, verb: str, epoch: str, data: str, resource_path: str) -> str:
    message = f"{verb}{epoch}{data}{resource_path}"
    digest = hmac.new(
        access_key.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return base64.b64encode(digest.encode("utf-8")).decode("utf-8")


class LmClient:
    def __init__(self, config: LmConfig | None = None) -> None:
        self.config = config or LmConfig.from_env()

    def get(self, resource_path: str, query: dict[str, str] | None = None) -> Any:
        return self._request("GET", resource_path, query=query)

    def collect(
        self,
        resource_path: str,
        extra_query: dict[str, str] | None = None,
        max_items: int | None = None,
    ) -> list[dict[str, Any]]:
        """Page through a list endpoint and return items."""
        items: list[dict[str, Any]] = []
        offset = 0
        while True:
            query = {"size": str(PAGE_SIZE), "offset": str(offset)}
            if extra_query:
                query.update(extra_query)
            payload = self.get(resource_path, query)
            batch = _items(payload)
            items.extend(batch)
            if max_items is not None and len(items) >= max_items:
                return items[:max_items]
            total = _total(payload)
            if len(batch) < PAGE_SIZE:
                break
            if total is not None and len(items) >= total:
                break
            offset += PAGE_SIZE
        return items

    def _request(
        self,
        verb: str,
        resource_path: str,
        query: dict[str, str] | None = None,
        data: str = "",
    ) -> Any:
        if verb != "GET":
            raise ApiError(0, f"This operations kit is read-only; refused {verb}")
        if not resource_path.startswith("/"):
            resource_path = "/" + resource_path
        last_error: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            epoch = _epoch_ms()
            signature = sign(self.config.access_key, verb, epoch, data, resource_path)
            auth = f"LMv1 {self.config.access_id}:{signature}:{epoch}"
            url = self.config.base_url + resource_path
            if query:
                url += "?" + urllib.parse.urlencode(query)
            request = urllib.request.Request(
                url,
                method=verb,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": auth,
                    "X-Version": API_VERSION,
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                    raw = response.read().decode("utf-8")
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                if exc.code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES:
                    time.sleep(2**attempt)
                    last_error = ApiError(exc.code, body)
                    continue
                raise ApiError(exc.code, body) from exc
            except urllib.error.URLError as exc:
                if attempt < MAX_RETRIES:
                    time.sleep(2**attempt)
                    last_error = ApiError(0, str(exc.reason))
                    continue
                raise ApiError(0, str(exc.reason)) from exc
            if not raw:
                return {}
            try:
                return json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ApiError(0, f"Non-JSON response: {raw[:500]}") from exc
        raise last_error or ApiError(0, "Request failed")


def _items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        data = payload.get("data", payload)
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data["items"]
        if isinstance(data, list):
            return data
    return []


def _total(payload: Any) -> int | None:
    if isinstance(payload, dict):
        data = payload.get("data", payload)
        if isinstance(data, dict) and "total" in data:
            try:
                return int(data["total"])
            except (TypeError, ValueError):
                return None
    return None
