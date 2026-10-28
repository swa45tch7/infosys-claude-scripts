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
    env_path = path or Path.cwd() / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


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
                + "(see .env.example). Do not paste API keys into chat."
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
        return self._request("GET", resource_path, query=query, data="")

    def _request(
        self,
        verb: str,
        resource_path: str,
        query: dict[str, str] | None = None,
        data: str = "",
    ) -> Any:
        if not resource_path.startswith("/"):
            resource_path = "/" + resource_path
        epoch = _epoch_ms()
        signature = sign(self.config.access_key, verb, epoch, data, resource_path)
        auth = f"LMv1 {self.config.access_id}:{signature}:{epoch}"
        url = self.config.base_url + resource_path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(
            url,
            data=data.encode("utf-8") if data else None,
            method=verb,
            headers={
                "Content-Type": "application/json",
                "Authorization": auth,
                "X-Version": API_VERSION,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ApiError(exc.code, body) from exc
        except urllib.error.URLError as exc:
            raise ApiError(0, str(exc.reason)) from exc
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ApiError(0, f"Non-JSON response: {raw[:500]}") from exc
