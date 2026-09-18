# Operations portal maintenance

Read-only health checks and configuration-deviation reports for Infosys operations
teams that look after a LogicMonitor portal day to day.

This folder is self-contained. It does **not** create, update, or delete anything
in the portal. Existing folders in this repository are left untouched.

## What you run

| Script | When to use it |
|---|---|
| `health_check.py` | Daily: is the portal reachable, are collectors up, are resources dead, is collector sizing within LogicMonitor published capacities, are alerts ageing? |
| `config_deviation.py` | Weekly: does the estate still match Infosys / LogicMonitor standards (properties, grouping, failover, alert routing, SDT, tokens)? |
| `maintenance_report.py` | Scheduled job: runs both suites and writes markdown, JSON and CSV under `reports/`. |
| `run_ops_checks.sh` | One command that runs all three. |

```bash
cd "Operations Portal Maintenance"

python3 health_check.py
python3 config_deviation.py
python3 maintenance_report.py --out-dir reports

# or
bash run_ops_checks.sh
```

JSON for a pipeline:

```bash
python3 health_check.py --json
python3 config_deviation.py --json
```

Exit codes: `0` if nothing critical, `1` if any critical finding, `2` if credentials
are missing. Warnings do not fail the run; they still appear in the report.

Python 3.10+ and no extra packages. Same credentials as the rest of this
repository (`LM_ACCOUNT`, `LM_ACCESS_ID`, `LM_ACCESS_KEY` in the environment or a
repo-root `.env`). The token needs **View** on Resources, Collectors, Alert rules
and Escalation chains. User-management View is optional and only used for token
hygiene.

## Standards the checks use

`standards.json` is the source of truth. Tune it per customer, then leave it
alone. The defaults are:

| Area | Standard |
|---|---|
| Collector capacity | LogicMonitor sizing starting points by collector size (nano 25 devices / 1500 instances through extra_large 1000 / 60000). Warn at 75% of that capacity, critical at 90%. |
| Collector failover | Any collector with assigned resources must have a backup collector and fail-back enabled. |
| Collector versions | Not more than two major versions behind the newest collector already in the portal. |
| Resource properties | Every resource should carry `location`, `owner`, `environment`, `service.tier`. Infosys also recommends `infosys.customer`. |
| Category credentials | Presence of the credential properties implied by `system.categories` (for example `snmp.community` for SNMP). Values are never printed. |
| Grouping | Resources must not sit only in the root group. |
| Discovery | Auto-properties older than 14 days are stale. |
| Alert routing | Every alert rule binds a chain that has destinations. Priorities must be unique. Escalation interval `0` is a deviation. |
| Ack SLA | Critical 15 minutes, error 60 minutes, warn 4 hours. |
| SDT | Open-ended windows are critical; windows longer than 30 days need review. |
| API tokens | Enabled tokens unused for 90 days should be rotated or disabled. |

Collector percentages are starting points. Confirm them against LogicMonitor
collector sizing for the version installed in that portal before you add hardware.

## Suggested operations cadence

1. **Daily** — `health_check.py`. Act on down collectors and dead resources first.
2. **After change windows** — `config_deviation.py` to catch properties, grouping
   and routing that drifted from the standard.
3. **Weekly** — `maintenance_report.py`. Attach the markdown file to the
   operations record.

When something looks wrong on a single resource, use the existing
`Diagnose scripts` folder. This kit is the estate-wide sweep; that kit is the
deep dive.

## Tests

```bash
python3 tests/test_ops_checks.py
```

The tests use a fixture snapshot. They do not call a live portal.
