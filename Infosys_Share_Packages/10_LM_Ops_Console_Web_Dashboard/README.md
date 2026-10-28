# LogicMonitor Ops Console (web dashboard)

A small web page that runs on your laptop and checks a LogicMonitor portal's
health: resource reachability, stale discovery, duplicates, missing properties,
Collector status/capacity/versions/failover, alert noise, unacknowledged
alerts, SDT windows, integrations, alert routing and API token hygiene. It only
reads from the portal and never changes anything.

## What you need

- Python 3.9 or newer (the start script installs the rest into a local `.venv`
  folder the first time; this needs internet access to PyPI).
- A LogicMonitor API token whose role has **View** on Resources, Collectors and
  Alert Settings (alert rules, escalation chains, integrations). View on Users &
  Roles is optional (API token check).
- Your portal name: `acme` for `https://acme.logicmonitor.com`.

## How to run

Open a terminal in this folder.

Windows: double-click `run.bat`, or in PowerShell:

```powershell
.\run.bat
```

Mac / Linux:

```bash
bash run.sh
```

Your browser opens `http://127.0.0.1:8787`. Enter the portal name, Access ID and
Access Key, click **Connect**, tick the checks you want, then **Run selected
checks**. Use **Disconnect and quit** to stop it. Options: `--port 8899` to use
another port, `--no-browser` to not open a tab (for example `bash run.sh --port 8899`).

## What you get

Results on the page, colour-coded Critical / Warning / Healthy / For
information, with search and filters. Export buttons download Excel, CSV, PDF,
HTML, JSON or Text. Nothing is saved unless you click an export. The page is
only reachable from your own laptop (127.0.0.1).

## Common errors

| Message | Fix |
|---|---|
| `The portal rejected these credentials` | Check the portal name, Access ID and Key, and that the token is enabled. |
| A check shows "This check did not complete" | The token's role lacks View for that area. |
| `address already in use` | Another copy is running. Close it, or use `--port 8899`. |
| `python3: command not found` / `'python' is not recognized` | Install Python from python.org (on Windows tick "Add python.exe to PATH"). |
