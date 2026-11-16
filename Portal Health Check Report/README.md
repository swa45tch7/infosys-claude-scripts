# Portal Health Check Report

Makes a Word report about the health of one LogicMonitor portal.
It only **reads** data. It changes nothing in LogicMonitor.

## How to start

You need: the zip file, and a LogicMonitor **access ID** and **access key**
(ask your LogicMonitor admin, or see "Create the login" below).

### Mac

1. Double-click the zip file. A folder opens.
2. Double-click **Start-Mac.command**.
   If a box says it "cannot be opened" or "Apple could not verify" it: click **Done**.
   Open **System Settings > Privacy & Security**, scroll down, click **Open Anyway**,
   then click **Open Anyway** again (type your Mac password if asked).
3. Answer the questions in the window. Press Enter to skip a question.
   Your report opens in Word when it is done.

### Windows

1. Right-click the zip file, click **Extract All**, then click **Extract**.
2. In the new folder, double-click **Start-Windows.bat**.
   If a blue box says "Windows protected your PC": click **More info**, then **Run anyway**.
   If a box asks "Do you want to run this file?": click **Run**.
3. Answer the questions in the window. Press Enter to skip a question.
   Your report opens in Word when it is done.

### Good to know

- **No Python?** The window says so and opens the download page. Install
  Python, then double-click the Start file again.
- **First run** takes about a minute to set up. Later runs skip this.
- **Your login** can be saved for next time. It is kept in a file named
  `.env` in this folder. Do not share that file.
- **Reports** are saved in the `reports` folder, inside this folder.
  Reports hold customer data. Do not share them outside the project.

## Create the login (LogicMonitor admin, once per portal)

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

---

# For technical users

## What is in this folder

| File | What it is for |
|---|---|
| `Start-Mac.command` | macOS / Linux launcher. Finds Python 3.9+, creates `.venv` here, installs `requirements.txt`, runs `app/easy_start.py`. |
| `Start-Windows.bat` | Windows launcher. Same steps, using `py -3` or `python`. |
| `requirements.txt` | The two Python packages the script needs (pinned versions). |
| `app/easy_start.py` | Guided questions (login, customer, contract devices), runs the report and opens it. |
| `app/lm_health_report.py` | Command-line entry point with all options. |
| `app/lm_client.py` | Talks to the LogicMonitor API: signing, rate limits, retries, paging. |
| `app/collect.py` | Decides which data to read from the portal. |
| `app/analysis.py` | Scores each area GREEN / AMBER / RED / UNKNOWN. Thresholds are at the top of the file. |
| `app/report_docx.py` | Builds the Word document. |
| `app/mock_portal.py` | A made-up portal ("Contoso Manufacturing") used by `--demo` and the tests. |
| `.env.example` | Template for credentials if you prefer to fill in `.env` by hand. |
| `tests/` | Automated tests against the made-up portal (not in the zip). |

## Command line

Python 3.9+ is required. From this folder:

```text
python -m pip install -r requirements.txt
python app/lm_health_report.py --customer "Acme Corp" --contract-devices 1500
python app/lm_health_report.py --demo        (invented portal, no login needed)
```

(On macOS use `python3`.) Credentials come from `LM_ACCOUNT`, `LM_ACCESS_ID`,
`LM_ACCESS_KEY` (optional `LM_DOMAIN`, default `logicmonitor.com`) in the
environment or a `.env` file in this folder. Without them the script asks,
hiding the key. The key is never printed, logged or written into the report.

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
at the end of the run, and the Start files open it for you.

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
are in `THRESHOLDS` at the top of `app/analysis.py` and are written into the
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
| `Python is needed. The download page will open now.` | Python 3.9+ is not installed. | Install it from the page that opens, then double-click the Start file again. |
| `Please unzip first` (Windows) | The Start file was opened from inside the zip. | Right-click the zip, click **Extract All**, then **Extract**. Use the new folder. |
| `the install failed` | The first-time install could not download packages. | Check internet, VPN or proxy. Behind a proxy: `.venv\Scripts\python -m pip install --proxy http://proxy:port -r requirements.txt`. |
| `LogicMonitor did not accept the login (HTTP 401)` | Wrong portal name, ID or key; token turned off; or the computer clock is wrong. | The portal name is only the first word (`acme`). Copy the ID and key again. Check the token is on and is LMv1. Set date, time and time zone to automatic. Type `y` to try again. |
| Rows saying `does not have View permission` (HTTP 403) | The token's role cannot see that area. | Add **View** for that area to the role and run again, or accept the gap. |
| `rate-limiting this request after several retries` (HTTP 429) | Another tool is using the same portal's API quota heavily. | Run again later. The rest of the report is still produced. |
| `Could not connect to acme.logicmonitor.com` | Network, VPN, proxy or a typo in the portal name / `LM_DOMAIN`. | Open `https://<portal>.logicmonitor.com` in a browser on the same computer. If that fails, fix the network first. |
| `endpoint is not available` (HTTP 404) | Older portal or a module not licensed. | Nothing to do; the row explains it. |
| `Could not save the report` | An older copy of today's report is open in Word. | Close it in Word and run again. |
| Wrong saved login | `.env` holds old values. | Delete the `.env` file in this folder and run again. |

## Tests

The tests use the invented portal in `app/mock_portal.py`, including simulated
HTTP 429, 5xx, 401 and 403 responses. They never contact a real portal.

```text
python -m unittest discover -s tests -v
```
