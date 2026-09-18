# Infosys Claude — LogicMonitor helper

Read-only LogicMonitor commands that Infosys engineers can run from a local Claude Code or Claude Desktop session.

This repository does **not** create, update, or delete monitored resources.

## What you can run

| Claude Code command | What it does |
| --- | --- |
| `/lm-health` | Check API token and portal reachability |
| `/lm-alerts` | List current alerts |
| `/lm-devices` | Search devices by name |
| `/lm-device` | Show one device by id or name |
| `/lm-collectors` | List collectors |

Same actions from a terminal:

```bash
python3 -m lm_infosys health
python3 -m lm_infosys alerts --size 20
python3 -m lm_infosys alerts --severity critical
python3 -m lm_infosys devices --query web01
python3 -m lm_infosys device 42
python3 -m lm_infosys collectors
```

Requires Python 3.10+ and no extra packages.

## Operations portal maintenance

Estate-wide, read-only health and configuration-deviation scripts live in
[`Operations Portal Maintenance/`](Operations%20Portal%20Maintenance/). They are
for the operations team’s regular portal upkeep, not for changing monitoring.

How to run them is documented in
[`Operations Portal Maintenance/README.md`](Operations%20Portal%20Maintenance/README.md).
Short version:

```bash
cd "Operations Portal Maintenance"

# Daily health (collectors, dead resources, capacity, ageing alerts)
python3 health_check.py

# Weekly: compare the portal to Infosys / LogicMonitor standards
python3 config_deviation.py

# Dated markdown + JSON + CSV report under reports/
python3 maintenance_report.py

# or all three
bash run_ops_checks.sh
```

Use the same `LM_ACCOUNT`, `LM_ACCESS_ID`, and `LM_ACCESS_KEY` values as above.
Exit `0` if nothing is critical, `1` if any critical finding, `2` if credentials
are missing.

## Infosys setup (Claude Code)

1. Clone this repository and open the folder in Claude Code:

   ```bash
   git clone https://github.com/swa45tch7/infosys-claude-scripts.git
   cd infosys-claude-scripts
   ```

2. Create a LogicMonitor API token (**Settings → Users & Roles → API Tokens**) with **read** access only.

3. Copy `.env.example` to `.env` and fill in:

   ```
   LM_ACCOUNT=yourcompany
   LM_ACCESS_ID=...
   LM_ACCESS_KEY=...
   ```

   `LM_ACCOUNT` is the portal subdomain (`yourcompany.logicmonitor.com`).

4. Confirm the token works:

   ```bash
   python3 -m lm_infosys health
   ```

5. In Claude Code, type `/lm-health` (or any command in the table above).

`.env` is gitignored. Never commit tokens or paste them into chat.

## Claude Desktop (MCP)

`.mcp.json` registers a stdio MCP server. After cloning, add this repo as an MCP server in Claude Desktop, set the three `LM_*` variables in the MCP server environment, then call tools `lm_health`, `lm_alerts`, `lm_devices`, `lm_device`, and `lm_collectors`.

## Safety

- Read-only REST calls (`GET` only).
- LMv1 request signing; query parameters are not part of the signature, per [LogicMonitor REST API authentication](https://www.logicmonitor.com/support/rest-api-authentication).
- Missing credentials exit `2` with the env var names, not the values.
