#!/usr/bin/env python3
"""Weekly / daily operations report: health + configuration deviations.

Writes markdown, JSON and CSV next to the script (or --out-dir). Read-only.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from engine import (  # noqa: E402
    fetch_snapshot,
    load_standards,
    print_report,
    results_to_json,
    results_to_markdown,
    run_checks,
    write_csv,
    exit_code as report_exit,
)
from lm_api import ApiError, ConfigError, LmClient, load_dotenv  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Produce a full operations maintenance report (read-only)."
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=HERE / "reports",
        help="Directory for markdown, JSON and CSV output",
    )
    parser.add_argument(
        "--standards",
        type=Path,
        default=None,
        help="Override path to standards.json",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Do not print the console report",
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

    results = run_checks(snapshot, standards, suite="all")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"ops-report-{stamp}.md"
    json_path = out_dir / f"ops-report-{stamp}.json"
    csv_path = out_dir / f"ops-report-{stamp}.csv"
    md_path.write_text(results_to_markdown(results, snapshot.portal, "all"), encoding="utf-8")
    json_path.write_text(
        json.dumps(results_to_json(results, snapshot.portal, "all"), indent=2),
        encoding="utf-8",
    )
    write_csv(results, csv_path)

    if not args.quiet:
        print_report(results, snapshot.portal, "all")
        print(f"\nWrote {md_path}")
        print(f"Wrote {json_path}")
        print(f"Wrote {csv_path}")
    return report_exit(results)


if __name__ == "__main__":
    raise SystemExit(main())
