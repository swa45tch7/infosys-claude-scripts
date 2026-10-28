"""
LogicMonitor REST API v3 client.

Authentication follows the LMv1 scheme documented by LogicMonitor:
    signature = base64( hex( HMAC-SHA256(AccessKey, VERB + epoch_ms + body + resourcePath) ) )
    Authorization: LMv1 <AccessId>:<signature>:<epoch_ms>

Design notes (kept deliberately light on laptop resources):
  * One shared async HTTP client, capped connection pool.
  * Server-side field selection via `fields=` so payloads stay small.
  * Results are streamed page by page; nothing is cached to disk.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

API_VERSION = "3"
PAGE_SIZE = 1000
MAX_RETRIES = 4
CONCURRENCY = 5


class LMAuthError(Exception):
    """Credentials were rejected by the portal."""


class LMApiError(Exception):
    """The portal returned an error we cannot recover from."""


@dataclass(frozen=True)
class Credentials:
    company: str
    access_id: str
    access_key: str

    @property
    def base_url(self) -> str:
        company = self.company.strip().lower()
        if "." in company:  # user pasted a full hostname
            return f"https://{company}/santaba/rest"
        return f"https://{company}.logicmonitor.com/santaba/rest"

    def redacted(self) -> Dict[str, str]:
        return {
            "company": self.company,
            "access_id": self.access_id[:4] + "…" if self.access_id else "",
        }


def sign(creds: Credentials, verb: str, resource_path: str, body: str = "") -> Dict[str, str]:
    """Build the LMv1 Authorization header for a single request."""
    epoch = str(int(time.time() * 1000))
    message = f"{verb.upper()}{epoch}{body}{resource_path}"
    digest = hmac.new(
        creds.access_key.encode("utf-8"),
        msg=message.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()
    signature = base64.b64encode(digest.encode("utf-8")).decode("utf-8")
    return {
        "Authorization": f"LMv1 {creds.access_id}:{signature}:{epoch}",
        "Content-Type": "application/json",
        "X-Version": API_VERSION,
        "Accept": "application/json",
    }


class LMClient:
    """Thin async wrapper around the v3 REST API."""

    def __init__(self, creds: Credentials, timeout: float = 60.0):
        self.creds = creds
        self._sem = asyncio.Semaphore(CONCURRENCY)
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=15.0),
            limits=httpx.Limits(max_connections=CONCURRENCY, max_keepalive_connections=CONCURRENCY),
            headers={"User-Agent": "LM-Ops-Console/1.0"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "LMClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    # ------------------------------------------------------------------ core

    async def request(
        self,
        verb: str,
        resource_path: str,
        params: Optional[Dict[str, Any]] = None,
        body: str = "",
    ) -> Dict[str, Any]:
        url = self.creds.base_url + resource_path
        attempt = 0
        while True:
            attempt += 1
            headers = sign(self.creds, verb, resource_path, body)
            try:
                async with self._sem:
                    resp = await self._client.request(
                        verb.upper(), url, params=params, headers=headers, content=body or None
                    )
            except httpx.HTTPError as exc:
                if attempt >= MAX_RETRIES:
                    raise LMApiError(f"Could not reach {self.creds.company}: {exc}") from exc
                await asyncio.sleep(min(2 ** attempt, 10))
                continue

            if resp.status_code in (401, 403):
                raise LMAuthError(
                    "The portal rejected these credentials. Check the company name, "
                    "Access ID and Access Key, and confirm the API token is enabled "
                    "with the required role permissions."
                )
            if resp.status_code == 429:
                wait = float(resp.headers.get("X-Rate-Limit-Window", "10"))
                if attempt >= MAX_RETRIES:
                    raise LMApiError("Rate limit reached. Reduce the selected checks and retry.")
                await asyncio.sleep(wait)
                continue
            if resp.status_code >= 500:
                if attempt >= MAX_RETRIES:
                    raise LMApiError(f"Portal returned {resp.status_code} for {resource_path}.")
                await asyncio.sleep(min(2 ** attempt, 10))
                continue
            if resp.status_code >= 400:
                raise LMApiError(f"{resp.status_code} on {resource_path}: {resp.text[:300]}")

            payload = resp.json()
            if isinstance(payload, dict) and payload.get("errmsg") not in (None, "OK"):
                raise LMApiError(str(payload.get("errmsg")))
            return payload

    async def get(self, resource_path: str, **params: Any) -> Dict[str, Any]:
        clean = {k: v for k, v in params.items() if v not in (None, "")}
        return await self.request("GET", resource_path, params=clean)

    async def paged(
        self,
        resource_path: str,
        fields: Optional[str] = None,
        filter_: Optional[str] = None,
        size: int = PAGE_SIZE,
        max_items: Optional[int] = None,
        **extra: Any,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Yield items one page at a time so memory stays flat on large estates."""
        offset = 0
        seen = 0
        while True:
            payload = await self.get(
                resource_path, size=size, offset=offset, fields=fields, filter=filter_, **extra
            )
            data = payload.get("data") or payload
            items = data.get("items") or []
            for item in items:
                yield item
                seen += 1
                if max_items and seen >= max_items:
                    return
            if len(items) < size:
                return
            offset += size

    async def collect(self, resource_path: str, **kwargs: Any) -> List[Dict[str, Any]]:
        return [item async for item in self.paged(resource_path, **kwargs)]

    # --------------------------------------------------------------- helpers

    async def verify(self) -> Dict[str, Any]:
        """Cheap call used by the Connect button to validate credentials."""
        payload = await self.get("/device/devices", size=1, fields="id")
        data = payload.get("data") or {}
        total = data.get("total")
        return {
            "company": self.creds.company,
            "device_total": total if isinstance(total, int) and total >= 0 else None,
        }
