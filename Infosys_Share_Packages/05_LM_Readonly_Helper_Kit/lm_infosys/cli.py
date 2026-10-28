from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from lm_infosys.client import ApiError, ConfigError, LmClient, load_dotenv


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


def cmd_health(_: argparse.Namespace) -> int:
    client = LmClient()
    devices = client.get("/device/devices", {"size": "1", "fields": "id,name"})
    alerts = client.get("/alert/alerts", {"size": "1", "fields": "id,type"})
    collectors = client.get(
        "/setting/collector/collectors",
        {"size": "1", "fields": "id,hostname,status"},
    )
    print("LogicMonitor API: OK")
    print(f"  portal     {client.config.account}.{client.config.domain}")
    print(f"  devices    {_total(devices) if _total(devices) is not None else 'reachable'}")
    print(f"  alerts     {_total(alerts) if _total(alerts) is not None else 'reachable'}")
    print(
        f"  collectors {_total(collectors) if _total(collectors) is not None else 'reachable'}"
    )
    return 0


def cmd_alerts(args: argparse.Namespace) -> int:
    query = {
        "size": str(args.size),
        "sort": "-startEpoch",
        "fields": "id,type,severity,cleared,resourceId,resourceTemplateName,instanceName,startEpoch,message",
    }
    if args.severity:
        query["filter"] = f"severity:{args.severity}"
    payload = LmClient().get("/alert/alerts", query)
    rows = _items(payload)
    if args.json:
        json.dump(payload, sys.stdout, indent=2)
        print()
        return 0
    total = _total(payload)
    print(f"Active alerts: {len(rows)}" + (f" (total {total})" if total is not None else ""))
    if not rows:
        print("No matching alerts.")
        return 0
    for row in rows:
        print(
            "  [{severity}] {id}  {resource}  {instance}  {message}".format(
                severity=row.get("severity", "?"),
                id=row.get("id", "?"),
                resource=row.get("resourceTemplateName") or row.get("resourceId") or "-",
                instance=row.get("instanceName") or "-",
                message=(row.get("message") or "").replace("\n", " ")[:160],
            )
        )
    return 0


def cmd_devices(args: argparse.Namespace) -> int:
    query = {
        "size": str(args.size),
        "fields": "id,name,displayName,hostStatus,preferredCollectorId",
    }
    if args.query:
        query["filter"] = f"name~{args.query}|displayName~{args.query}"
    payload = LmClient().get("/device/devices", query)
    rows = _items(payload)
    if args.json:
        json.dump(payload, sys.stdout, indent=2)
        print()
        return 0
    total = _total(payload)
    print(f"Devices: {len(rows)}" + (f" (total {total})" if total is not None else ""))
    if not rows:
        print("No matching devices.")
        return 0
    for row in rows:
        print(
            "  {id:>6}  {status:<8}  {name}  ({display})".format(
                id=row.get("id", "?"),
                status=row.get("hostStatus") or "-",
                name=row.get("name") or "-",
                display=row.get("displayName") or "-",
            )
        )
    return 0


def cmd_collectors(args: argparse.Namespace) -> int:
    query = {
        "size": str(args.size),
        "fields": "id,hostname,description,status,platform,collectorGroupName,onetimeDowntimeStart",
    }
    payload = LmClient().get("/setting/collector/collectors", query)
    rows = _items(payload)
    if args.json:
        json.dump(payload, sys.stdout, indent=2)
        print()
        return 0
    total = _total(payload)
    print(f"Collectors: {len(rows)}" + (f" (total {total})" if total is not None else ""))
    if not rows:
        print("No collectors returned.")
        return 0
    for row in rows:
        print(
            "  {id:>6}  {status:<10}  {host}  {group}  {desc}".format(
                id=row.get("id", "?"),
                status=row.get("status") or "-",
                host=row.get("hostname") or "-",
                group=row.get("collectorGroupName") or "-",
                desc=(row.get("description") or "")[:80],
            )
        )
    return 0


def cmd_device(args: argparse.Namespace) -> int:
    client = LmClient()
    target = args.identifier.strip()
    payload: Any
    if target.isdigit():
        payload = client.get(f"/device/devices/{target}")
        device = payload.get("data", payload) if isinstance(payload, dict) else payload
    else:
        listing = client.get(
            "/device/devices",
            {
                "size": "10",
                "filter": f"name~{target}|displayName~{target}",
                "fields": "id,name,displayName,hostStatus,preferredCollectorId,deviceType",
            },
        )
        rows = _items(listing)
        if not rows:
            print(f"No device matched {target!r}.")
            return 1
        if len(rows) > 1 and not args.json:
            print(f"Multiple matches for {target!r}. Showing the first {len(rows)}:")
            for row in rows:
                print(
                    f"  {row.get('id')}  {row.get('name')}  ({row.get('displayName')})"
                )
        device = rows[0]
        payload = listing
    if args.json:
        json.dump(payload, sys.stdout, indent=2)
        print()
        return 0
    if not isinstance(device, dict):
        print("Unexpected device payload.")
        return 1
    print(f"Device {device.get('id')}")
    print(f"  name        {device.get('name')}")
    print(f"  display     {device.get('displayName')}")
    print(f"  status      {device.get('hostStatus')}")
    print(f"  collector   {device.get('preferredCollectorId')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m lm_infosys",
        description="Read-only LogicMonitor helpers for Infosys Claude sessions.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health", help="Verify API token and portal reachability").set_defaults(
        func=cmd_health
    )

    alerts = sub.add_parser("alerts", help="List current alerts")
    alerts.add_argument("--size", type=int, default=20)
    alerts.add_argument("--severity", choices=("warn", "error", "critical"))
    alerts.add_argument("--json", action="store_true")
    alerts.set_defaults(func=cmd_alerts)

    devices = sub.add_parser("devices", help="Search or list devices")
    devices.add_argument("--query", "-q", help="Name or displayName substring")
    devices.add_argument("--size", type=int, default=25)
    devices.add_argument("--json", action="store_true")
    devices.set_defaults(func=cmd_devices)

    collectors = sub.add_parser("collectors", help="List collectors")
    collectors.add_argument("--size", type=int, default=25)
    collectors.add_argument("--json", action="store_true")
    collectors.set_defaults(func=cmd_collectors)

    device = sub.add_parser("device", help="Show one device by id or name")
    device.add_argument("identifier")
    device.add_argument("--json", action="store_true")
    device.set_defaults(func=cmd_device)

    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2
    except ApiError as exc:
        print(f"API error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
