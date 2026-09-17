# LogicMonitor Bulk Device Onboarding

Standardized CSV-driven device onboarding for LogicMonitor (LM Envision), built for
Infosys delivery teams. One script, one CSV format, one audit trail per engagement.

Uses LogicMonitor REST API **v3** (`POST /device/devices`, `GET /setting/collectors`,
`GET|POST /device/groups`) with LMv1 authentication.

---

## Contents

| File | Purpose |
|---|---|
| `lm_device_onboard.py` | The onboarding script |
| `devices_template.csv` | Reference CSV with all supported columns |
| `README.md` | This document |

---

## 1. Prerequisites

**Python 3.9 or later**, plus one dependency:

```bash
pip install requests
```

**A LogicMonitor API token** (Settings → Users & Roles → API Tokens). The token's
role needs:

| Area | Minimum right |
|---|---|
| Resources | Manage |
| Collectors | View |

Without Manage on Resources the script fails with HTTP 403. Without View on
Collectors the collector list comes back empty.

**Portal name** is the subdomain only. For `https://infosys.logicmonitor.com`, the
portal name is `infosys`.

---

## 2. Quick start

```bash
# Step 1 — always validate first. Writes nothing to the portal.
python3 lm_device_onboard.py --csv devices.csv --dry-run

# Step 2 — onboard for real
python3 lm_device_onboard.py --csv devices.csv
```

On launch the script prompts for:

1. **Portal / company name** — e.g. `infosys`
2. **Access ID**
3. **Access Key** — hidden input; never echoed, never written to the log
4. **Collector** — the script fetches the portal's collectors and prints a numbered
   table (ID, up/down status, hostname, collector group). Enter the row number, or
   `id:171` to pick by collector ID directly.

---

## 3. CSV format

Only `name` is required. Everything else is optional.

### Core columns

| Column | Description |
|---|---|
| `name` | IP address or DNS name. This is the LogicMonitor host. **Required.** |
| `displayName` | Display name in LogicMonitor. Defaults to `name`. |
| `groupFullPath` | Slash-delimited group path, e.g. `Infosys/Customers/ACME/Network`. Missing levels are created automatically. |
| `hostGroupIds` | Comma-separated numeric group IDs. Takes precedence over `groupFullPath`. |
| `description` | Free-text description. |
| `collectorId` | Numeric collector ID. Overrides the collector chosen at startup. |
| `collectorName` | Collector hostname or description. Resolved to an ID. Used only if `collectorId` is blank. |
| `disableAlerting` | `true` / `false`. Useful for staging devices before go-live. |

### Property columns

Any column prefixed with `prop.` becomes a custom property on the device. The text
after the prefix is the property name, exactly as LogicMonitor expects it.

| CSV column | Property created |
|---|---|
| `prop.snmp.community` | `snmp.community` |
| `prop.snmp.version` | `snmp.version` |
| `prop.system.categories` | `system.categories` |
| `prop.location` | `location` |
| `prop.infosys.customer` | `infosys.customer` |

To add a new property across an engagement, add a column. No code change needed.

Blank property cells are skipped, so one template can serve mixed device types.

### Example

```csv
name,displayName,groupFullPath,collectorName,prop.snmp.version,prop.snmp.community
10.20.30.41,ACME-CORE-SW-01,Infosys/Customers/ACME/Network,lm-collector-mum-01,v2c,acme-ro
srv-app-01.acme.local,ACME-APP-01,Infosys/Customers/ACME/Servers/Linux,,v2c,acme-ro
```

See `devices_template.csv` for a fuller example.

---

## 4. Command reference

| Flag | Effect |
|---|---|
| `--csv PATH` | Path to the device CSV. Required. |
| `--dry-run` | Validates credentials, CSV structure, hostnames and collector references. Writes nothing and creates no groups. |
| `--company NAME` | Supplies the portal name, skipping that prompt. |
| `--collector-id N` | Supplies the default collector, skipping the selection table. |
| `--update-existing` | Updates devices that already exist. Without it, existing devices are skipped. |
| `--verbose` | Verbose console output. |

---

## 5. Behaviour worth knowing

**Existing devices are safe by default.** Before creating, the script looks the
device up by `name`. If it exists, the row is skipped and the report says so. Only
`--update-existing` will modify it.

**Groups are created, never deleted.** `groupFullPath` is walked one level at a
time; each missing level is created under its parent. Resolved paths are cached, so
a hundred devices in the same group cost one lookup.

**Dry run does not create groups.** This keeps validation runs completely
side-effect free, which also means dry run cannot confirm that a new group path is
creatable — only that the CSV is well formed.

**Retries are automatic.** HTTP 429 and 5xx responses are retried up to four times
with exponential backoff. If the portal sends a rate-limit window, that value is
honoured instead of the backoff.

**Interruption is safe.** Ctrl-C stops the run and still writes a report covering
every row processed up to that point.

---

## 6. Outputs

Both files are written next to the input CSV.

**`<csv_name>_onboarding_report.csv`** — one row per input row:

| Column | Meaning |
|---|---|
| `row` | Line number in the source CSV (header is line 1) |
| `name` | Device name |
| `displayName` | Resolved display name |
| `action` | `created`, `updated`, `skipped`, `failed`, or `dry-run` |
| `deviceId` | LogicMonitor device ID |
| `collectorId` | Collector actually used |
| `group` | Group path or IDs applied |
| `detail` | Property count, or the reason for a skip or failure |

**`<csv_name>_onboarding.log`** — full timestamped run log, including every group
created and every retry. Keep this with the engagement records.

The script exits `0` when no row failed, `1` when at least one did. That makes it
usable as a pipeline gate.

---

## 7. Unattended and pipeline use

Set these environment variables to skip all interactive prompts:

```bash
export LM_COMPANY=infosys
export LM_ACCESS_ID=your_access_id
export LM_ACCESS_KEY=your_access_key

python3 lm_device_onboard.py --csv devices.csv --collector-id 171
```

Never commit credentials. Use your CI platform's secret store, and confirm
`.env` and similar files are covered by `.gitignore`.

---

## 8. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Authentication failed (HTTP 401)` | Wrong portal name, Access ID or Access Key, or the token is disabled. Check the portal name is the subdomain only. |
| `Permission denied (HTTP 403)` | The token's role lacks Manage on Resources. Update the role in Settings → Users & Roles. |
| `No collectors returned` | The token cannot view collectors, or the portal has none installed. |
| `collectorName '...' not found` | The value must match a collector's hostname or description exactly. Run any command and read the collector table for the correct values. |
| `'...' is not a valid IP address or DNS name` | The `name` cell contains spaces or unsupported characters. `displayName` is the field for friendly names. |
| `must contain a 'name' column` | The CSV header is missing or misspelled. Compare against `devices_template.csv`. |
| Device created but not monitored | Expected. Onboarding adds the resource; DataSources apply on the collector's next discovery cycle. Check credentials properties such as `snmp.community`. |
| Repeated `Retry n/4` warnings | Portal-side rate limiting. The script handles it. For large batches, split the CSV or run off-peak. |

---

## 9. Recommended engagement workflow

1. Copy `devices_template.csv` and fill it in with the customer's inventory.
2. Confirm the group hierarchy matches the account standard.
3. Run with `--dry-run`. Resolve every `failed` row before continuing.
4. Run live. Review the report.
5. Re-run for failed rows only, in a smaller CSV.
6. Attach the report and log to the engagement record.
7. Verify in the portal that DataSources have applied after the next discovery cycle.

---

## 10. Pushing updates from iPad (iSH)

```bash
cd ~/infosys-claude-scripts
git add .
git commit -m "Describe your change"
git push origin main
```

When prompted, enter your GitHub username and **paste** your personal access token
as the password. The token will not display as you paste it — that is expected.
