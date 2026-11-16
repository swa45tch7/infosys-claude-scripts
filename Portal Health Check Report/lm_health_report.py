#!/usr/bin/env python3
"""Generate a LogicMonitor (LM Envision) portal health check report as a Word document.

Read-only: only HTTP GET requests are made. Usage:

    python lm_health_report.py
    python lm_health_report.py --customer "Contoso" --contract-devices 1500
    python lm_health_report.py --demo        (fictional portal, no credentials needed)
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import List, Optional

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

if sys.version_info < (3, 9):
    sys.exit("Python 3.9 or newer is required. See README.md, step 1.")

try:
    import docx  # noqa: F401
    import requests  # noqa: F401
except ImportError:
    sys.exit("Missing packages. Run:  python -m pip install -r requirements.txt   (see README.md, step 2)")

from analysis import assess  # noqa: E402
from collect import AuthError, PortalUnreachable, collect_facts  # noqa: E402
from lm_client import ConfigError, LmClient, LmConfig, load_dotenv  # noqa: E402
from report_docx import build_report  # noqa: E402

log = logging.getLogger("lm_health")


def parse_args(argv: Optional[List[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a read-only LM Envision portal health check report (.docx).")
    parser.add_argument("--customer", help="Name shown on the report and in the file name (default: LM_ACCOUNT)")
    parser.add_argument("--out-dir", type=Path, default=HERE / "reports",
                        help="Folder for the report (default: the 'reports' folder next to this script)")
    parser.add_argument("--contract-devices", type=float, help="Contracted resources, to compute headroom")
    parser.add_argument("--contract-logs-gb", type=float, help="Contracted LM Logs volume in GB (shown for reference)")
    parser.add_argument("--contract-uptime-checks", type=float, help="Contracted LM Uptime checks (shown for reference)")
    parser.add_argument("--demo", action="store_true", help="Use a built-in fictional portal; no credentials needed")
    parser.add_argument("--no-prompt", action="store_true", help="Never prompt for credentials (for scheduled runs)")
    parser.add_argument("--verbose", action="store_true", help="Show rate-limit pacing and retry details")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None, session=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="  %(message)s")
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    contract = {"devices": args.contract_devices, "logs_gb": args.contract_logs_gb,
                "uptime_checks": args.contract_uptime_checks}
    sleep = time.sleep
    if args.demo:
        from mock_portal import DEMO_ACCESS_ID, DEMO_ACCESS_KEY, DEMO_ACCOUNT, DEMO_NAME, FakeSession
        config = LmConfig(DEMO_ACCOUNT, DEMO_ACCESS_ID, DEMO_ACCESS_KEY)
        session = session or FakeSession()
        sleep = lambda seconds: None  # noqa: E731
        customer = args.customer or DEMO_NAME
        print("Demo mode: using a built-in fictional portal. No real portal is contacted.")
    else:
        load_dotenv()
        try:
            config = LmConfig.from_env(interactive=not args.no_prompt)
        except ConfigError as exc:
            print(f"Credentials problem: {exc}", file=sys.stderr)
            return 2
        customer = args.customer or config.account

    client = LmClient(config, session=session, sleep=sleep)
    print(f"Collecting read-only data from {client.portal_host} ...")
    try:
        facts = collect_facts(client, customer, contract, progress=lambda m: print(f"  - {m}"))
    except AuthError:
        print(
            "\nLogicMonitor rejected the credentials (HTTP 401).\n"
            "  * Check LM_ACCOUNT is only the portal prefix (for 'acme.logicmonitor.com' use 'acme').\n"
            "  * Check the Access ID and Access Key were copied completely and the token is enabled.\n"
            "  * Make sure this computer's date, time and time zone are correct (signatures are time-based).",
            file=sys.stderr)
        return 2
    except PortalUnreachable as exc:
        print(f"\nCould not reach {client.portal_host}: {exc}\n"
              "  * Check LM_ACCOUNT / LM_DOMAIN, your internet or VPN connection and any proxy.", file=sys.stderr)
        return 1

    assessment = assess(facts)
    path = build_report(assessment, args.out_dir)
    print(f"\nDone. {facts.request_count} read-only requests, {facts.throttle_events} rate-limit back-offs.")
    if facts.gaps:
        print(f"{len(facts.gaps)} area(s) could not be read; the report explains why (Appendix B).")
    print(f"Overall status: {assessment.overall}")
    print(f"Report saved to:\n  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
