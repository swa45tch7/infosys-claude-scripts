# START HERE

| Folder | What it does | When to use it | File to run |
|---|---|---|---|
| `Diagnose_Script` | Finds what is wrong with a device, Collector, alert routing or the whole portal | A device is not being monitored or not alerting as expected | `lm_diagnose.py` |
| `Device_Onboarding_Script` | Adds many devices at once from a CSV list | Onboarding a batch of new devices | `lm_device_onboard.py` |
| `Portal_Maintenance_Checks` | Runs the daily and weekly portal health checks and saves a report | Routine daily or weekly maintenance | `maintenance_report.py` |
| `Thresholds_And_Alerting_Setup` | Creates escalation chains, alert rules and alert thresholds from Excel | Setting up alerting for a new customer or site | `lm_escalation_chains.py`, then `lm_alert_rules.py`, then `lm_threshold_apply.py` |
| `Health_Check_Report` | Builds the customer health check report as a Word file | Preparing a customer health review | `lm_health_check_report.py` |
| `Docker_Container_DataSources` | Monitors each Docker container on Linux and Windows hosts | Adding Docker container monitoring | Import `Linux_Docker_Containers.json` and `Windows_Docker_Containers.json` into LogicMonitor |
| `Docker_Swarm_NetScan` | Adds every Docker Swarm node to LogicMonitor as a device | Onboarding a Docker Swarm cluster | Paste `Docker_Swarm_Onboard_Netscan.groovy` into a LogicMonitor NetScan |
| `LM_Readonly_Helper_Kit` | Quick look-ups: login check, alerts, devices, Collectors | Quick questions about the portal from a terminal | `lm_helper.py` |
| `Ops_Console_Web_Dashboard` | Portal health checks in a web page, with Excel, PDF and CSV export | You prefer clicking buttons to typing commands | `run.bat` (Windows) or `run.sh` (Mac/Linux) |

1. Install Python 3.10 or newer from python.org (on Windows, tick "Add python.exe to PATH").
2. Open a terminal in the folder you need (Windows: open the folder, type `powershell` in the address bar, press Enter. Mac: open Terminal, type `cd `, drag the folder onto the window, press Enter).
3. Follow the `README.md` in that folder.
