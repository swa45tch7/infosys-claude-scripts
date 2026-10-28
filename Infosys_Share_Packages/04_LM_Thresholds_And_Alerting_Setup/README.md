# LogicMonitor Thresholds and Alerting Setup

Sets up alerting in LogicMonitor from Excel: escalation chains, alert rules, and
datapoint thresholds per device. These tools **write** to the portal, so always
run `--dry-run` first. Dry runs only read the workbook and never connect.

## What you need

- Python 3.9 or newer.
- A LogicMonitor API token whose role has **Manage** on Resources (thresholds),
  Alert rules and Escalation chains, and **View** on Collectors.
- Your portal name: `acme` for `https://acme.logicmonitor.com`.
- Filled-in copies of the templates. Each has an Instructions sheet.
  - `alerting_template.xlsx`: sheets `EscalationChains` and `AlertRules`.
  - `thresholds_template.xlsx`: sheet `Thresholds`.

## How to run

Run in this order: chains first, then rules (they point at chains by name), then
thresholds (after the devices have been discovered). Each live run asks for the
portal name, Access ID and Access Key (hidden).

Windows (PowerShell):

```powershell
py -m pip install -r requirements.txt
py lm_escalation_chains.py --excel alerting.xlsx --sheet EscalationChains --dry-run
py lm_escalation_chains.py --excel alerting.xlsx --sheet EscalationChains
py lm_alert_rules.py --excel alerting.xlsx --sheet AlertRules --dry-run
py lm_alert_rules.py --excel alerting.xlsx --sheet AlertRules
py lm_threshold_apply.py --excel thresholds.xlsx --dry-run
py lm_threshold_apply.py --excel thresholds.xlsx
```

Mac / Linux: the same commands with `python3` instead of `py`.

Existing chains and rules are skipped unless you add `--update-existing`.
`--help` on any script lists every option.

## What you get

Next to the workbook, per script: a `<name>_chains_report.csv`,
`<name>_rules_report.csv` or `<name>_thresholds_report.csv` with one line per
row (`created`, `updated`, `applied`, `skipped`, `failed` or `dry-run`), and a
`.log` file. The exit code is `1` if any row failed, otherwise `0`.

## Common errors

| Message | Fix |
|---|---|
| `Permission denied (HTTP 403)` | The token's role needs Manage on the area in the message. |
| `Escalation chain not found` | Run `lm_escalation_chains.py` first, or correct the name. |
| `Device not found` | Use the device display name exactly as shown in the portal. |
| `has no discovered instances yet` | Discovery has not run since onboarding. Wait one cycle and retry. |
