# LogicMonitor tools for Infosys: share packages

Each folder below is a self-contained package with its own `README.md`. Share a
folder, or its ready-made zip from `zips/`, on its own; nothing else is needed.

| Folder | What it is | Type |
|---|---|---|
| `01_LM_Diagnose` | Read-only diagnosis of one resource, Collector, alert route, or the whole portal | Python script |
| `02_LM_Device_Onboarding` | Bulk-add devices from a CSV (writes to the portal; dry run first) | Python script |
| `03_LM_Portal_Maintenance_Checks` | Read-only daily health and weekly standards-deviation checks, with saved reports | Python script |
| `04_LM_Thresholds_And_Alerting_Setup` | Create escalation chains, alert rules and thresholds from Excel (writes; dry run first) | Python script |
| `05_LM_Readonly_Helper_Kit` | Read-only lookups (health, alerts, devices, Collectors) for the terminal, Claude Code or Claude Desktop | Python script |
| `06_Docker_Container_DataSources_JSON` | Per-container Docker metrics over SSH (Linux) and WinRM (Windows) | LogicMonitor DataSource |
| `07_Docker_Swarm_NetScan` | Adds every Docker Swarm node as a resource, from the Swarm manager | NetScan (Groovy) |
| `08_Docker_Container_DataSources_XML` | Per-container Docker metrics via scripts on the Collector host (alternative to 06) | LogicMonitor DataSource |
| `09_LM_Health_Check_Report` | Read-only LM Envision Customer Health Check Word report, with a demo mode | Python script |
| `10_LM_Ops_Console_Web_Dashboard` | Read-only estate health checks in a local web page, with Excel/PDF/CSV export | Python web app |

Python packages need Python 3.9 or newer (3.10 for 03, 05 and 09) and a
LogicMonitor API token; each README lists the exact rights. Never put API keys
in files you share.
