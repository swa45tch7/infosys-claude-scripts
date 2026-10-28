"""
Report exporters.

One run in memory becomes any of six output formats. Nothing is written to disk
until the user asks for it; every exporter returns bytes plus a filename.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any, Dict, List, Tuple

SEVERITY_FILL = {
    "critical": "FFD6D6",
    "warn": "FFF0CC",
    "ok": "E2F5E2",
    "info": "E8ECF2",
}
SEVERITY_LABEL = {
    "critical": "Critical",
    "warn": "Warning",
    "ok": "Healthy",
    "info": "For information",
}

FORMATS = ["xlsx", "csv", "json", "html", "pdf", "txt"]


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M")


def filename(company: str, fmt: str) -> str:
    safe = "".join(c for c in company if c.isalnum() or c in "-_") or "portal"
    return f"lm-health-{safe}-{_stamp()}.{fmt}"


# ------------------------------------------------------------------ JSON, CSV

def to_json(run: Dict[str, Any]) -> bytes:
    return json.dumps(run, indent=2, default=str).encode("utf-8")


def to_csv(run: Dict[str, Any]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["check", "severity", "field", "value"])
    for check in run["checks"]:
        for row in check["rows"]:
            for col in check["columns"]:
                if col == "severity":
                    continue
                writer.writerow([check["name"], row.get("severity", ""), col, row.get(col, "")])
    return buf.getvalue().encode("utf-8-sig")


def to_csv_wide(run: Dict[str, Any]) -> bytes:
    """One row per finding, union of all columns. Easier for pivot tables."""
    columns: List[str] = ["check", "severity"]
    for check in run["checks"]:
        for col in check["columns"]:
            if col not in columns:
                columns.append(col)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for check in run["checks"]:
        for row in check["rows"]:
            payload = {"check": check["name"]}
            payload.update(row)
            writer.writerow(payload)
    return buf.getvalue().encode("utf-8-sig")


# ---------------------------------------------------------------------- XLSX

def to_xlsx(run: Dict[str, Any]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="16283C")

    # Summary sheet
    ws = wb.active
    ws.title = "Summary"
    ws.append(["LogicMonitor estate health"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append(["Portal", run["company"]])
    ws.append(["Generated", run["finished_at"]])
    ws.append(["Checks run", len(run["checks"])])
    ws.append([])
    ws.append(["Check", "Critical", "Warning", "Healthy", "Information", "Findings"])
    for cell in ws[6]:
        cell.font = header_font
        cell.fill = header_fill
    for check in run["checks"]:
        counts = check["counts"]
        ws.append([
            check["name"], counts.get("critical", 0), counts.get("warn", 0),
            counts.get("ok", 0), counts.get("info", 0), len(check["rows"]),
        ])
    ws.column_dimensions["A"].width = 34
    for col in "BCDEF":
        ws.column_dimensions[col].width = 13
    ws.freeze_panes = "A7"

    # One sheet per check
    used: set = {"Summary"}
    for check in run["checks"]:
        title = check["name"][:28]
        n = 2
        while title in used:
            title = f"{check['name'][:25]}-{n}"
            n += 1
        used.add(title)
        sheet = wb.create_sheet(title)
        sheet.append(check["columns"])
        for cell in sheet[1]:
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(vertical="center")
        for row in check["rows"]:
            sheet.append([row.get(col, "") for col in check["columns"]])
            fill = SEVERITY_FILL.get(row.get("severity", "info"))
            if fill:
                sheet.cell(row=sheet.max_row, column=1).fill = PatternFill("solid", fgColor=fill)
        for idx, col in enumerate(check["columns"], start=1):
            longest = max([len(str(col))] + [len(str(r.get(col, ""))) for r in check["rows"][:200]])
            sheet.column_dimensions[get_column_letter(idx)].width = min(max(longest + 2, 10), 52)
        sheet.freeze_panes = "A2"
        if check["rows"]:
            sheet.auto_filter.ref = sheet.dimensions
        if check.get("notes"):
            sheet.append([])
            sheet.append(["Note", check["notes"]])

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


# ---------------------------------------------------------------------- HTML

def to_html(run: Dict[str, Any]) -> bytes:
    def esc(value: Any) -> str:
        return (
            str(value)
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )

    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        f"<title>LogicMonitor estate health — {esc(run['company'])}</title>",
        """<style>
body{font-family:Arial,Helvetica,sans-serif;margin:0;background:#f4f6f9;color:#16283c}
header{background:#16283c;color:#fff;padding:28px 32px}
header h1{margin:0 0 6px;font-size:22px;font-weight:700}
header p{margin:0;color:#b9c4d4;font-size:13px}
main{padding:24px 32px 60px;max-width:1200px}
section{background:#fff;border:1px solid #dce2ea;border-radius:6px;margin-bottom:22px;overflow:hidden}
h2{font-size:15px;margin:0;padding:14px 18px;border-bottom:1px solid #dce2ea;background:#fbfcfd}
.note{font-size:12px;color:#5a6a7e;padding:10px 18px;border-top:1px solid #eef1f5}
table{border-collapse:collapse;width:100%;font-size:12px}
th{text-align:left;background:#eef1f5;padding:8px 10px;font-weight:700;white-space:nowrap}
td{padding:7px 10px;border-top:1px solid #eef1f5;vertical-align:top}
tr.critical td:first-child{box-shadow:inset 4px 0 0 #c0392b}
tr.warn td:first-child{box-shadow:inset 4px 0 0 #d98b00}
tr.ok td:first-child{box-shadow:inset 4px 0 0 #2e8b57}
tr.info td:first-child{box-shadow:inset 4px 0 0 #7c8b9e}
.pill{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:700}
.pill.critical{background:#fbe0dd;color:#9e2a1d}.pill.warn{background:#fdf0d5;color:#8a5a00}
.pill.ok{background:#e2f5e2;color:#1f6b40}.pill.info{background:#e8ecf2;color:#47576b}
.empty{padding:16px 18px;color:#5a6a7e;font-size:13px}
</style></head><body>""",
        "<header><h1>LogicMonitor estate health</h1>",
        f"<p>{esc(run['company'])} &nbsp;·&nbsp; generated {esc(run['finished_at'])} "
        f"&nbsp;·&nbsp; {len(run['checks'])} checks</p></header><main>",
    ]
    for check in run["checks"]:
        counts = check["counts"]
        pills = " ".join(
            f"<span class='pill {k}'>{SEVERITY_LABEL[k]} {counts.get(k, 0)}</span>"
            for k in ("critical", "warn", "ok", "info") if counts.get(k)
        )
        parts.append(f"<section><h2>{esc(check['name'])} &nbsp; {pills}</h2>")
        if not check["rows"]:
            parts.append("<p class='empty'>Nothing to report.</p>")
        else:
            parts.append("<table><thead><tr>")
            parts += [f"<th>{esc(c.replace('_', ' '))}</th>" for c in check["columns"]]
            parts.append("</tr></thead><tbody>")
            for row in check["rows"]:
                sev = row.get("severity", "info")
                parts.append(f"<tr class='{esc(sev)}'>")
                for col in check["columns"]:
                    val = row.get(col, "")
                    if col == "severity":
                        val = SEVERITY_LABEL.get(str(val), val)
                    parts.append(f"<td>{esc(val)}</td>")
                parts.append("</tr>")
            parts.append("</tbody></table>")
        if check.get("notes"):
            parts.append(f"<p class='note'>{esc(check['notes'])}</p>")
        parts.append("</section>")
    parts.append("</main></body></html>")
    return "".join(parts).encode("utf-8")


# ----------------------------------------------------------------- plain text

def to_txt(run: Dict[str, Any]) -> bytes:
    lines = [
        "LogicMonitor estate health",
        f"Portal: {run['company']}",
        f"Generated: {run['finished_at']}",
        "",
        "Summary",
        "-------",
    ]
    for check in run["checks"]:
        c = check["counts"]
        lines.append(
            f"{check['name']}: {c.get('critical', 0)} critical, "
            f"{c.get('warn', 0)} warning, {len(check['rows'])} findings"
        )
    for check in run["checks"]:
        lines += ["", check["name"], "=" * len(check["name"])]
        if not check["rows"]:
            lines.append("Nothing to report.")
            continue
        for row in check["rows"][:200]:
            head = row.get(check["columns"][1]) if len(check["columns"]) > 1 else ""
            lines.append(
                f"[{SEVERITY_LABEL.get(row.get('severity', 'info'), '')}] "
                f"{head} - {row.get('finding', '')}"
            )
        if len(check["rows"]) > 200:
            lines.append(f"... {len(check['rows']) - 200} more findings in the full export.")
        if check.get("notes"):
            lines += ["", f"Note: {check['notes']}"]
    return ("\n".join(lines) + "\n").encode("utf-8")


# ----------------------------------------------------------------------- PDF

def to_pdf(run: Dict[str, Any]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        LongTable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, TableStyle,
    )

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=12 * mm,
        title=f"LogicMonitor estate health — {run['company']}",
    )
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontName="Helvetica-Bold",
                        fontSize=16, textColor=colors.HexColor("#16283C"))
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontName="Helvetica-Bold",
                        fontSize=11, textColor=colors.HexColor("#16283C"), spaceBefore=10)
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=8, leading=10)
    cell = ParagraphStyle("cell", parent=styles["BodyText"], fontSize=6.5, leading=8)

    story = [
        Paragraph("LogicMonitor estate health", h1),
        Paragraph(
            f"Portal {run['company']} &nbsp; | &nbsp; generated {run['finished_at']} "
            f"&nbsp; | &nbsp; {len(run['checks'])} checks",
            body,
        ),
        Spacer(1, 6 * mm),
    ]

    summary = [["Check", "Critical", "Warning", "Healthy", "Information", "Findings"]]
    for check in run["checks"]:
        c = check["counts"]
        summary.append([
            Paragraph(check["name"], cell), str(c.get("critical", 0)),
            str(c.get("warn", 0)), str(c.get("ok", 0)), str(c.get("info", 0)),
            str(len(check["rows"])),
        ])
    table = LongTable(summary, colWidths=[110 * mm, 22 * mm, 22 * mm, 22 * mm, 26 * mm, 22 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#16283C")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#DCE2EA")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story += [table, PageBreak()]

    row_colours = {
        "critical": colors.HexColor("#FBE0DD"),
        "warn": colors.HexColor("#FDF0D5"),
        "ok": colors.HexColor("#E9F6EC"),
        "info": colors.HexColor("#EEF1F5"),
    }

    for check in run["checks"]:
        story.append(Paragraph(check["name"], h2))
        if not check["rows"]:
            story += [Paragraph("Nothing to report.", body), Spacer(1, 4 * mm)]
            continue
        cols = check["columns"][:8]
        data = [[Paragraph(f"<b>{c.replace('_', ' ')}</b>", cell) for c in cols]]
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF1F5")),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#DCE2EA")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]
        for idx, row in enumerate(check["rows"][:400], start=1):
            values = []
            for col in cols:
                val = row.get(col, "")
                if col == "severity":
                    val = SEVERITY_LABEL.get(str(val), val)
                values.append(Paragraph(str(val)[:160], cell))
            data.append(values)
            colour = row_colours.get(row.get("severity", "info"))
            if colour:
                style.append(("BACKGROUND", (0, idx), (0, idx), colour))
        width = doc.width / len(cols)
        tbl = LongTable(data, colWidths=[width] * len(cols), repeatRows=1)
        tbl.setStyle(TableStyle(style))
        story.append(tbl)
        if len(check["rows"]) > 400:
            story.append(Paragraph(
                f"{len(check['rows']) - 400} further findings are in the workbook export.", body))
        if check.get("notes"):
            story.append(Paragraph(check["notes"], body))
        story.append(Spacer(1, 5 * mm))

    doc.build(story)
    return buf.getvalue()


# -------------------------------------------------------------------- routing

MEDIA_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
    "json": "application/json",
    "html": "text/html",
    "pdf": "application/pdf",
    "txt": "text/plain",
}


def export(run: Dict[str, Any], fmt: str) -> Tuple[bytes, str, str]:
    fmt = fmt.lower()
    builders = {
        "xlsx": to_xlsx,
        "csv": to_csv_wide,
        "json": to_json,
        "html": to_html,
        "pdf": to_pdf,
        "txt": to_txt,
    }
    if fmt not in builders:
        raise ValueError(f"Unsupported format: {fmt}")
    payload = builders[fmt](run)
    return payload, filename(run["company"], fmt), MEDIA_TYPES[fmt]
