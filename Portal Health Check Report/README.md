# Portal Health Check Report

Creates a Word (`.docx`) **LM Envision portal health check report** for one
LogicMonitor portal, using only an API access ID and access key.

The report follows the Infosys LM Envision customer health check layout:
cover page with overall RAG status, executive summary, health-at-a-glance
tiles, an adoption and health scorecard, what is good / weak / missing,
module and capability usage, Edwin AI readiness, a 30/60/90-day action plan,
sources, and appendices with the supporting data.

**It is read-only.** It only sends HTTP GET requests. It does not create,
change, acknowledge or delete anything in the portal.

## What is in this folder

| File | What it is for |
|---|---|
| `lm_health_report.py` | The script you run. Collects the data and writes the report. |
| `run_health_check.bat` | Windows shortcut: double-click to install packages and run. |
| `run_health_check.command` | macOS shortcut: double-click to install packages and run. |
| `requirements.txt` | The two Python packages the script needs (pinned versions). |
| `.env.example` | Template for your credentials. Copy to `.env` and fill in. |
| `lm_client.py` | Talks to the LogicMonitor API: signing, rate limits, retries, paging. |
| `collect.py` | Decides which data to read from the portal. |
| `analysis.py` | Scores each area GREEN / AMBER / RED / UNKNOWN. Thresholds are at the top of the file. |
| `report_docx.py` | Builds the Word document. |
| `mock_portal.py` | A made-up portal ("Contoso Manufacturing") used by `--demo` and the tests. |
| `tests/` | Automated tests against the made-up portal. |
| `reports/` | Created on first run. Your reports are saved here. |

## Step 1 - Install Python (once per computer)

You need **Python 3.9 or newer**.

**Windows**

1. Go to <https://www.python.org/downloads/> and download the latest Python 3 installer.
2. Run it. On the first screen tick **"Add python.exe to PATH"**, then click **Install Now**.
3. Open **Command Prompt** (Start menu, type `cmd`) and check: `python --version`

**macOS**

1. Go to <https://www.python.org/downloads/> and download the macOS installer. Run it.
2. Open **Terminal** (Spotlight, type `Terminal`) and check: `python3 --version`

## Step 2 - Get this folder

Download or clone the repository, then open the `Portal Health Check Report`
folder. The double-click shortcuts in step 5 install the packages for you. To
install them by hand instead:

```text
Windows:  cd "Portal Health Check Report"
          python -m pip install -r requirements.txt

macOS:    cd "Portal Health Check Report"
          python3 -m pip install -r requirements.txt
```

Want to see a report before you have credentials? Run the demo (no portal is
contacted; the data is invented):

```text
Windows:  python lm_health_report.py --demo
macOS:    python3 lm_health_report.py --demo
```

## Step 3 - Create the API token and its minimum role (in LogicMonitor)

You need a LogicMonitor admin to do this once per portal.

1. **Role.** Go to **Settings > Users and Roles > Roles**. Use the built-in
   **readonly** role, or create a role named for example
   `Infosys Health Check (read-only)` with **View** (never Manage) on:
   - Dashboards (all), Resources (all groups), Websites (all), Reports (all)
   - Alerts (View)
   - Settings: Collectors, LogicModules, Alert Rules, Escalation Chains,
     Integrations, Users and Roles, Audit Logs (Access Logs), Account Information / Usage

   Anything you leave out is not a failure: that part of the report says
   "Could not be assessed: the API token's role does not have View permission".
2. **User.** Go to **Settings > Users and Roles > Users > Add**. Create an
   **API-only** user (for example `infosys-healthcheck-api`) with that role.
3. **Token.** Open that user, go to **API Tokens > + (Add)**, and choose the
   **LMv1** token type (not Bearer). Copy the **Access ID** and **Access Key**
   now: the key is shown only once.

## Step 4 - Give the script your credentials

The script needs three values:

| Name | Example | Where it comes from |
|---|---|---|
| `LM_ACCOUNT` | `acme` | The first part of your portal address `https://acme.logicmonitor.com` |
| `LM_ACCESS_ID` | `a1B2c3...` | Step 3 |
| `LM_ACCESS_KEY` | `x9Y8z7...` | Step 3 |
| `LM_DOMAIN` (optional) | `logicmonitor.com` | Only change for portals on a different domain |

Pick **one** of these:

- **Easiest: a `.env` file.** Copy `.env.example` to `.env` in this folder,
  open it in Notepad / TextEdit and fill in the values. Keep this file private.
- **Just run it.** If nothing is set, the script asks for each value. The
  access key is hidden while you type.
- **Environment variables** (current window only):

```text
Windows (Command Prompt):  set LM_ACCOUNT=acme
                           set LM_ACCESS_ID=a1B2c3...
                           set LM_ACCESS_KEY=x9Y8z7...

macOS (Terminal):          export LM_ACCOUNT=acme
                           export LM_ACCESS_ID=a1B2c3...
                           export LM_ACCESS_KEY=x9Y8z7...
```

The script never prints, logs or saves the access key.

## Step 5 - Run it

**Windows:** double-click `run_health_check.bat`.
**macOS:** double-click `run_health_check.command`. (The first time, macOS may
block it: right-click it, choose **Open**, then **Open** again.)

Or from Command Prompt / Terminal inside this folder:

```text
Windows:  python lm_health_report.py --customer "Acme Corp"
macOS:    python3 lm_health_report.py --customer "Acme Corp"
```

Useful options:

| Option | What it does |
|---|---|
| `--customer "Acme Corp"` | Name on the cover and in the file name (default: `LM_ACCOUNT`). |
| `--contract-devices 1500` | Contracted resources. Adds utilisation and headroom (the API does not expose contracts). |
| `--contract-logs-gb 500` | Contracted LM Logs GB, shown for reference. |
| `--contract-uptime-checks 50000` | Contracted LM Uptime checks, shown for reference. |
| `--out-dir "C:\Reports"` | Save somewhere other than `reports/`. |
| `--verbose` | Show rate-limit pacing and retries while it runs. |
| `--no-prompt` | Never ask for credentials (for scheduled jobs). |
| `--demo` | Use the invented demo portal. |

Most portals finish within a few minutes. Large portals take longer because
the script deliberately stays inside LogicMonitor's API rate limits.

## Where the report lands

In the `reports` folder inside this folder, named
`<Customer>_LM_Envision_Health_Check_<DD_Mon_YYYY>.docx`, for example
`Acme_Corp_LM_Envision_Health_Check_16_Nov_2026.docx`. The full path is printed
at the end of the run. Open it in Microsoft Word.

The report contains customer data. Do not commit it to git (the folder's
`.gitignore` already excludes `reports/` and `.docx` files) or share it outside
the engagement.

## What the report can and cannot measure

Everything is read from the LogicMonitor REST API v3. Some numbers on the
portal's **Usage** and **Portal Information** pages are not available in the
API. Those rows show **UNKNOWN** with the reason, and Appendix B lists every
check with its coverage. The run never fails because one area is unavailable.

| Report area | Coverage |
|---|---|
| Device footprint, dead devices, alerting disabled, stale discovery | Full |
| Instances per device | Full (from collector counters) |
| Alerts in the last 24 hours, open alerts, unacknowledged criticals, top alert sources | Full |
| Collectors: down, failover, fail-back, grouping, sizing, load balance, versions | Full |
| LogicModules: inventory, likely clones, modules applied to nothing | Full (clones and unused are heuristics) |
| Dashboards, widgets, reports and how recently reports ran | Counts only (views and recipients are not in the API) |
| Service Insights services, config-monitored devices, cloud and Kubernetes counts | Counts from `/metrics/usage` |
| Websites and external synthetic checks | Counts only |
| Alert rules and escalation chains (routing, rate limiting) | Full |
| Audit log in the last 24 hours, Active Discovery churn | Full |
| Users: active, inactive, never logged in | Full |
| Contract utilisation and headroom | Only if you pass `--contract-...` options |
| Billed rolling MTD resources, datapoint totals, monthly datapoint collection | **Not in the API** |
| LM Logs ingestion (MTD, projected), partitions, LogAlerts, tracked queries | **Not in the API** |
| LM Uptime checks executed versus entitlement | **Not in the API** |
| Dependent alert mapping, dynamic thresholds, LM Exchange update status | **Not in the API** |
| Edwin AI usage, business baselines (tickets, MTTR, SLA, cost) | **Not in the API** (readiness criteria are assessed instead) |

Scoring thresholds (for example "more than 3 alerts per device per day is RED")
are in `THRESHOLDS` at the top of `analysis.py` and are written into the
evidence text so reviewers can see why a row has its colour.

## How it treats the LogicMonitor API

- **GET only.** Any other request type is refused inside the script.
- **LMv1 signing** with the access ID and key, as LogicMonitor documents.
- **Rate limits.** Every response's `X-Rate-Limit-Limit`, `X-Rate-Limit-Remaining`
  and `X-Rate-Limit-Window` headers are read. When fewer than half the calls
  in a window remain, requests are spread out; when the quota is used up, the
  script waits for the window to reset.
- **HTTP 429** (too many requests): waits for `Retry-After` (or the window),
  then retries with growing, capped back-off and a little random jitter, up to
  6 attempts.
- **HTTP 500/502/503/504 and network drops:** retried with back-off.
- **Paging** at the API maximum of 1000 items per request, one request at a time.

## Troubleshooting

| Message | What it means | What to do |
|---|---|---|
| `Python was not found` / `'python' is not recognized` | Python is not installed or not on PATH. | Repeat step 1. On Windows, re-run the installer, choose **Modify**, and tick **Add Python to environment variables**. Try `py` instead of `python`. |
| `Missing packages. Run: python -m pip install ...` | Step 2 was skipped. | Run the command shown. Behind a proxy: `python -m pip install --proxy http://proxy:port -r requirements.txt`. |
| `Credentials problem: Missing LM_ACCESS_KEY ...` | A value is not set. | Fill in `.env` (step 4) or run without `--no-prompt` to be asked. |
| `HTTP 401` / `LogicMonitor rejected the credentials` | Wrong account, ID or key; token disabled; or the computer clock is wrong. | `LM_ACCOUNT` is only the prefix (`acme`, not the full URL). Re-copy ID and key. Check the token is enabled and is LMv1. Set the computer's date, time and time zone to automatic. |
| Rows saying `does not have View permission` (HTTP 403) | The token's role cannot see that area. | Add **View** for that area to the role (step 3) and run again, or accept the gap. |
| `rate-limiting this request after several retries` (HTTP 429) | Another tool is using the same portal's API quota heavily. | Run again later, or outside busy automation windows. The rest of the report is still produced. |
| `Could not reach acme.logicmonitor.com` | Network, VPN, proxy or a typo in `LM_ACCOUNT` / `LM_DOMAIN`. | Open `https://<account>.logicmonitor.com` in a browser from the same computer. If that fails, fix the network first. |
| `endpoint is not available` (HTTP 404) | Older portal or a module not licensed. | Nothing to do; the row explains it. |
| macOS: "cannot be opened because it is from an unidentified developer" | Gatekeeper blocks double-clicked scripts. | Right-click `run_health_check.command` > **Open** > **Open**, or run it from Terminal. |
| Report will not open in Word | Usually the file is still open from a previous run. | Close Word and run again; each run overwrites that day's file. |

## Tests

The tests use the invented portal in `mock_portal.py`, including simulated
HTTP 429, 5xx, 401 and 403 responses. They never contact a real portal.

```text
python -m unittest discover -s tests -v
```
