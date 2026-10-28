# Health Check Report

**What it does:** Builds the customer health check report for a LogicMonitor portal as a Word file: summary, scorecard, what is good, weak or missing, recommendations and charts. It only reads; it changes nothing.

**File to run:** `lm_health_check_report.py`

The other files are helpers used by this script. Do not run or edit them. `sample_output/Sample_Health_Check.docx` shows what a finished report looks like.

**What you need:**
- Python 3.10 or newer.
- A LogicMonitor API Access ID and Access Key whose role has **View** only on Resources, Websites, Dashboards, Reports, Collectors, LogicModules and Alert Settings (View on Users & Roles and Audit Logs is optional).
- Your portal name (`acme` for `https://acme.logicmonitor.com`) and the customer name.

**How to run:**
1. Open a terminal in this folder.
2. Install the add-ons (first time only):
   - Windows: `py -m pip install -r requirements.txt`
   - Mac/Linux: `python3 -m pip install -r requirements.txt`
3. Optional practice run with made-up data (no login needed):
   - Windows: `py lm_health_check_report.py --demo`
   - Mac/Linux: `python3 lm_health_check_report.py --demo`
4. For the real report, run the same command without `--demo` and answer the questions (portal name, Access ID, Access Key, customer name).

**What you get:** A Word file in the `reports` folder here, named like `<Customer>_Infosys_LM_Envision_Health_Check_<date>.docx`. Fill in the amber "[Consultant to complete: ...]" notes and review every rating before sending it to a customer.

**If something goes wrong:**
- `python-docx is not installed` — run step 2 again.
- `Authentication failed (HTTP 401)` — check the Access ID and Access Key, that the token is enabled, and the computer clock.
- `Could not write ... Is the file open in Word?` — close the report in Word and run again.
