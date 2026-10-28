# Ops Console Web Dashboard

**What it does:** Opens a web page on your own computer that checks a LogicMonitor portal's health (unreachable devices, Collector problems, alert noise, alert routing and more) and lets you export the results. It only reads; it changes nothing.

**File to run:** `run.bat` (Windows) or `run.sh` (Mac/Linux)

The other files are helpers used by this script. Do not run or edit them.

**What you need:**
- Python 3.10 or newer, and internet access the first time (it downloads its add-ons).
- A LogicMonitor API Access ID and Access Key whose role has **View** on Resources, Collectors and Alert Settings (View on Users & Roles is optional).
- Your portal name (`acme` for `https://acme.logicmonitor.com`).

**How to run:**
1. Start it:
   - Windows: double-click `run.bat`.
   - Mac/Linux: open a terminal in this folder and run `bash run.sh`
2. Your browser opens `http://127.0.0.1:8787`. Enter the portal name, Access ID and Access Key, then click **Connect**.
3. Tick the checks you want and click **Run selected checks**.
4. When you are done, click **Disconnect and quit**.

**What you get:** Colour-coded results on the page (Critical, Warning, Healthy, For information). The export buttons download Excel, CSV, PDF, HTML, JSON or Text. The page can only be opened on your own computer.

**If something goes wrong:**
- `The portal rejected these credentials` — check the portal name, Access ID and Access Key, and that the token is enabled.
- `address already in use` — it is already running; close the other window, or start it on another port: `bash run.sh --port 8899` (Windows: `run.bat --port 8899`).
- `'python' is not recognized` or `python3: command not found` — install Python from python.org (on Windows, tick "Add python.exe to PATH").
