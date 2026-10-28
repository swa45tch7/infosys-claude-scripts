# LogicMonitor Diagnose

Runs read-only checks against a LogicMonitor portal and prints what is wrong
with a resource, a Collector, alert routing, or the whole portal, plus what to
do next. It makes no changes.

## What you need

- Python 3.9 or newer.
- A LogicMonitor API token (Settings > Users & Roles > API Tokens) whose role has
  **View** on Resources, Collectors, Alert rules and Escalation chains.
- Your portal name: `acme` for `https://acme.logicmonitor.com`.

## How to run

Open a terminal in this folder. The script asks for the portal name, Access ID
and Access Key (the key is hidden as you type).

Windows (PowerShell):

```powershell
py -m pip install -r requirements.txt
py lm_diagnose.py portal
py lm_diagnose.py device "ACME-APP-01"
```

Mac / Linux:

```bash
python3 -m pip install -r requirements.txt
python3 lm_diagnose.py portal
python3 lm_diagnose.py device "ACME-APP-01"
```

Other checks: `collector <name or id>`, `credentials <resource>`,
`alerting <resource> --datasource CPU --datapoint CPUBusyPercent`, and
`all <resource>` (device + credentials + alerting). Add `--out findings.md` to
save a Markdown copy, or `--json` for JSON. `--help` lists every option.

## What you get

Findings on screen, worst first: `FAIL` (likely cause), `WARN`, `OK`, `INFO`.
A run log is written to `lm_diagnose.log` in the folder you ran it from. The
exit code is `1` if anything failed, otherwise `0`.

## Common errors

| Message | Fix |
|---|---|
| `Missing dependency. Install with: pip install requests` | Run the `pip install -r requirements.txt` line above. |
| `Authentication failed (HTTP 401)` | Check the portal name (subdomain only), Access ID and Access Key, and that the token is enabled. |
| `Permission denied (HTTP 403)` | The token's role is missing a View right listed above. |
| `Device not found` | Use the resource's display name exactly as shown in the portal. |
