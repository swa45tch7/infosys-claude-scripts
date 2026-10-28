# LogicMonitor Bulk Device Onboarding

Adds devices to LogicMonitor in bulk from a CSV file: creates any missing
resource groups, assigns a Collector, and sets custom properties. This tool
**writes** to the portal, so always run `--dry-run` first.

## What you need

- Python 3.9 or newer.
- A LogicMonitor API token whose role has **Manage** on Resources and **View**
  on Collectors.
- Your portal name: `acme` for `https://acme.logicmonitor.com`.
- A CSV of devices. Copy `devices_template.csv` and replace the example rows.
  Only the `name` column (IP or DNS name) is required. Any column starting with
  `prop.` becomes a property, for example `prop.snmp.community`.

## How to run

Open a terminal in this folder. The script asks for the portal name, Access ID,
Access Key (hidden) and then shows a numbered list of Collectors to pick from.

Windows (PowerShell):

```powershell
py -m pip install -r requirements.txt
py lm_device_onboard.py --csv devices.csv --dry-run
py lm_device_onboard.py --csv devices.csv
```

Mac / Linux:

```bash
python3 -m pip install -r requirements.txt
python3 lm_device_onboard.py --csv devices.csv --dry-run
python3 lm_device_onboard.py --csv devices.csv
```

Devices that already exist are skipped unless you add `--update-existing`.
`--collector-id 171` skips the Collector prompt. `--help` lists every option.

## What you get

Two files next to your CSV:

- `<csv name>_onboarding_report.csv`: one line per row with `created`,
  `updated`, `skipped`, `failed` or `dry-run`, and the reason.
- `<csv name>_onboarding.log`: the full run log.

The exit code is `1` if any row failed, otherwise `0`.

## Common errors

| Message | Fix |
|---|---|
| `Authentication failed (HTTP 401)` | Check the portal name (subdomain only), Access ID and Access Key. |
| `Permission denied (HTTP 403)` | The token's role needs Manage on Resources. |
| `collectorName '...' not found` | Use the Collector hostname or description exactly as shown in the Collector list. |
| `must contain a 'name' column` | The CSV header is missing or misspelled. Compare with `devices_template.csv`. |
