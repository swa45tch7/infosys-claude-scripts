# Thresholds and Alerting Setup

**What it does:** Sets up alerting in LogicMonitor from Excel: escalation chains (who gets notified), alert rules (which alerts go to which chain) and alert thresholds per device. These scripts **make changes** in the portal, so always do the test run first.

**Files to run, in this order:** `lm_escalation_chains.py`, then `lm_alert_rules.py`, then `lm_threshold_apply.py`

The two `.xlsx` files are templates to copy and fill in. The other files are helpers used by these scripts. Do not run or edit them.

**What you need:**
- Python 3.10 or newer.
- A LogicMonitor API Access ID and Access Key whose role has **Manage** on Resources, Alert rules and Escalation chains, and **View** on Collectors.
- Your portal name (`acme` for `https://acme.logicmonitor.com`).
- Filled-in copies of the templates saved in this folder as `alerting.xlsx` (from `alerting_template.xlsx`) and `thresholds.xlsx` (from `thresholds_template.xlsx`). Each template has an Instructions sheet.

**How to run:**
1. Open a terminal in this folder.
2. Install the add-ons (first time only):
   - Windows: `py -m pip install -r requirements.txt`
   - Mac/Linux: `python3 -m pip install -r requirements.txt`
3. Test run (no login needed, changes nothing). Windows:
   ```
   py lm_escalation_chains.py --excel alerting.xlsx --sheet EscalationChains --dry-run
   py lm_alert_rules.py --excel alerting.xlsx --sheet AlertRules --dry-run
   py lm_threshold_apply.py --excel thresholds.xlsx --dry-run
   ```
   Mac/Linux: the same three commands with `python3` instead of `py`.
4. If the test looks right, run the same three commands again without `--dry-run`, in the same order. Each one asks for the portal name, Access ID and Access Key.

**What you get:** A results file next to each workbook (for example `alerting_chains_report.csv`), with one line per row saying created, updated, skipped, failed or dry-run, plus a log file.

**If something goes wrong:**
- `Permission denied (HTTP 403)` — the token's role needs Manage on the area named in the message.
- `Escalation chain not found` — run `lm_escalation_chains.py` first, or fix the chain name in the sheet.
- `has no discovered instances yet` — LogicMonitor has not finished scanning the device; wait a little and run again.
