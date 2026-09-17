# LogicMonitor Diagnostics

Read-only diagnostic tool for LogicMonitor (LM Envision). Runs live checks against
a portal and reports each finding with the next action to take.

Built for Infosys delivery teams. **Makes no changes to the portal.**

Requires `lm_api.py` in the same folder.

---

## 1. Prerequisites

```bash
pip install requests openpyxl
```

The API token needs **View** rights on Resources, Collectors, Alert rules and
Escalation chains. No Manage rights are needed, so this can safely be given to
engineers who should not be able to change configuration.

---

## 2. The five scenarios

```bash
# Discovery and no-data problems on one resource
python3 lm_diagnose.py device ACME-CORE-SW-01

# Performance, capacity and health of one Collector
python3 lm_diagnose.py collector lm-collector-mum-01
python3 lm_diagnose.py collector 171

# Why an alert did not fire, or reached the wrong people
python3 lm_diagnose.py alerting ACME-APP-01 --datasource CPU --datapoint CPUBusyPercent

# Missing or wrong properties and credentials on one resource
python3 lm_diagnose.py credentials ACME-APP-01

# Portal-wide sweep
python3 lm_diagnose.py portal

# device + credentials + alerting in one run
python3 lm_diagnose.py all ACME-APP-01
```

---

## 3. Finding levels

| Level | Meaning |
|---|---|
| `FAIL` | Broken, and very likely the cause |
| `WARN` | Worth attention, may or may not be the cause |
| `OK` | Checked and healthy |
| `INFO` | Context, no action implied |

Findings are printed FAIL first, so the likely cause is at the top. Each FAIL and
WARN carries a recommended action. Exit code is `1` if any FAIL was reported,
which makes the tool usable as a health gate in a pipeline.

---

## 4. What each scenario checks

### `device` — discovery and no data

- Host status. A `dead` host means LogicMonitor cannot reach the resource at all.
- Preferred Collector is set, and whether that Collector is up.
- Whether the resource has failed over to a different Collector than its preferred one.
- Resource-level alerting switch.
- Whether any DataSources matched. None applied means nothing will ever be
  collected, and points at `system.categories` or the sysOID.
- Active Discovery results per DataSource. Zero instances is treated as a warning
  within the first hour after onboarding and a failure after that, because a fresh
  resource legitimately has not discovered yet.
- DataSources with monitoring stopped.
- Data freshness. Samples up to three instances of the busiest DataSource and
  reports how old the newest value is. Distinguishes three states: no data at all,
  partial staleness across instances, and timestamps arriving with null values —
  which are three different root causes.

### `collector` — performance and capacity

- Up or down.
- Uptime, flagging a restart within the last hour since that explains data gaps.
- Watchdog freshness, which goes stale when the host is under pressure or has lost
  the portal connection.
- Resource and instance counts.
- **Load compared to its peers of the same size**, rather than against a fixed
  number. LogicMonitor's documented capacity depends on which collection methods
  are in use, so a hardcoded device limit would mislead. A Collector carrying more
  than twice the median of its same-size peers is flagged.
- Failover Collector configured.
- **The Collector's own monitored metrics**, read from the Collector host in
  monitoring: unavailable scheduled tasks, task queue depth and JVM heap. These are
  the metrics LogicMonitor documents as the leading indicators of a Collector
  reaching capacity. Any non-zero unavailable-task or queue value is reported,
  because zero is the healthy state.

If the Collector host is not itself in monitoring, the tool says so rather than
reporting a clean result it could not verify.

### `alerting` — not firing, or misrouted

Walks the full path an alert takes, stopping at whatever breaks it:

1. Resource-level alerting disabled.
2. Active scheduled downtime.
3. Whether a threshold exists on the datapoint at all. No threshold means no alert
   is ever raised, so rules never come into play — a common dead end.
4. Datapoint-level alerting disabled.
5. **Alert rule matching, simulated in priority order.** Evaluates DataSource,
   datapoint, severity, named devices and resource group membership (including
   inheritance down the group tree) to determine which rule would actually win.
6. All other matching rules, since only the first applies. This is the check that
   finds misrouting.
7. Escalation interval of `0`, which means only the first stage is ever notified.
8. Clear-notification suppression.
9. The winning chain's stages and recipients, including empty stages.
10. Chain throttling, which during an alert storm looks exactly like alerts not firing.

Defaults to `--level critical`. Use `--level warning` or `--level error` to
simulate a different severity, since rules are often severity-scoped.

### `credentials` — properties

- `system.categories`, which drives much of DataSource matching.
- `system.sysoid`. Absent on an SNMP device usually means SNMP is not responding.
- **Credential properties expected for the categories present** on that resource —
  for example `snmp.community` for an SNMP category, `wmi.user` for Windows.
  Presence only is checked, never the value.
- Local credential overrides. A local value beats the group value, so a stale local
  credential survives every group-level fix. That makes it worth surfacing.
- Properties that exist with a blank value. These block inheritance from the group
  without supplying a value themselves, which is a genuinely confusing failure.

### `portal` — sweep

- Collectors down, with a count of affected resources.
- Collectors with no failover configured.
- Collector version spread.
- Resources in a `dead` state.
- Resources with alerting disabled.
- Alert rules with no escalation chain. These match alerts and send nothing.
- Duplicate rule priorities, where routing order is not guaranteed.
- Escalation chains with no recipients.

Run this first on an unfamiliar portal. It tells you where to point the
single-resource scenarios.

---

## 5. Exporting findings

```bash
# Markdown table, good for pasting into a ticket or engagement record
python3 lm_diagnose.py device ACME-APP-01 --out findings.md

# JSON, for piping into another tool
python3 lm_diagnose.py portal --json
```

A full run log is written to `lm_diagnose.log` in the working directory.

---

## 6. Suggested triage order

When a customer reports "monitoring is not working", work outwards:

1. `portal` — is something broken account-wide?
2. `collector <name>` — is the Collector carrying that resource healthy?
3. `device <name>` — is the resource discovered and collecting?
4. `credentials <name>` — if discovery is empty, this is usually why.
5. `alerting <name> --datasource X --datapoint Y` — if data is fine but nobody
   was told.

`all <name>` runs steps 3 to 5 together when you already know which resource.

---

## 7. Known limits

- Alert rule matching is a **simulation**. It reproduces the documented matching
  behaviour for DataSource, datapoint, severity, device and group scope. It does
  not evaluate resource-property filters on rules, so a rule using those may match
  in the portal when this tool says it would not. The tool reports what it checked,
  so treat a rule-match result as strong evidence rather than proof.
- Data freshness samples up to three instances of one DataSource, not everything.
  It is a probe to confirm collection works, not an audit of every datapoint.
- Collector saturation metrics depend on the Collector host being in monitoring.
  The tool reports when it could not read them instead of assuming health.
- The staleness window is 30 minutes. A DataSource on a longer collection interval
  may be reported stale while behaving normally. Check the interval before acting.

---

## 8. Pushing updates from iPad (iSH)

```bash
cd ~/infosys-claude-scripts
git add .
git commit -m "Describe your change"
git push origin main
```

Paste your personal access token as the password. It will not display as you
paste — that is expected.
