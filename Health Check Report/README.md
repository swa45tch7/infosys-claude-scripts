# Health check report

Builds the **LM Envision Customer Health Check** Word report (`.docx`) from a
LogicMonitor portal. It follows the Infosys health check layout: cover,
executive summary, scorecard, good / weak / missing, module usage, Edwin AI POV
recommendation, 30 / 60 / 90-day plan, sources and appendix charts.

Read-only: every call is a signed `GET` against REST API v3 through the shared
client in `Operations Portal Maintenance/lm_api.py`. Nothing in the portal is
created, changed or deleted.

## Run it

```bash
python3 -m pip install -r requirements.txt     # once: python-docx and matplotlib
python3 "Health Check Report/lm_health_check_report.py"
```

With no options it prompts for:

1. Portal name (`acme` for `acme.logicmonitor.com`; a full URL also works)
2. API Access ID
3. API Access Key (hidden, read with `getpass`)
4. Customer name for the title
5. Prepared by (optional, press Enter to skip)

Anything already set is not asked again. Values are read in this order: flags,
then environment variables, then the repo-root `.env`, then the prompt.

| Input | Flag | Environment |
|---|---|---|
| Portal | `--portal` / `--account` | `LM_ACCOUNT` |
| Access ID | `--access-id` | `LM_ACCESS_ID` |
| Access Key | `--access-key` (prefer the prompt or env var) | `LM_ACCESS_KEY` |
| Domain | `--domain` | `LM_DOMAIN` (default `logicmonitor.com`) |
| Customer | `--customer` | `LM_CUSTOMER` |
| Prepared by | `--prepared-by` | `LM_PREPARED_BY` |

Other options:

| Flag | Purpose |
|---|---|
| `--output PATH` | Output file. Default `Health Check Report/reports/<Customer>_Infosys_LM_Envision_Health_Check_<DD_Mon_YYYY>.docx` (date in IST). `reports/` is gitignored. |
| `--template PATH.docx` | Use your own Word file as the style base (styles, page setup and, if present, its header and footer). Its body text is discarded. |
| `--contracted-resources N` | Contracted resource entitlement. Turns the Core resource adoption row into a headroom calculation. |
| `--subtitle TEXT` | Cover subtitle (default `Portal health check \| <portal>`). |
| `--no-prompt` | Never prompt; exit `2` and name what is missing. For scheduled jobs. |
| `--demo` | Build from the bundled synthetic data in `tests/fixtures/api/`. No portal needed. |
| `--debug` | Show a traceback on unexpected errors. |

Exit codes: `0` report written, `1` portal or file error, `2` missing input or
missing Python package.

Each endpoint and each report section prints one progress line. If an endpoint
fails (for example HTTP 403 because the role cannot view users), collection
continues and the affected rows say **Data unavailable: <reason>**. Paging uses
`size`/`offset`; HTTP 429 and 5xx are retried with back-off that honours
`Retry-After` / `X-Rate-Limit-Window`.

The report is a draft for a consultant. Text in amber italics marked
**[Consultant to complete: …]** covers what the REST API cannot see (commercial
context, billing usage, Logs and Uptime consumption). Review every RAG rating
before sharing it with a customer.

A preview generated from synthetic data is in
[`sample_output/Sample_Health_Check.docx`](sample_output/Sample_Health_Check.docx).

## API token permissions

Create an **API-only user** with a custom role that has **View** rights only
(no Manage, no Acknowledge):

| Role area | Right | Used for |
|---|---|---|
| Resources (all groups) | View | Devices, groups, services, alerts on resources, SDTs, NetScans |
| Websites (all groups) | View | Website checks, website alerts |
| Dashboards (all groups) | View | Dashboard and widget counts |
| Reports (all groups) | View | Report counts and last run time |
| Settings → Collectors | View | Collector status, size, version, failover, groups |
| Settings → LogicModules | View | DataSources, EventSources, ConfigSources, PropertySources, TopologySources, JobMonitors, LogSources, AppliesTo functions, SysOID maps |
| Settings → Alert Settings | View | Alert rules, escalation chains, integrations |
| Settings → Users and Roles | View (optional) | Users, roles, API token hygiene |
| Settings → Audit Logs | View (optional) | Discovery and change activity |

Without the optional rights the report still builds; those rows show
**Data unavailable (HTTP 403)**. The script never reads API token access IDs,
user e-mail addresses or credential property values.

## Where each section comes from

**Automated** means it is calculated from the API. **Rule-based** means wording
and RAG are chosen by thresholds in `report_rules.json` and
`Operations Portal Maintenance/standards.json`. **Placeholder** means the
consultant completes it.

| Report section | API source (GET) | Type |
|---|---|---|
| Cover: overall status and condition, primary conclusion | Roll-up of the scorecard | Rule-based |
| Cover: assessment date, operating model, portal | Inputs and collection time (IST) | Automated |
| Executive summary paragraphs 1–2 | All datasets below | Automated + rule-based |
| Executive summary: strategic context, account risks | — | Placeholder |
| Health at a glance tiles | `/device/devices`, `/setting/collector/collectors` (instances), `/alert/alerts` (open and prior 24 h) | Automated |
| Immediate direction | Scorecard statuses | Rule-based |
| Scorecard: Core resource adoption | `/device/devices` (+ `--contracted-resources`) | Automated; billed usage is a placeholder |
| Scorecard: Device footprint | `/device/devices`, `/device/groups` | Automated + rule-based |
| Scorecard: Telemetry depth | `/setting/collector/collectors`, `/setting/datasources` (datapoints) | Automated; datapoint totals are a placeholder |
| Scorecard: Monthly datapoint collection | — | Placeholder (UNKNOWN) |
| Scorecard: Alerting health | `/alert/alerts`, `/setting/datasources` (dynamic thresholds) | Automated + rule-based |
| Scorecard: Dashboards and reporting | `/dashboard/dashboards`, `/dashboard/widgets`, `/report/reports` | Automated + rule-based |
| Scorecard: Service Insights | `/device/devices` (service resources) | Automated + rule-based |
| Scorecard: LM Logs | `/setting/logsources` | LogSource count automated; usage is a placeholder |
| Scorecard: LM Uptime | `/website/websites` | Check count automated; usage is a placeholder |
| Scorecard: Websites | `/website/websites` | Automated + rule-based |
| Scorecard: Collectors | `/setting/collector/collectors`, `/setting/collector/groups` | Automated + rule-based |
| Scorecard: LogicModules | `/setting/datasources`, `/setting/eventsources`, `/setting/configsources`, `/setting/propertyrules`, `/setting/topologysources`, `/setting/batchjobs`, `/setting/logsources`, `/setting/functions`, `/setting/oids`, `/setting/datasources/{id}/devices` | Automated + rule-based; update availability is not in the API |
| Scorecard: Config monitoring | `/setting/configsources` | Count automated; Config Devices is a placeholder |
| Scorecard: Cloud / SaaS / Kubernetes | `/device/devices`, `/device/groups` | Automated + rule-based |
| Scorecard: Alert routing and escalation (added) | `/setting/alert/rules`, `/setting/alert/chains`, `/setting/integrations` | Automated + rule-based |
| Scorecard: Scheduled downtime (added) | `/sdt/sdts` | Automated + rule-based |
| Scorecard: Access governance (added) | `/setting/admins`, `/setting/roles`, `/setting/admins/apitokens` | Automated + rule-based |
| Scorecard: Discovery and change activity (added) | `/setting/netscans`, `/setting/accesslogs` | Automated + rule-based |
| What is good / weak / missing; stickiness | Scorecard metrics | Rule-based; one retention-risk placeholder |
| Module and capability usage | Same datasets as the scorecard | Automated + rule-based; Edwin AI, Logs, Uptime and Config usage are placeholders |
| Edwin AI POV recommendation and use cases | Collectors, alert rules, devices, top alert sources from `/alert/alerts` | Rule-based; customer mandate is a placeholder |
| Execution model, success measures, decisions | — | Standard Infosys wording |
| 30 / 60 / 90-day plan | Scorecard findings | Rule-based |
| Sources and validation points | Collection log | Automated; sources [2] and [3] are placeholders |
| Appendix A charts | Resource, alert, website and collector datasets | Automated (matplotlib) |
| Appendix B: top alert sources, collector exceptions (added) | `/alert/alerts`, `/setting/collector/collectors` | Automated |
| Appendix C: data collection log (added) | Every endpoint above | Automated |

### Not reproduced from the API

- **Billing and entitlement usage** (rolling month-to-date resources, monthly
  datapoint collections, LM Logs GB, partitions, LogAlerts, tracked queries,
  LM Uptime checks executed versus contracted, Config Devices). These are on
  the portal Usage and Account Information pages, not in REST API v3. The rows
  are UNKNOWN with a placeholder, and the KPI tiles show Open alerts and
  Collectors instead of Logs and Uptime usage.
- **Portal screenshots** in Appendix A are replaced by charts drawn from API
  data. Paste Usage page screenshots in if needed.
- **Adoption evidence** such as dashboard views, report recipients' use and
  Edwin AI usage is not exposed through the API.
- **Commercial and stakeholder context** (account plan, POV status, internal
  updates) comes from people, not the portal.

Field-level assumptions worth confirming on a real portal: service resources
are `deviceType` 6; dynamic thresholds are read from datapoint
`adAdvSettingEnabled` / `*AdAdvSetting`; dependent alert mapping from the alert
`dependencyRole`; locally authored modules from `installationMetadata`. If a
portal rejects a `fields` list (HTTP 400) the call is retried without it.

## Files

| File | Purpose |
|---|---|
| `lm_health_check_report.py` | Command line entry point: inputs, prompts, progress, errors |
| `portal_data.py` | Endpoint list, read-only collection, fixture reader for tests and `--demo` |
| `assessment.py` | Metrics, scorecard rules and report wording |
| `charts.py` | Appendix charts (matplotlib) |
| `docx_writer.py` | Word layout, styles, header and footer |
| `report_rules.json` | Thresholds for RAG ratings and collection limits; tune per customer |

## Tests

```bash
python3 "Health Check Report/tests/test_health_check_report.py"
```

The tests use synthetic fixtures shaped like REST API v3 responses and mock the
HTTP layer. They do not call a live portal. The fixture data is invented; it
does not come from any customer portal.
