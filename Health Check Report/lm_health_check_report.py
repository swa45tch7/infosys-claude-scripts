#!/usr/bin/env python3
"""Generate an LM Envision customer health check Word report from a LogicMonitor portal.

Read-only: every call is a signed GET against REST API v3. The simplest run
prompts for everything it needs (the Access Key is read without echo):

    python3 "Health Check Report/lm_health_check_report.py"

Values can also come from flags, the environment (LM_ACCOUNT, LM_ACCESS_ID,
LM_ACCESS_KEY, LM_DOMAIN, LM_CUSTOMER, LM_PREPARED_BY) or a repo-root .env.
Exit 0 when the report is written, 1 on a portal or write error, 2 on missing
input or dependencies.
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
OPS_DIR = HERE.parent / "Operations Portal Maintenance"
for _path in (HERE, OPS_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from lm_api import ApiError, LmClient, LmConfig, load_dotenv  # noqa: E402

DEFAULT_DOMAIN = "logicmonitor.com"
DEFAULT_OUT_DIR = HERE / "reports"
DEMO_FIXTURES = HERE / "tests" / "fixtures" / "api"
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
ACCOUNT_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class InputError(RuntimeError):
    """Missing or invalid user input."""


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build the LM Envision customer health check Word report from a LogicMonitor "
            "portal (read-only). Run with no options to be prompted for everything."
        )
    )
    parser.add_argument("--portal", "--account", dest="account", help="Portal name, e.g. acme for acme.logicmonitor.com (LM_ACCOUNT)")
    parser.add_argument("--access-id", help="API token Access ID (LM_ACCESS_ID)")
    parser.add_argument(
        "--access-key",
        help="API token Access Key (LM_ACCESS_KEY). Prefer the hidden prompt or the env var: flags stay in shell history.",
    )
    parser.add_argument("--domain", help=f"Portal domain (LM_DOMAIN, default {DEFAULT_DOMAIN})")
    parser.add_argument("--customer", help="Customer name for the title, e.g. 'Acme Corp' (LM_CUSTOMER)")
    parser.add_argument("--prepared-by", help="Name shown as the report author (LM_PREPARED_BY, optional)")
    parser.add_argument("--output", type=Path, help="Output .docx path (default: reports/<Customer>_Infosys_LM_Envision_Health_Check_<DD_Mon_YYYY>.docx)")
    parser.add_argument("--template", type=Path, help="Your own Word file to use as the style base (styles, page setup, header/footer)")
    parser.add_argument("--subtitle", help="Cover subtitle (default: 'Portal health check | <portal>')")
    parser.add_argument("--contracted-resources", type=int, help="Contracted resource entitlement, enables the headroom rows")
    parser.add_argument("--no-prompt", action="store_true", help="Never prompt; fail if something required is missing")
    parser.add_argument("--demo", action="store_true", help="Build the report from the bundled synthetic data (no portal needed)")
    parser.add_argument("--debug", action="store_true", help="Show a traceback on unexpected errors")
    return parser.parse_args(argv)


def parse_portal(value: str, domain: str) -> tuple[str, str]:
    text = re.sub(r"^https?://", "", value.strip().lower()).split("/")[0]
    if "." in text:
        account, _, rest = text.partition(".")
        domain = rest or domain
    else:
        account = text
    if not ACCOUNT_RE.match(account):
        raise InputError(f"'{value}' is not a valid portal name (expected e.g. acme for acme.{domain}).")
    return account, domain


def report_filename(customer: str, moment: datetime) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "", re.sub(r"\s+", "_", customer.strip())) or "Customer"
    return f"{safe}_Infosys_LM_Envision_Health_Check_{moment.day:02d}_{MONTHS[moment.month - 1]}_{moment.year}.docx"


def _interactive(args: argparse.Namespace) -> bool:
    return not args.no_prompt and sys.stdin is not None and sys.stdin.isatty()


def resolve_inputs(args: argparse.Namespace) -> dict[str, str]:
    """Flags, then environment / .env, then prompts. Never echoes the Access Key."""
    values = {
        "account": args.account or os.environ.get("LM_ACCOUNT", ""),
        "access_id": args.access_id or os.environ.get("LM_ACCESS_ID", ""),
        "access_key": args.access_key or os.environ.get("LM_ACCESS_KEY", ""),
        "domain": args.domain or os.environ.get("LM_DOMAIN", "") or DEFAULT_DOMAIN,
        "customer": args.customer or os.environ.get("LM_CUSTOMER", ""),
        "prepared_by": args.prepared_by or os.environ.get("LM_PREPARED_BY", ""),
    }
    values = {key: value.strip() for key, value in values.items()}
    prompts = (
        ("account", "LogicMonitor portal name (e.g. acme for acme.logicmonitor.com): ", False),
        ("access_id", "API Access ID: ", False),
        ("access_key", "API Access Key (hidden): ", True),
        ("customer", "Customer name for the report title (e.g. Acme Corp): ", False),
    )
    interactive = _interactive(args)
    if interactive:
        for key, prompt, secret in prompts:
            while not values[key]:
                values[key] = (getpass.getpass(prompt) if secret else input(prompt)).strip()
        if not args.prepared_by and not os.environ.get("LM_PREPARED_BY"):
            values["prepared_by"] = input("Prepared by (optional, press Enter to skip): ").strip()
    missing = {
        "account": "portal name (--portal or LM_ACCOUNT)",
        "access_id": "Access ID (--access-id or LM_ACCESS_ID)",
        "access_key": "Access Key (LM_ACCESS_KEY or the prompt)",
        "customer": "customer name (--customer or LM_CUSTOMER)",
    }
    absent = [label for key, label in missing.items() if not values[key]]
    if absent:
        raise InputError("Missing " + "; ".join(absent) + ". Run in a terminal to be prompted, or set them.")
    values["account"], values["domain"] = parse_portal(values["account"], values["domain"])
    return values


def connection_error(exc: ApiError, portal_url: str) -> str:
    if exc.status == 401:
        return (
            "Authentication failed (HTTP 401). Check the Access ID and Access Key, that the API "
            "token is enabled, and that this laptop's clock is correct."
        )
    if exc.status == 403:
        return (
            "The API token was accepted but its role cannot view Resources (HTTP 403). Give the "
            "token a read-only role (see the README) and run again."
        )
    if exc.status == 404:
        return f"{portal_url} answered HTTP 404. Check the portal name (LM_ACCOUNT) and LM_DOMAIN."
    if exc.status == 0:
        reason = str(exc.body or "").strip()
        if reason.startswith("Non-JSON response"):
            return f"{portal_url} did not answer like a LogicMonitor portal. Check the portal name and LM_DOMAIN."
        return (
            f"Could not reach {portal_url} ({reason or 'network error'}). Check the portal name "
            "(LM_ACCOUNT), LM_DOMAIN, and your network or proxy."
        )
    return f"{portal_url} answered HTTP {exc.status}. Try again later or check the portal status."


def _print(text: str = "") -> None:
    print(text, flush=True)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except InputError as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        if args.debug:
            traceback.print_exc()
        print(f"Unexpected error: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        if not args.debug:
            print("Re-run with --debug for details.", file=sys.stderr)
        return 1


def run(args: argparse.Namespace) -> int:
    try:
        import docx  # noqa: F401
    except ImportError:
        print(
            "python-docx is not installed. Run: python3 -m pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 2
    from assessment import IST, ReportContext, build_assessment, load_rules  # noqa: E402
    from portal_data import FixtureSource, LiveSource, collect_portal_data  # noqa: E402

    import engine  # noqa: E402

    if args.template and not args.template.is_file():
        raise InputError(f"Template not found: {args.template}")
    load_dotenv()
    rules = load_rules()
    standards = engine.load_standards()
    clone_re = re.compile(rules["clone_name_pattern"])

    if args.demo:
        source = FixtureSource(DEMO_FIXTURES)
        now = float(source.meta.get("now") or time.time())
        customer = (args.customer or os.environ.get("LM_CUSTOMER") or "Example Corp").strip()
        prepared_by = (args.prepared_by or "").strip()
        portal = source.portal
        _print(f"LM health check report (demo data)  portal={portal}  customer={customer}")
    else:
        values = resolve_inputs(args)
        config = LmConfig(values["account"], values["access_id"], values["access_key"], values["domain"])
        portal = f"{config.account}.{config.domain}"
        customer, prepared_by = values["customer"], values["prepared_by"]
        _print(f"LM health check report  portal={portal}  customer={customer}")
        _print(f"Connecting to https://{portal} ...")
        try:
            LmClient(config, max_retries=2).get("/device/devices", {"size": "1", "fields": "id"})
        except ApiError as exc:
            print(f"Connection failed: {connection_error(exc, f'https://{portal}')}", file=sys.stderr)
            return 1
        _print("Connected. Collecting portal data (read-only):")
        source = LiveSource(LmClient(config))
        now = time.time()

    generated_at = datetime.fromtimestamp(now, IST)
    output = args.output or DEFAULT_OUT_DIR / report_filename(customer, generated_at)
    if output.suffix.lower() != ".docx":
        output = output.with_suffix(".docx")

    def show_fetch(index, total, spec, dataset) -> None:
        if dataset.ok:
            detail = f"{dataset.count:,}" if dataset.count is not None else "ok"
            if dataset.partial:
                detail += f" (partial: {dataset.partial})"
        else:
            detail = f"unavailable - {dataset.error}"
        _print(f"  [{index:>2}/{total}] {spec.label:<22} {detail}")

    data = collect_portal_data(source, now, rules, show_fetch, clone_pattern=clone_re.search)
    failed = [d for d in data.datasets.values() if not d.ok]
    if failed and len(failed) == len(data.datasets):
        print(f"Connection failed: no data could be read ({failed[0].error}).", file=sys.stderr)
        return 1

    ctx = ReportContext(
        customer=customer,
        portal=portal,
        generated_at=generated_at,
        prepared_by=prepared_by,
        subtitle=args.subtitle or f"Portal health check | {portal}",
        contracted_resources=args.contracted_resources,
    )
    assessment = build_assessment(data, ctx, rules, standards)

    import charts  # noqa: E402
    from docx_writer import build_document  # noqa: E402

    chart_images: dict[str, bytes | None] = {}
    charts_note = ""
    if charts.charts_available():
        m = assessment.metrics
        for key, render in (
            ("resources", lambda: charts.resource_chart(m, ctx.contracted_resources)),
            ("alerts", lambda: charts.alert_chart(m)),
            ("websites", lambda: charts.website_chart(m)),
            ("collectors", lambda: charts.collector_chart(assessment.collector_rows)),
        ):
            try:
                chart_images[key] = render()
            except Exception as exc:
                chart_images[key] = None
                _print(f"  chart {key}: skipped ({exc.__class__.__name__})")
    else:
        charts_note = "Chart unavailable: matplotlib is not installed (python3 -m pip install -r requirements.txt)."

    _print("Writing report sections:")

    def show_section(name: str, ok: bool, note: str) -> None:
        _print(f"  {name:<40} {'ok' if ok else 'FAILED - ' + note[:120]}")

    document = build_document(assessment, chart_images, args.template, charts_note, show_section)
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        document.save(str(output))
    except OSError as exc:
        print(f"Could not write {output}: {exc.strerror or exc}. Is the file open in Word?", file=sys.stderr)
        return 1
    _print("")
    if failed:
        _print(f"{len(failed)} of {len(data.datasets)} endpoints were unavailable; those rows say 'Data unavailable'.")
    _print(f"Overall status: {assessment.overall_status}")
    _print(f"Report written: {output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
