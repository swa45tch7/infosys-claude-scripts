# Diagnose Script

**What it does:** Checks a LogicMonitor portal and tells you in plain words what is wrong with a device, a Collector, alert routing or the whole portal, and what to do next. It only reads; it changes nothing.

**File to run:** `lm_diagnose.py`

The other files are helpers used by this script. Do not run or edit them.

**What you need:**
- Python 3.10 or newer.
- A LogicMonitor API Access ID and Access Key whose role has **View** on Resources, Collectors, Alert rules and Escalation chains.
- Your portal name (`acme` for `https://acme.logicmonitor.com`).

**How to run:**
1. Open a terminal in this folder.
2. Install the add-ons (first time only):
   - Windows: `py -m pip install -r requirements.txt`
   - Mac/Linux: `python3 -m pip install -r requirements.txt`
3. Check the whole portal:
   - Windows: `py lm_diagnose.py portal`
   - Mac/Linux: `python3 lm_diagnose.py portal`
4. Or check one device, using its name as shown in the portal:
   - Windows: `py lm_diagnose.py device "<device-name>"`
   - Mac/Linux: `python3 lm_diagnose.py device "<device-name>"`
5. Type the portal name, Access ID and Access Key when asked (the key stays hidden).

**What you get:** A list of findings on screen, worst first (FAIL, WARN, OK, INFO), and a log file `lm_diagnose.log` in this folder.

**If something goes wrong:**
- `Missing dependency` — run step 2 again.
- `Authentication failed (HTTP 401)` — check the portal name, Access ID and Access Key, and that the token is enabled.
- `Permission denied (HTTP 403)` — the token's role is missing one of the View rights above.
