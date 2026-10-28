"""
Local application server.

The GUI is a single page served from this process and reached at
http://127.0.0.1:8787. Nothing is exposed to the network: the socket binds to
the loopback address only.

Credentials live in this process for the life of the session. If you tick
"Remember on this laptop", the Access Key goes to the operating system keychain
through the `keyring` library — never to a file in this folder.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import exporters
from .checks import BY_ID, REGISTRY, CONFIG, Inventory, run_check
from .lm_client import Credentials, LMApiError, LMAuthError, LMClient

APP_NAME = "LogicMonitor Ops Console"
KEYRING_SERVICE = "lm-ops-console"
SESSION_TTL_SECONDS = 8 * 3600
RUN_TTL_SECONDS = 2 * 3600

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title=APP_NAME, docs_url=None, redoc_url=None)

SESSIONS: Dict[str, Dict[str, Any]] = {}
RUNS: Dict[str, Dict[str, Any]] = {}


# ----------------------------------------------------------------- models

class ConnectRequest(BaseModel):
    company: str = Field(min_length=1)
    access_id: str = Field(min_length=1)
    access_key: str = Field(min_length=1)
    remember: bool = False


class RunRequest(BaseModel):
    session_id: str
    checks: List[str]


# ----------------------------------------------------------------- helpers

def _now() -> str:
    return datetime.now().strftime("%d %b %Y, %H:%M")


def _prune() -> None:
    cutoff = time.time() - SESSION_TTL_SECONDS
    for sid in [s for s, v in SESSIONS.items() if v["created"] < cutoff]:
        stale = SESSIONS.pop(sid, None)
        if stale:
            asyncio.create_task(stale["client"].close())
    cutoff = time.time() - RUN_TTL_SECONDS
    for rid in [r for r, v in RUNS.items() if v["created"] < cutoff]:
        RUNS.pop(rid, None)


def _session(session_id: str) -> Dict[str, Any]:
    _prune()
    session = SESSIONS.get(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="This session has expired. Connect again.")
    return session


def _keyring():
    try:
        import keyring

        return keyring
    except Exception:
        return None


# ------------------------------------------------------------------ routes

@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/checks")
async def list_checks() -> Dict[str, Any]:
    groups: Dict[str, List[Dict[str, str]]] = {}
    for spec in REGISTRY:
        groups.setdefault(spec.group, []).append(
            {"id": spec.id, "name": spec.name, "description": spec.description}
        )
    return {
        "groups": groups,
        "formats": exporters.FORMATS,
        "config": {
            "alert_window_hours": CONFIG["alert_window_hours"],
            "stale_discovery_days": CONFIG["stale_discovery_days"],
            "required_properties": CONFIG["required_properties"],
            "probe_integrations": CONFIG["probe_integrations"],
        },
    }


@app.get("/api/saved")
async def saved_credentials(company: str = "", access_id: str = "") -> Dict[str, Any]:
    kr = _keyring()
    if not kr or not company or not access_id:
        return {"available": False}
    try:
        secret = kr.get_password(KEYRING_SERVICE, f"{company.lower()}:{access_id}")
        return {"available": bool(secret)}
    except Exception:
        return {"available": False}


@app.post("/api/connect")
async def connect(req: ConnectRequest) -> Dict[str, Any]:
    access_key = req.access_key
    kr = _keyring()
    if access_key == "__saved__" and kr:
        access_key = kr.get_password(
            KEYRING_SERVICE, f"{req.company.lower()}:{req.access_id}"
        ) or ""
        if not access_key:
            raise HTTPException(status_code=400,
                                detail="No saved key found for this Access ID.")

    creds = Credentials(company=req.company.strip(), access_id=req.access_id.strip(),
                        access_key=access_key.strip())
    client = LMClient(creds)
    try:
        info = await client.verify()
    except LMAuthError as exc:
        await client.close()
        raise HTTPException(status_code=401, detail=str(exc))
    except LMApiError as exc:
        await client.close()
        raise HTTPException(status_code=502, detail=str(exc))

    if req.remember and kr:
        try:
            kr.set_password(KEYRING_SERVICE, f"{creds.company.lower()}:{creds.access_id}",
                            creds.access_key)
        except Exception:
            pass

    session_id = secrets.token_urlsafe(24)
    SESSIONS[session_id] = {"client": client, "creds": creds, "created": time.time()}
    return {
        "session_id": session_id,
        "company": creds.company,
        "device_total": info.get("device_total"),
        "keyring_available": bool(kr),
    }


@app.post("/api/disconnect")
async def disconnect(payload: Dict[str, str]) -> Dict[str, bool]:
    session = SESSIONS.pop(payload.get("session_id", ""), None)
    if session:
        await session["client"].close()
    return {"ok": True}


@app.post("/api/run")
async def start_run(req: RunRequest) -> Dict[str, str]:
    session = _session(req.session_id)
    selected = [BY_ID[c] for c in req.checks if c in BY_ID]
    if not selected:
        raise HTTPException(status_code=400, detail="Select at least one check.")

    run_id = secrets.token_urlsafe(16)
    RUNS[run_id] = {
        "run_id": run_id,
        "company": session["creds"].company,
        "started_at": _now(),
        "finished_at": "",
        "status": "running",
        "checks": [],
        "queue": asyncio.Queue(),
        "created": time.time(),
    }
    asyncio.create_task(_execute(run_id, session["client"], selected))
    return {"run_id": run_id}


async def _execute(run_id: str, client: LMClient, specs) -> None:
    run = RUNS[run_id]
    queue: asyncio.Queue = run["queue"]
    inventory = Inventory(client)

    async def emit(event: str, data: Dict[str, Any]) -> None:
        await queue.put({"event": event, "data": data})

    await emit("run_started", {"total": len(specs), "started_at": run["started_at"]})
    for index, spec in enumerate(specs, start=1):
        await emit("check_started",
                   {"id": spec.id, "name": spec.name, "index": index, "total": len(specs)})
        started = time.perf_counter()
        result = await run_check(spec, inventory)
        elapsed = round(time.perf_counter() - started, 1)
        payload = {
            "id": spec.id,
            "name": spec.name,
            "group": spec.group,
            "columns": result.columns,
            "rows": result.rows,
            "counts": result.counts(),
            "summary": result.summary,
            "notes": result.notes,
            "seconds": elapsed,
        }
        run["checks"].append(payload)
        await emit("check_finished", payload)

    run["status"] = "complete"
    run["finished_at"] = _now()
    await emit("run_finished", {
        "finished_at": run["finished_at"],
        "checks": len(run["checks"]),
        "totals": _totals(run),
    })
    await queue.put(None)


def _totals(run: Dict[str, Any]) -> Dict[str, int]:
    totals = {"critical": 0, "warn": 0, "ok": 0, "info": 0, "findings": 0}
    for check in run["checks"]:
        for key, value in check["counts"].items():
            totals[key] = totals.get(key, 0) + value
        totals["findings"] += len(check["rows"])
    return totals


@app.get("/api/run/{run_id}/events")
async def run_events(run_id: str) -> StreamingResponse:
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Unknown run.")
    queue: asyncio.Queue = run["queue"]

    async def stream():
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=20)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
                continue
            if item is None:
                yield "event: stream_end\ndata: {}\n\n"
                return
            yield f"event: {item['event']}\ndata: {json.dumps(item['data'], default=str)}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/run/{run_id}")
async def get_run(run_id: str) -> Dict[str, Any]:
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Unknown run.")
    return {k: v for k, v in run.items() if k not in ("queue", "created")}


@app.get("/api/run/{run_id}/export")
async def export_run(run_id: str, fmt: str = "xlsx") -> Response:
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Unknown run.")
    snapshot = {
        "company": run["company"],
        "started_at": run["started_at"],
        "finished_at": run["finished_at"] or _now(),
        "checks": run["checks"],
        "totals": _totals(run),
    }
    try:
        payload, name, media = exporters.export(snapshot, fmt)
    except ImportError as exc:
        raise HTTPException(
            status_code=501,
            detail=f"This format needs an extra library: {exc}. "
                   f"Install it with: pip install reportlab",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return Response(
        content=payload,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.post("/api/shutdown")
async def shutdown() -> Dict[str, bool]:
    for session in list(SESSIONS.values()):
        await session["client"].close()
    SESSIONS.clear()
    RUNS.clear()
    threading.Timer(0.5, lambda: os._exit(0)).start()
    return {"ok": True}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def main(host: str = "127.0.0.1", port: int = 8787, open_browser: bool = True) -> None:
    if open_browser:
        opener = threading.Timer(1.2, lambda: webbrowser.open(f"http://{host}:{port}"))
        opener.daemon = True
        opener.start()
    print(f"{APP_NAME} is running at http://{host}:{port}")
    print("Close this window to stop the application.")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
