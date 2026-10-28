# LogicMonitor Read-only Helper Kit

Quick read-only lookups against a LogicMonitor portal from a terminal, or from
Claude Code / Claude Desktop: token check, current alerts, device search, one
device, Collectors. It only sends `GET` requests and never changes anything.

## What you need

- Python 3.10 or newer. No extra packages.
- A LogicMonitor API token whose role has **View** on Resources (devices and
  alerts) and Collectors.
- Three settings: `LM_ACCOUNT` (portal name, `acme` for `acme.logicmonitor.com`),
  `LM_ACCESS_ID`, `LM_ACCESS_KEY`. Optional `LM_DOMAIN` (default
  `logicmonitor.com`). Set them as below, or copy `.env.example` to `.env` in
  this folder and fill it in.

## How to run

Open a terminal in this folder (the one that contains `lm_infosys`).

Windows (PowerShell):

```powershell
$env:LM_ACCOUNT="acme"; $env:LM_ACCESS_ID="<access-id>"; $env:LM_ACCESS_KEY="<access-key>"
py -m lm_infosys health
py -m lm_infosys alerts --severity critical --size 20
py -m lm_infosys devices --query web01
py -m lm_infosys device 42
py -m lm_infosys collectors
```

Mac / Linux:

```bash
export LM_ACCOUNT=acme LM_ACCESS_ID='<access-id>' LM_ACCESS_KEY='<access-key>'
python3 -m lm_infosys health
python3 -m lm_infosys alerts --severity critical --size 20
python3 -m lm_infosys devices --query web01
```

Add `--json` to `alerts`, `devices`, `device` or `collectors` for raw JSON.

**Claude Code:** open this folder in Claude Code and use `/lm-health`,
`/lm-alerts`, `/lm-devices`, `/lm-device`, `/lm-collectors` (from
`.claude/commands`). **Claude Desktop:** `.mcp.json` registers the MCP server
`python3 -m lm_infosys.mcp`; set the `LM_*` values in that server's environment.

## What you get

Plain text on screen (ids, names, statuses), or JSON with `--json`. Exit code
`0` ok, `1` API error, `2` settings missing.

## Common errors

| Message | Fix |
|---|---|
| `Config error: Missing LM_ACCOUNT, ...` | Set the settings above in the same terminal window, or create `.env` here. |
| `No module named lm_infosys` | Run the command from this folder. |
| `API error: LogicMonitor API HTTP 401` | Wrong Access ID or Key, token disabled, or the laptop clock is wrong. |
