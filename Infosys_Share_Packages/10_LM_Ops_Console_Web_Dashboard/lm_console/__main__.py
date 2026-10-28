"""Start the console: python -m lm_console [--port N] [--no-browser]."""

from __future__ import annotations

import argparse
from typing import List, Optional


def cli(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m lm_console",
        description=(
            "LogicMonitor Ops Console: a local web page that runs read-only health "
            "checks against a LogicMonitor portal. Listens on 127.0.0.1 only."
        ),
    )
    parser.add_argument("--port", type=int, default=8787, help="Local port (default 8787)")
    parser.add_argument(
        "--no-browser", action="store_true", help="Do not open a browser tab on start"
    )
    args = parser.parse_args(argv)

    from .server import main

    main(port=args.port, open_browser=not args.no_browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
