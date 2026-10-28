# Portal Maintenance Checks

**What it does:** Runs the daily and weekly health checks on a LogicMonitor portal (down Collectors, dead devices, old alerts, devices missing standard settings, alert routing) and saves a report. It only reads; it changes nothing.

**File to run:** `maintenance_report.py`

The other files are helpers used by this script. Do not run or edit them.

**What you need:**
- Python 3.10 or newer (nothing else to install).
- A LogicMonitor API Access ID and Access Key whose role has **View** on Resources, Collectors, Alert rules and Escalation chains (View on Users is optional).
- Your portal name (`acme` for `https://acme.logicmonitor.com`).

**How to run:**
1. Open a terminal in this folder.
2. Enter your portal details, replacing the values in `< >` (they last until you close the terminal):
   - Windows: `$env:LM_ACCOUNT='<portal-name>'; $env:LM_ACCESS_ID='<access-id>'; $env:LM_ACCESS_KEY='<access-key>'`
   - Mac/Linux: `export LM_ACCOUNT='<portal-name>' LM_ACCESS_ID='<access-id>' LM_ACCESS_KEY='<access-key>'`
3. Run the checks:
   - Windows: `py maintenance_report.py`
   - Mac/Linux: `python3 maintenance_report.py`

**What you get:** The results on screen (critical, warn, info, ok), and the same report saved as `.md`, `.json` and `.csv` files in a `reports` folder here.

**If something goes wrong:**
- `Config error: Missing LM_ACCOUNT ...` — do step 2 again in the same terminal window.
- `API error: ... HTTP 401` — wrong Access ID or Access Key, the token is disabled, or the computer clock is wrong.
- `API error: ... HTTP 0` — the portal could not be reached; check the portal name and your network or VPN.
