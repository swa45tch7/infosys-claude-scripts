#!/usr/bin/env python3
"""Live health check of a LogicMonitor portal against published standards.

Read-only. Exit 1 if any critical finding is reported.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from engine import (  # noqa: E402
    fetch_snapshot,
    load_standards,
    print_report,
    results_to_json,
    run_checks,
    exit_code as report_exit,
)
from lm_api import ApiError, ConfigError, LmClient, load_dotenv  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Health-check a LogicMonitor portal (read-only)."
    )
    parser.add_argument("--json", action="store_true", help="Write JSON to stdout")
    parser.add_argument(
        "--standards",
        type=Path,
        default=None,
        help="Override path to standards.json",
    )
    args = parser.parse_args(argv)

    load_dotenv()
    standards = load_standards(args.standards)
    try:
        client = LmClient()
        snapshot = fetch_snapshot(client, standards)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2
    except ApiError as exc:
        print(f"API error: {exc}", file=sys.stderr)
        return 1

    results = run_checks(snapshot, standards, suite="health")
    if args.json:
        json.dump(results_to_json(results, snapshot.portal, "health"), sys.stdout, indent=2)
        print()
    else:
        print_report(results, snapshot.portal, "health")
    return report_exit(results)


if __name__ == "__main__":
    raise SystemExit(main())
