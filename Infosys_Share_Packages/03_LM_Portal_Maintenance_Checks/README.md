# LogicMonitor Portal Maintenance Checks

Read-only daily and weekly checks for a LogicMonitor portal: down Collectors,
dead resources, Collector capacity and failover, ageing alerts, and drift from
the standards in `standards.json` (properties, grouping, alert routing, SDT, API
tokens). It makes no changes.

## What you need

- Python 3.10 or newer. No extra packages.
- A LogicMonitor API token whose role has **View** on Resources, Collectors,
  Alert rules and Escalation chains. View on Users is optional (token checks).
- Three settings: `LM_ACCOUNT` (portal name, `acme` for `acme.logicmonitor.com`),
  `LM_ACCESS_ID`, `LM_ACCESS_KEY`. Set them as below, or copy `.env.example` to
  `.env` in this folder and fill it in.

## How to run

Open a terminal in this folder.

Windows (PowerShell):

```powershell
$env:LM_ACCOUNT="acme"; $env:LM_ACCESS_ID="<access-id>"; $env:LM_ACCESS_KEY="<access-key>"
py health_check.py
py config_deviation.py
py maintenance_report.py
```

Mac / Linux:

```bash
export LM_ACCOUNT=acme LM_ACCESS_ID='<access-id>' LM_ACCESS_KEY='<access-key>'
python3 health_check.py
python3 config_deviation.py
python3 maintenance_report.py
# or all three in one go:
bash run_ops_checks.sh
```

`health_check.py` is the daily check, `config_deviation.py` the weekly standards
check, and `maintenance_report.py` runs both and saves the results. Add `--json`
to the first two for JSON output.

## What you get

A report on screen with `critical`, `warn`, `info` and `ok` findings.
`maintenance_report.py` also writes `ops-report-<date>.md`, `.json` and `.csv`
into a `reports` folder here (change it with `--out-dir`). Exit code: `0` nothing
critical, `1` something critical, `2` settings missing.

## Common errors

| Message | Fix |
|---|---|
| `Config error: Missing LM_ACCOUNT, ...` | Set the three settings above in the same terminal window, or create `.env`. |
| `API error: LogicMonitor API HTTP 401` | Wrong Access ID or Key, token disabled, or the laptop clock is wrong. |
| `API error: LogicMonitor API HTTP 0` | The portal could not be reached. Check `LM_ACCOUNT`, the network or proxy. |
