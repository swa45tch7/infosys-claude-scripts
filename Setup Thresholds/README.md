# LogicMonitor Alerting Setup from Excel

Standardized threshold tuning, escalation chain creation and alert rule routing
for LogicMonitor (LM Envision), driven from Excel. Built for Infosys delivery teams.

Uses LogicMonitor REST API **v3** with LMv1 authentication.

---

## Contents

| File | Purpose |
|---|---|
| `lm_api.py` | Shared API client and helpers. Imported by the three scripts. Not run directly. |
| `lm_threshold_apply.py` | Applies datapoint thresholds per device |
| `lm_escalation_chains.py` | Creates escalation chains with stages and recipients |
| `lm_alert_rules.py` | Creates alert rules and binds them to chains |
| `thresholds_template.xlsx` | Threshold input template, with an Instructions sheet |
| `alerting_template.xlsx` | Chain and rule input template, with Instructions sheets |

Keep all four `.py` files in the same folder. The three scripts import `lm_api.py`.

---

## 1. Prerequisites

```bash
pip install requests openpyxl
```

Python 3.9 or later.

**API token role permissions:**

| Area | Minimum right | Needed by |
|---|---|---|
| Resources | Manage | Thresholds |
| Alert rules | Manage | Alert rules |
| Escalation chains | Manage | Escalation chains |
| Collectors | View | All |

**Portal name** is the subdomain only. For `https://infosys.logicmonitor.com`, use `infosys`.

Each script prompts for portal name, Access ID and Access Key at launch. The
Access Key is hidden input and is never written to the log.

---

## 2. Run order matters

These three build on each other. Running them out of order fails.

```bash
# 1. Escalation chains first — alert rules reference them by name
python3 lm_escalation_chains.py --excel alerting.xlsx --sheet EscalationChains --dry-run
python3 lm_escalation_chains.py --excel alerting.xlsx --sheet EscalationChains

# 2. Alert rules second
python3 lm_alert_rules.py --excel alerting.xlsx --sheet AlertRules --dry-run
python3 lm_alert_rules.py --excel alerting.xlsx --sheet AlertRules

# 3. Thresholds any time after the devices are onboarded and discovered
python3 lm_threshold_apply.py --excel thresholds.xlsx --dry-run
python3 lm_threshold_apply.py --excel thresholds.xlsx
```

Always run `--dry-run` first and clear every failed row before going live.

Thresholds need the DataSource to have been applied and its instances discovered,
so run them after onboarding has completed a discovery cycle, not immediately after.

---

## 3. Thresholds — `lm_threshold_apply.py`

### Sheet format

One row per severity. Required columns: `device`, `dataSource`, `datapoint`.

| Column | Description |
|---|---|
| `device` | Device display name as shown in LogicMonitor. Host name also works. |
| `dataSource` | DataSource name exactly as applied, e.g. `Ping`, `CPU`, `Disk` |
| `datapoint` | Datapoint name, e.g. `PingLossPercent`, `CPUBusyPercent` |
| `severity` | `warn`, `error` or `critical` |
| `value` | The threshold value for that severity |
| `operator` | `>` `>=` `<` `<=` `=` `!=`. Default `>` |
| `instance` | Instance name, or `*` for every instance. Default `*` |
| `scope` | `instance` (default) or `datasource` |
| `alertTransitionInterval` | Polls above threshold before the alert raises |
| `alertClearTransitionInterval` | Polls below threshold before the alert clears |
| `disableAlerting` | `true` / `false` |
| `alertExpr` | Full expression override, e.g. `> 80 90 95`. Wins over severity rows. |
| `note` | Free text, carried into the report only |

### How severity rows combine

Rows sharing `device` + `dataSource` + `instance` + `datapoint` are grouped into
one threshold expression:

| Severities supplied | Expression produced |
|---|---|
| warn 80, error 90, critical 95 | `> 80 90 95` |
| warn 80, error 90 | `> 80 90` |
| warn 80 | `> 80` |
| critical 90 only | `> 90 90 90` |

LogicMonitor evaluates the expression right to left, so the highest severity wins.
When the lowest severity you supply is not `warn`, the lower slots are padded with
the same value — that is what makes a critical-only threshold fire as critical
rather than as a warning. Every composed expression is written to the log, so it
can be checked before going live.

### Scope

- `instance` (default) sets the threshold on each matching instance of that
  DataSource on that device. This is the granular, per-instance path.
- `datasource` sets it once at the device's DataSource level, covering all its
  instances through the instance group.

Use `instance` unless you specifically want the DataSource-level setting.

### Settings without a threshold

A row with `disableAlerting` set and no severity or value is valid. It applies
the setting and leaves the threshold untouched — useful for staging or UAT
devices that should be monitored but not alerting yet.

---

## 4. Escalation chains — `lm_escalation_chains.py`

### Sheet format

One row per recipient. Required columns: `chainName`, `stage`, `recipientType`, `recipient`.

| Column | Description |
|---|---|
| `chainName` | Rows sharing this name build one chain |
| `stage` | Stage number, starting at 1 |
| `recipientType` | `group`, `admin` or `arbitrary` |
| `recipient` | Recipient group name, LogicMonitor username, or email/phone |
| `method` | `email`, `sms`, `voice`, `smsemail`. Not needed for `group` |
| `contact` | Phone number, where the method needs one |
| `description` | Chain description. Read from the first row of the chain. |
| `enableThrottling` | `true` / `false` |
| `throttlingPeriod` | Minutes |
| `throttlingAlerts` | Alert count within the period |
| `ccRecipient` | Recipient copied on every stage |
| `ccRecipientType` | `group`, `admin` or `arbitrary`. Default `admin` |
| `ccMethod` | `email`, `sms`, `voice`, `smsemail` |

### Recipient types

- **`group`** — the recipient is a LogicMonitor recipient group name. The group
  carries its own contact methods, so `method` and `contact` are ignored.
- **`admin`** — the recipient is a LogicMonitor username. Set `method`.
- **`arbitrary`** — the recipient is a raw email address or phone number. Set `method`.

### Stages

Every recipient in a stage is notified together. A later stage is notified only
if the alert is still active and unacknowledged after the escalation interval —
and that interval lives on the **alert rule**, not on the chain.

Stage numbers must run consecutively from 1. A gap is reported as a failure
rather than silently collapsed, because an accidental gap changes who gets paged.

`description`, `enableThrottling`, `throttlingPeriod` and `throttlingAlerts` are
read from the first row of each chain. Later rows may leave them blank.

---

## 5. Alert rules — `lm_alert_rules.py`

### Sheet format

One row is one alert rule. Nothing is grouped. Required columns: `ruleName`,
`priority`, `escalationChainName`.

| Column | Description |
|---|---|
| `ruleName` | Alert rule name |
| `priority` | A number, or `high` (100), `medium` (200), `low` (300) |
| `escalationChainName` | Name of an existing escalation chain |
| `dataSource` | DataSource name. Wildcards allowed, e.g. `*MYSQL*`. Blank matches all |
| `instance` | Instance name. Wildcards allowed |
| `datapoint` | Datapoint name. Wildcards allowed |
| `devices` | Device display names separated by `;` |
| `deviceGroups` | Resource group full paths separated by `;` |
| `levelStr` | `All`, `Warning`, `Error` or `Critical`. Default `All` |
| `escalationInterval` | Minutes between stages. `0` disables escalation |
| `suppressAlertClear` | `true` stops clear notifications |
| `suppressAlertAckSdt` | `true` stops acknowledgement and SDT notifications |
| `description` | Rule description |

### Priority decides routing

An alert is routed by the **first matching rule only**. Specific rules need a
lower priority number than broad ones, or the broad rule swallows them.

In the template, the named-device rule sits at 150 and the whole-account rule at
200, so the specific one is evaluated first. The script warns on duplicate
priorities and creates rules in priority order so the portal's rule list reads
in the same sequence it will be evaluated.

### Validation before creation

Device names and resource group paths are resolved against the portal before the
rule is created. A typo therefore fails loudly instead of creating a rule that
silently matches nothing — which is the failure mode that hurts most, since a
rule matching nothing looks correctly configured in the UI.

---

## 6. Command reference

All three scripts share these flags.

| Flag | Effect |
|---|---|
| `--excel PATH` | Path to the Excel file. Required. |
| `--sheet NAME` | Worksheet name. Defaults to the first sheet. |
| `--dry-run` | Validates the sheet and shows exactly what would be applied. Makes no changes. The threshold script does not even connect to the portal in this mode. |
| `--company NAME` | Supplies the portal name, skipping that prompt |
| `--update-existing` | Chains and rules only. Updates existing objects instead of skipping them. |
| `--verbose` | Verbose console output |

---

## 7. Sheet reading rules

The same rules apply to all three scripts.

- **Row 1 is the header.** Column names are matched exactly, including case.
- **Reading stops at the first completely blank row.** Anything below the data
  block — notes, working columns, scratch calculations — is never parsed.
- **A row whose first populated cell starts with `#` is skipped.** This is how
  the example row in each template is neutralised, so it is safe to leave in place.
- **Instructions live on their own sheet** in both templates, never under the data.

---

## 8. Outputs

Each script writes two files next to the input workbook.

| File | Content |
|---|---|
| `<name>_thresholds_report.csv` | row, device, action, dataSource, datapoint, instance, expression, detail |
| `<name>_chains_report.csv` | row, chain, action, chainId, stages, recipients, detail |
| `<name>_rules_report.csv` | row, rule, action, ruleId, priority, chain, scope, detail |
| `<name>_*.log` | Full timestamped run log including every retry |

`action` is one of `created`, `updated`, `applied`, `skipped`, `failed` or `dry-run`.

Exit code is `0` when no row failed and `1` when at least one did, so each script
works as a pipeline gate. Ctrl-C still writes a report for everything processed
up to that point.

---

## 9. Unattended and pipeline use

```bash
export LM_COMPANY=infosys
export LM_ACCESS_ID=your_access_id
export LM_ACCESS_KEY=your_access_key

python3 lm_escalation_chains.py --excel alerting.xlsx --sheet EscalationChains
python3 lm_alert_rules.py --excel alerting.xlsx --sheet AlertRules
python3 lm_threshold_apply.py --excel thresholds.xlsx
```

Never commit credentials. Use the CI platform's secret store and confirm `.env`
and similar files are covered by `.gitignore`.

---

## 10. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Authentication failed (HTTP 401)` | Wrong portal name, Access ID or Access Key, or the token is disabled |
| `Permission denied (HTTP 403)` | The token's role lacks Manage on the area named in the message |
| `Device not found` | The `device` value does not match a display name or host name. Check for trailing spaces. |
| `matches N devices` | Two devices share that name. Use the exact display name. |
| `DataSource 'X' is not applied to this device` | The error lists the DataSources that are applied. Copy the name from there. |
| `has no discovered instances yet` | Active Discovery has not run since onboarding. Wait a cycle and re-run. |
| `Datapoint 'X' not found on any matching instance` | The error lists available datapoints. Names are case-sensitive in the sheet but matched case-insensitively. |
| `Escalation chain not found` | Run `lm_escalation_chains.py` first, or correct the name |
| `Stage numbers must run consecutively from 1` | A stage number is missing or duplicated. Fix the `stage` column. |
| `Resource group not found` | The `deviceGroups` path must be the full path, e.g. `Infosys/Customers/ACME/Network`, with no leading slash |
| Rule created but no notifications arrive | Check rule priority — a broader rule at a lower number may be matching first. Also confirm recipient email addresses are verified in LogicMonitor; unverified addresses do not receive alerts. |
| Repeated `Retry n/4` warnings | Portal-side rate limiting. Handled automatically. Split large workbooks or run off-peak. |

---

## 11. Recommended engagement workflow

1. Onboard devices first (`lm_device_onboard.py`). Wait for discovery to complete.
2. Fill in the `EscalationChains` sheet. Confirm recipient groups exist in the portal.
3. Dry run, then create the chains.
4. Fill in the `AlertRules` sheet. Agree the priority order with the customer before running.
5. Dry run, review the reported routing scope per rule, then create the rules.
6. Fill in the threshold sheet from the customer's agreed alert matrix.
7. Dry run. Check every composed expression in the log against the matrix.
8. Apply. Attach all reports and logs to the engagement record.
9. Validate end to end by triggering one test alert and confirming it reaches the intended recipient.

---

## 12. Pushing updates from iPad (iSH)

```bash
cd ~/infosys-claude-scripts
git add .
git commit -m "Describe your change"
git push origin main
```

When prompted, enter your GitHub username and **paste** your personal access
token as the password. The token will not display as you paste it — that is expected.
