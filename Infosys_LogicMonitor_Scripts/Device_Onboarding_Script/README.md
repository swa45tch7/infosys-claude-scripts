# Device Onboarding Script

**What it does:** Adds many devices to LogicMonitor at once from a CSV list, puts them in the right groups and assigns a Collector. This script **makes changes** in the portal, so always do the test run first.

**File to run:** `lm_device_onboard.py`

`devices_template.csv` is an example list to copy and fill in.

**What you need:**
- Python 3.10 or newer.
- A LogicMonitor API Access ID and Access Key whose role has **Manage** on Resources and **View** on Collectors.
- Your portal name (`acme` for `https://acme.logicmonitor.com`).
- Your device list: copy `devices_template.csv` to `devices.csv` in this folder and replace the example rows. Only the `name` column (IP address or DNS name) is required.

**How to run:**
1. Open a terminal in this folder.
2. Install the add-ons (first time only):
   - Windows: `py -m pip install -r requirements.txt`
   - Mac/Linux: `python3 -m pip install -r requirements.txt`
3. Test run (logs in and checks every row, but changes nothing):
   - Windows: `py lm_device_onboard.py --csv devices.csv --dry-run`
   - Mac/Linux: `python3 lm_device_onboard.py --csv devices.csv --dry-run`
4. Type the portal name, Access ID and Access Key when asked, then pick the Collector number from the list.
5. If the test looks right, run the same command again without `--dry-run`.

**What you get:** `devices_onboarding_report.csv` in this folder, with one line per device saying created, skipped, failed or dry-run and why, plus a log file.

**If something goes wrong:**
- `Permission denied (HTTP 403)` — the token's role needs Manage on Resources.
- `must contain a 'name' column` — the first row of the CSV must be the header row; compare with `devices_template.csv`.
- `collectorName '...' not found` — use the Collector name exactly as shown in the Collector list.
