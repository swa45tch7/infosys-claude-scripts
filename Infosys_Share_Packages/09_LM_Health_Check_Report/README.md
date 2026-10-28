# LogicMonitor Health Check Report

Builds the **LM Envision Customer Health Check** Word report (`.docx`) from a
LogicMonitor portal: executive summary, scorecard, good / weak / missing, module
usage, Edwin AI recommendation, 30/60/90-day plan and charts. It only reads
from the portal (signed `GET` requests) and never changes anything.

## What you need

- Python 3.10 or newer, plus `python-docx` and `matplotlib` (installed below).
- A LogicMonitor API token for an API-only user whose role has **View** only on:
  Resources, Websites, Dashboards, Reports, Collectors, LogicModules and Alert
  Settings. View on Users & Roles and on Audit Logs is optional; without it
  those rows say "Data unavailable".
- Your portal name: `acme` for `https://acme.logicmonitor.com`.

## How to run

Open a terminal in this folder. The script asks for the portal name, Access ID,
Access Key (hidden), customer name and an optional "prepared by" name.

Windows (PowerShell):

```powershell
py -m pip install -r requirements.txt
py lm_health_check_report.py --demo
py lm_health_check_report.py
```

Mac / Linux:

```bash
python3 -m pip install -r requirements.txt
python3 lm_health_check_report.py --demo
python3 lm_health_check_report.py
```

`--demo` builds a sample report from the made-up data in `demo_data/`, with no
portal needed. Useful options: `--customer "Acme Corp"`,
`--contracted-resources 1500`, `--output my_report.docx`, `--template
our_style.docx`. You can also set `LM_ACCOUNT`, `LM_ACCESS_ID` and
`LM_ACCESS_KEY` (or a `.env` file here, see `.env.example`) to skip the prompts.
`--help` lists everything.

## What you get

`reports/<Customer>_Infosys_LM_Envision_Health_Check_<DD_Mon_YYYY>.docx` in this
folder. `sample_output/Sample_Health_Check.docx` shows what it looks like. Text
in amber marked **[Consultant to complete: ...]** covers what the API cannot
see; review every rating before sending it to a customer.

## Common errors

| Message | Fix |
|---|---|
| `python-docx is not installed` | Run the `pip install -r requirements.txt` line above. |
| `Authentication failed (HTTP 401)` | Check the Access ID and Key, that the token is enabled, and the laptop clock. |
| `... cannot view Resources (HTTP 403)` | Give the token the read-only role described above. |
| `Could not write ... Is the file open in Word?` | Close the report in Word and run again. |
