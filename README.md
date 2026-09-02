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
