# LogicMonitor Read-only Helper Kit

**What it does:** Quick look-ups in a LogicMonitor portal from a terminal: check the login works, list current alerts, find devices, show one device, list Collectors. It only reads; it changes nothing.

**File to run:** `lm_helper.py`

The other files are helpers used by this script. Do not run or edit them.

**What you need:**
- Python 3.10 or newer (nothing else to install).
- A LogicMonitor API Access ID and Access Key whose role has **View** on Resources and Collectors.
- Your portal name (`acme` for `https://acme.logicmonitor.com`).

**How to run:**
1. Open a terminal in this folder.
2. Enter your portal details, replacing the values in `< >` (they last until you close the terminal):
   - Windows: `$env:LM_ACCOUNT='<portal-name>'; $env:LM_ACCESS_ID='<access-id>'; $env:LM_ACCESS_KEY='<access-key>'`
   - Mac/Linux: `export LM_ACCOUNT='<portal-name>' LM_ACCESS_ID='<access-id>' LM_ACCESS_KEY='<access-key>'`
3. Run a look-up. Windows:
   ```
   py lm_helper.py health
   py lm_helper.py alerts --severity critical
   py lm_helper.py devices --query <part-of-device-name>
   py lm_helper.py device <device-id-or-name>
   py lm_helper.py collectors
   ```
   Mac/Linux: the same commands with `python3` instead of `py`.

**What you get:** Plain text on screen (IDs, names, statuses). Add `--json` to any look-up except `health` to get raw data instead.

**If something goes wrong:**
- `Config error: Missing LM_ACCOUNT ...` — do step 2 again in the same terminal window.
- `API error: ... HTTP 401` — wrong Access ID or Access Key, the token is disabled, or the computer clock is wrong.
- `API error: ... HTTP 403` — the token's role needs View on Resources and Collectors.
