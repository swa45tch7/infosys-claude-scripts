"""Guided start for non-technical users. Called by Start-Mac.command and Start-Windows.bat.

Asks for the login (unless .env already has it), the customer name and the
contract device count, runs the report and opens it.
"""

from __future__ import annotations

import getpass
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGE_DIR = HERE.parent
ENV_FILE = PACKAGE_DIR / ".env"
sys.path.insert(0, str(HERE))

import lm_health_report  # noqa: E402
from lm_client import load_dotenv  # noqa: E402

KEYS = ("LM_ACCOUNT", "LM_ACCESS_ID", "LM_ACCESS_KEY")
LOGIN_REJECTED, PORTAL_UNREACHABLE = 2, 3


def say(text: str = "") -> None:
    print(text, flush=True)


def ask(question: str) -> str:
    try:
        return input(f"{question} ").strip()
    except EOFError:
        return ""


def clean_account(text: str) -> str:
    text = text.strip().lower()
    for prefix in ("https://", "http://"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text.split("/")[0].split(".")[0]


def have_login() -> bool:
    return all(os.environ.get(key, "").strip() for key in KEYS)


def ask_login() -> None:
    say()
    say("YOUR LOGICMONITOR LOGIN")
    say("Portal name = the first word of your LogicMonitor web address.")
    say("Example: for acme.logicmonitor.com, type acme")
    account = ""
    while not account:
        account = clean_account(ask("Portal name:"))
    access_id = ""
    while not access_id:
        access_id = ask("Access ID:")
    say("Access key: paste or type it, then press Enter. It stays hidden.")
    access_key = ""
    while not access_key:
        try:
            access_key = getpass.getpass("Access key: ").strip()
        except EOFError:
            access_key = ""
    os.environ.update({"LM_ACCOUNT": account, "LM_ACCESS_ID": access_id, "LM_ACCESS_KEY": access_key})
    if ask("Save this login for next time? Type y and press Enter, or just press Enter for no:").lower().startswith("y"):
        save_login()
        say("Saved.")


def save_login() -> None:
    lines = [f"{key}={os.environ[key]}" for key in KEYS]
    domain = os.environ.get("LM_DOMAIN", "").strip()
    if domain:
        lines.append(f"LM_DOMAIN={domain}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if os.name == "posix":
        os.chmod(ENV_FILE, 0o600)


def ask_details() -> list:
    say()
    say("REPORT DETAILS (press Enter to skip)")
    account = os.environ["LM_ACCOUNT"]
    customer = ask(f"Customer name [{account}]:") or account
    args = ["--no-prompt", "--customer", customer]
    while True:
        devices = ask("Contract device count:").replace(",", "").replace(" ", "")
        if not devices:
            break
        if devices.isdigit() and int(devices) > 0:
            args += ["--contract-devices", devices]
            break
        say("Please type a number, like 1500. Or press Enter to skip.")
    return args


def open_file(path: Path) -> bool:
    for target in (path, path.parent):
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", str(target)], check=True)
            elif os.name == "nt":
                os.startfile(str(target))  # type: ignore[attr-defined]
            else:
                subprocess.run(["xdg-open", str(target)], check=True)
            return True
        except Exception:
            continue
    return False


def run() -> int:
    say("LOGICMONITOR HEALTH CHECK")
    say("This only reads data. It changes nothing in LogicMonitor.")
    load_dotenv(PACKAGE_DIR)
    if have_login():
        say()
        say(f"Using your saved login for: {os.environ['LM_ACCOUNT']}")
    else:
        ask_login()
    args = ask_details()

    while True:
        say()
        say("MAKING THE REPORT. This can take a few minutes...")
        say()
        code = lm_health_report.main(args)
        if code == 0 and lm_health_report.last_report:
            report = lm_health_report.last_report
            say()
            if open_file(report):
                say("DONE. Your report is open.")
            else:
                say("DONE.")
            say(f"It is saved here: {report}")
            return 0
        if code in (LOGIN_REJECTED, PORTAL_UNREACHABLE):
            say()
            say("The login or portal name did not work.")
            if ask("Type y and press Enter to try again, or just press Enter to stop:").lower().startswith("y"):
                ask_login()
                continue
        say()
        say("NOT FINISHED. Please read the message above.")
        return 1


def main() -> int:
    try:
        return run()
    except KeyboardInterrupt:
        say()
        say("Stopped.")
        return 1
    except Exception as exc:  # last-resort message for non-technical users
        say()
        say(f"NOT FINISHED. Something went wrong: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
