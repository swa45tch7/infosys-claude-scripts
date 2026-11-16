"""Render an Assessment as a Word report shaped like the Infosys LM Envision health check."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional, Sequence

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from analysis import AMBER, GREEN, RED, UNKNOWN, Assessment, capability_rows

NAVY = "071C4F"
NAVY2 = "0D2A6B"
BLUE = "006FF2"
INK = "1E2430"
GREY = "586273"
PALE = "F5F9FF"
PALE_BLUE = "EAF3FF"
TILE = "F3F5F7"
BORDER = "D9DEE5"
STATUS_STYLE = {
    GREEN: ("E7F4E8", "2E7D32"),
    AMBER: ("FFF3D6", "A15C00"),
    RED: ("FCE8E6", "B3261E"),
    UNKNOWN: ("ECEFF1", "5F6368"),
}
HEALTH_STATUS = {"Strong": GREEN, "Developing": AMBER, "At risk": RED, "Unknown": UNKNOWN}
CONTENT_WIDTH_CM = 17.7


def report_filename(portal_name: str, when: datetime) -> str:
    safe = re.sub(r"[^A-Za-z0-9]+", "_", portal_name).strip("_") or "Portal"
    return f"{safe}_LM_Envision_Health_Check_{when.strftime('%d_%b_%Y')}.docx"


# --------------------------------------------------------------------------- low-level helpers


_SCHEMA_ORDER = {
    "tblPr": ["tblStyle", "tblpPr", "tblOverlap", "bidiVisual", "tblStyleRowBandSize", "tblStyleColBandSize", "tblW",
              "jc", "tblCellSpacing", "tblInd", "tblBorders", "shd", "tblLayout", "tblCellMar", "tblLook"],
    "trPr": ["cnfStyle", "divId", "gridBefore", "gridAfter", "wBefore", "wAfter", "cantSplit", "trHeight",
             "tblHeader", "tblCellSpacing", "jc", "hidden"],
    "tcPr": ["cnfStyle", "tcW", "gridSpan", "hMerge", "vMerge", "tcBorders", "shd", "noWrap", "tcMar",
             "textDirection", "tcFitText", "vAlign", "hideMark"],
}


def _normalise(doc) -> None:
    """Sort table property children into OOXML schema order so Word opens the file cleanly."""
    for parent, order in _SCHEMA_ORDER.items():
        rank = {qn(f"w:{name}"): idx for idx, name in enumerate(order)}
        for el in doc.element.body.iter(qn(f"w:{parent}")):
            children = list(el)
            for child in children:
                el.remove(child)
            for child in sorted(children, key=lambda c: rank.get(c.tag, len(order))):
                el.append(child)


def _rgb(hex_color: str) -> RGBColor:
    return RGBColor.from_string(hex_color)


def _shade(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    for old in tc_pr.findall(qn("w:shd")):
        tc_pr.remove(old)
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    tc_pr.append(shd)


def _cell_margins(cell, top=80, bottom=80, left=110, right=110) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    mar = OxmlElement("w:tcMar")
    for side, value in (("top", top), ("bottom", bottom), ("left", left), ("right", right)):
        el = OxmlElement(f"w:{side}")
        el.set(qn("w:w"), str(value))
        el.set(qn("w:type"), "dxa")
        mar.append(el)
    tc_pr.append(mar)


def _borders(table, color: Optional[str] = BORDER) -> None:
    tbl_pr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        if color:
            el.set(qn("w:val"), "single")
            el.set(qn("w:sz"), "4")
            el.set(qn("w:color"), color)
        else:
            el.set(qn("w:val"), "nil")
        borders.append(el)
    tbl_pr.append(borders)


def _widths(table, widths_cm: Sequence[float]) -> None:
    """Fix column widths; Word and LibreOffice need the grid, layout and cell widths to agree."""
    table.autofit = False
    tbl_pr = table._tbl.tblPr
    twips = [int(Cm(w).twips) for w in widths_cm]
    for tag, attrs in (("w:tblW", {"w:w": str(sum(twips)), "w:type": "dxa"}), ("w:tblLayout", {"w:type": "fixed"})):
        for old in tbl_pr.findall(qn(tag)):
            tbl_pr.remove(old)
        el = OxmlElement(tag)
        for key, value in attrs.items():
            el.set(qn(key), value)
        tbl_pr.append(el)
    grid = table._tbl.tblGrid
    for col, width in zip(grid.findall(qn("w:gridCol")), twips):
        col.set(qn("w:w"), str(width))
    for row in table.rows:
        tr_pr = row._tr.get_or_add_trPr()
        if tr_pr.find(qn("w:cantSplit")) is None:
            tr_pr.append(OxmlElement("w:cantSplit"))
        for idx, width in enumerate(widths_cm):
            if idx < len(row.cells):
                row.cells[idx].width = Cm(width)


def _repeat_header(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    el = OxmlElement("w:tblHeader")
    el.set(qn("w:val"), "true")
    tr_pr.append(el)


def _write(cell_or_par, text: str, size: float = 9, color: str = INK, bold: bool = False,
           align=None, new_paragraph: bool = False, space_after: float = 0):
    if hasattr(cell_or_par, "paragraphs"):
        par = cell_or_par.add_paragraph() if new_paragraph else cell_or_par.paragraphs[0]
    else:
        par = cell_or_par
    run = par.add_run(text)
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = _rgb(color)
    run.font.name = "Arial"
    par.paragraph_format.space_after = Pt(space_after)
    par.paragraph_format.space_before = Pt(0)
    if align is not None:
        par.alignment = align
    return par


def _para(doc, text: str, size: float = 10.5, color: str = INK, bold: bool = False, space_after: float = 6,
          style: Optional[str] = None):
    par = doc.add_paragraph(style=style) if style else doc.add_paragraph()
    _write(par, text, size, color, bold, space_after=space_after)
    return par


def _subtitle(doc, text: str) -> None:
    _para(doc, text, 9.5, GREY, space_after=8)


def _status_cell(cell, status: str, size: float = 8.5, label: Optional[str] = None) -> None:
    fill, ink = STATUS_STYLE.get(status, STATUS_STYLE[UNKNOWN])
    _shade(cell, fill)
    _write(cell, label or status, size, ink, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER


def _data_table(doc, header: Sequence[str], rows: Iterable[Sequence[str]], widths: Sequence[float],
                status_col: Optional[int] = None, health_col: Optional[int] = None):
    rows = list(rows)
    table = doc.add_table(rows=1 + len(rows), cols=len(header))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    _borders(table)
    for idx, title in enumerate(header):
        cell = table.rows[0].cells[idx]
        _shade(cell, NAVY)
        _cell_margins(cell)
        _write(cell, title, 8.5, "FFFFFF", bold=True)
    _repeat_header(table.rows[0])
    for r, values in enumerate(rows, start=1):
        for c, value in enumerate(values):
            cell = table.rows[r].cells[c]
            _cell_margins(cell)
            if c == status_col:
                _status_cell(cell, str(value))
            elif c == health_col:
                _status_cell(cell, HEALTH_STATUS.get(str(value), UNKNOWN), label=str(value))
            elif "\n" in str(value):
                for i, line in enumerate(str(value).split("\n")):
                    _write(cell, f"\u2022 {line}", 8.5, INK, new_paragraph=i > 0, space_after=2)
            else:
                _write(cell, str(value), 8.5, INK, bold=(c == 0))
    _widths(table, widths)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return table


def _callout(doc, title: str, body: str, bar: str = BLUE, fill: str = PALE) -> None:
    table = doc.add_table(rows=1, cols=2)
    _borders(table, None)
    left, right = table.rows[0].cells
    _shade(left, bar)
    _shade(right, fill)
    _cell_margins(right, 140, 140, 200, 200)
    _write(right, title, 10.5, NAVY, bold=True, space_after=3)
    _write(right, body, 10, INK, new_paragraph=True)
    _widths(table, [0.25, CONTENT_WIDTH_CM - 0.25])
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def _status_banner(doc, status: str, text: str) -> None:
    table = doc.add_table(rows=1, cols=2)
    _borders(table, None)
    left, right = table.rows[0].cells
    _status_cell(left, status, size=11)
    fill, _ = STATUS_STYLE.get(status, STATUS_STYLE[UNKNOWN])
    _shade(right, fill)
    _cell_margins(right, 140, 140, 200, 200)
    _write(right, text, 10, INK)
    right.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    _widths(table, [2.6, CONTENT_WIDTH_CM - 2.6])
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def _columns(doc, columns: Sequence[tuple], fill: str = PALE) -> None:
    table = doc.add_table(rows=1, cols=len(columns))
    _borders(table, "FFFFFF")
    width = CONTENT_WIDTH_CM / len(columns)
    for cell, (title, color, bullets) in zip(table.rows[0].cells, columns):
        _shade(cell, fill)
        _cell_margins(cell, 140, 140, 160, 160)
        _write(cell, title, 10, color, bold=True, space_after=4)
        for bullet in bullets:
            _write(cell, f"\u2022 {bullet}", 8.5, INK, new_paragraph=True, space_after=3)
    _widths(table, [width] * len(columns))
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def _tiles(doc, tiles: Sequence[tuple], per_row: int = 3) -> None:
    rows = [tiles[i:i + per_row] for i in range(0, len(tiles), per_row)]
    table = doc.add_table(rows=len(rows), cols=per_row)
    _borders(table, "FFFFFF")
    for r, chunk in enumerate(rows):
        for c, tile in enumerate(chunk):
            value, suffix, label, sub = tile
            cell = table.rows[r].cells[c]
            _shade(cell, PALE)
            _cell_margins(cell, 140, 140, 160, 160)
            par = _write(cell, value, 18, BLUE, bold=True)
            if suffix:
                _write(par, f" {suffix}", 10, GREY, bold=True)
            _write(cell, label, 9.5, NAVY, bold=True, new_paragraph=True, space_after=1)
            _write(cell, sub, 8, GREY, new_paragraph=True)
    _widths(table, [CONTENT_WIDTH_CM / per_row] * per_row)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def _numbered(doc, items: Sequence[str]) -> None:
    for idx, item in enumerate(items, start=1):
        _para(doc, f"{idx}. {item}", 10, INK, space_after=4)


def _bullets(doc, items: Sequence[str]) -> None:
    for item in items:
        par = doc.add_paragraph(style="List Bullet")
        _write(par, item, 10, INK, space_after=3)


def _source(doc, ref: str, title: str, body: str) -> None:
    table = doc.add_table(rows=1, cols=2)
    _borders(table, None)
    left, right = table.rows[0].cells
    _shade(left, PALE_BLUE)
    _shade(right, PALE_BLUE)
    _write(left, ref, 9, BLUE, bold=True)
    _cell_margins(right, 100, 100, 140, 140)
    _write(right, title, 9, NAVY, bold=True, space_after=2)
    _write(right, body, 8.5, GREY, new_paragraph=True)
    _widths(table, [1.0, CONTENT_WIDTH_CM - 1.0])
    doc.add_paragraph().paragraph_format.space_after = Pt(1)


def _caption(doc, text: str) -> None:
    par = doc.add_paragraph(style="Caption")
    _write(par, text, 8.5, GREY, space_after=10)


def _setup(doc, portal_name: str) -> None:
    section = doc.sections[0]
    section.orientation = WD_ORIENT.PORTRAIT
    section.page_width, section.page_height = Cm(21.0), Cm(29.7)
    for side in ("left_margin", "right_margin"):
        setattr(section, side, Cm(1.65))
    section.top_margin, section.bottom_margin = Cm(1.8), Cm(1.6)

    styles = doc.styles
    normal = styles["Normal"]
    normal.font.name = "Arial"
    normal.font.size = Pt(10.5)
    normal.font.color.rgb = _rgb(INK)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), "Arial")
    for name, size, color in (("Title", 30, NAVY), ("Heading 1", 20, NAVY), ("Heading 2", 13, NAVY2)):
        style = styles[name]
        style.font.name = "Arial"
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = _rgb(color)
        rpr = style.element.get_or_add_rPr()
        fonts = rpr.find(qn("w:rFonts"))
        if fonts is None:
            fonts = OxmlElement("w:rFonts")
            rpr.append(fonts)
        for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
            fonts.set(qn(attr), "Arial")
        for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
            if fonts.get(qn(attr)) is not None:
                del fonts.attrib[qn(attr)]
    styles["Heading 1"].paragraph_format.space_before = Pt(14)
    styles["Heading 1"].paragraph_format.space_after = Pt(4)
    styles["Heading 2"].paragraph_format.space_before = Pt(10)
    styles["Heading 2"].paragraph_format.space_after = Pt(4)
    title_pr = styles["Title"].element.get_or_add_pPr()
    for border in title_pr.findall(qn("w:pBdr")):
        title_pr.remove(border)

    header = section.header.paragraphs[0]
    _write(header, f"LOGICMONITOR  |  {portal_name.upper()} HEALTH CHECK", 8, GREY, bold=True)

    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    _write(footer, "Page ", 8, GREY)
    run = footer.add_run()
    for kind, text in (("begin", None), (None, "PAGE"), ("end", None)):
        if kind:
            el = OxmlElement("w:fldChar")
            el.set(qn("w:fldCharType"), kind)
        else:
            el = OxmlElement("w:instrText")
            el.set(qn("xml:space"), "preserve")
            el.text = text
        run._r.append(el)
    run.font.size = Pt(8)


# --------------------------------------------------------------------------- report


def build_report(a: Assessment, out_dir: Path) -> Path:
    facts = a.facts
    when = facts.collected_at.astimezone()
    doc = Document()
    _setup(doc, facts.portal_name)

    banner = doc.add_table(rows=1, cols=2)
    _borders(banner, None)
    for cell, text, align in zip(banner.rows[0].cells, ("LOGICMONITOR", "PORTAL HEALTH CHECK"),
                                 (WD_ALIGN_PARAGRAPH.LEFT, WD_ALIGN_PARAGRAPH.RIGHT)):
        _shade(cell, NAVY)
        _cell_margins(cell, 120, 120, 200, 200)
        _write(cell, text, 9, "FFFFFF", bold=True, align=align)
    _widths(banner, [CONTENT_WIDTH_CM / 2] * 2)

    _para(doc, facts.portal_name.upper(), 11, BLUE, bold=True, space_after=0).paragraph_format.space_before = Pt(18)
    title = doc.add_paragraph(style="Title")
    _write(title, "LM Envision Portal Health Check", 30, NAVY, bold=True, space_after=4)
    _para(doc, f"Automated read-only assessment  |  {facts.portal_host}", 11, GREY, space_after=12)

    _status_banner(doc, a.overall, a.overall_summary)
    _callout(doc, a.conclusion_title, a.conclusion)

    meta = [
        ("Assessment date", when.strftime("%d %B %Y, %H:%M %Z").strip()),
        ("Portal", f"{facts.portal_name} ({facts.portal_host})"),
        ("Data source", f"LogicMonitor REST API v3, read-only GET requests ({facts.request_count} requests; "
                        f"{facts.throttle_events} rate-limit back-offs)"),
        ("Classification", "Internal - contains portal configuration data"),
    ]
    for label, value in meta:
        par = doc.add_paragraph()
        _write(par, f"{label}  ", 9.5, GREY, bold=True, space_after=2)
        _write(par, value, 9.5, INK, space_after=2)

    doc.add_heading("Executive summary", level=1)
    _subtitle(doc, "Current-state assessment and immediate direction")
    for text in a.summary_paragraphs:
        _para(doc, text + " [1]", 10.5)
    doc.add_heading("Health at a glance", level=2)
    _tiles(doc, a.tiles)
    doc.add_heading("Immediate direction", level=2)
    _numbered(doc, a.direction)

    doc.add_heading("Portal adoption and health scorecard", level=1)
    _subtitle(doc, "Point-in-time API data; real-time portal metrics may differ from billed usage")
    _data_table(doc, ["Area", "Status", "Evidence and implication"],
                [(c.area, c.status, c.evidence) for c in a.checks], [4.0, 2.2, 11.5], status_col=1)
    reds = [c.area for c in a.checks if c.status == RED]
    greens = [c.area for c in a.checks if c.status == GREEN]
    interp = []
    if greens:
        interp.append("Healthy: " + ", ".join(greens) + ".")
    if reds:
        interp.append("Value leakage is concentrated in " + ", ".join(reds) + ".")
    interp.append("UNKNOWN rows are not exposed by the REST API; see Appendix B.")
    _callout(doc, "Scorecard interpretation", " ".join(interp))

    doc.add_heading("What is good, what is weak and what is missing", level=1)
    _subtitle(doc, "Derived from the scorecard checks")
    good = [g for c in a.checks for g in c.good] or ["No clear strengths could be evidenced from API data."]
    weak = [w for c in a.checks for w in c.weak] or ["No material weaknesses detected from API data."]
    missing = [m for c in a.checks for m in c.missing] or ["Nothing outstanding."]
    _columns(doc, [("WHAT IS GOOD", STATUS_STYLE[GREEN][1], good[:8]),
                   ("WHAT IS WEAK", STATUS_STYLE[RED][1], weak[:8]),
                   ("WHAT IS MISSING", STATUS_STYLE[AMBER][1], missing[:8])])
    doc.add_heading("Stickiness and account economics", level=2)
    _columns(doc, [("Existing anchors", STATUS_STYLE[GREEN][1], a.anchors),
                   ("Expansion layers", BLUE, a.expansion),
                   ("Retention risks", STATUS_STYLE[RED][1], a.risks)])

    doc.add_heading("Module and capability usage", level=1)
    _subtitle(doc, "Observed adoption versus value-realisation opportunity")
    _data_table(doc, ["Capability", "Observed usage", "Health", "Value motion"], capability_rows(a),
                [3.2, 6.8, 2.2, 5.5], health_col=2)

    doc.add_heading("Edwin AI readiness", level=1)
    _subtitle(doc, "Technical entry criteria that can be evidenced from the API")
    _status_banner(doc, a.readiness_status, a.readiness_text)
    doc.add_heading("Entry criteria", level=2)
    _data_table(doc, ["Criterion", "Status", "Evidence"], a.readiness, [4.2, 2.2, 11.3], status_col=1)
    _para(doc, "Business baselines (tickets, workforce effort, MTTD/MTTR, SLA performance, cost per ticket) are not "
               "held in LogicMonitor and must be agreed with the customer before a proof of value.", 9.5, GREY)
    doc.add_heading("Recommended use cases and evidence", level=2)
    _data_table(doc, ["Use case", "Purpose", "Evidence to capture"], a.use_cases, [4.2, 7.5, 6.0])
    doc.add_heading("Three-week execution model", level=2)
    _data_table(doc, ["Horizon", "Priority actions", "Exit evidence"], [
        ("Week 0 - readiness", "Lock scope, baseline, technical access, RBAC, architecture, owners and exit criteria.",
         "Signed start checklist and approved charter."),
        ("Week 1 - connect", "Connect Edwin to the LM environment, validate data quality and select representative alert clusters.",
         "Verified data flow and accepted test set."),
        ("Week 2 - assess and tune", "Run event grouping and investigations, tune and compare results with manual operations.",
         "Measured quality, time and workload evidence."),
        ("Week 3 - playback", "Quantify workload, MTTR, incident quality and SLA impact; present findings.",
         "Production decision, scope and commercial gate."),
    ], [3.6, 8.6, 5.5])
    doc.add_heading("Core success measures", level=2)
    _tiles(doc, [
        ("Alert-to-insight", "", "Noise reduction", "Raw events grouped without losing SLA evidence"),
        ("Minutes saved", "", "Triage efficiency", "Manual investigation time reclaimed"),
        ("MTTR / SLA", "", "Service performance", "Priority-level improvement and risk reduction"),
        ("Ticket quality", "", "ITSM enrichment", "Better context, handoff and RCA consistency"),
        ("Hours / FTE", "", "Capacity reclaimed", "Engineering effort redirected to higher-value work"),
        ("Go / no-go", "", "Production gate", "Evidence-based scope and commercial decision"),
    ])

    doc.add_heading("30 / 60 / 90-day action plan", level=1)
    _subtitle(doc, "Generated from the findings; sequence foundational fixes before broader AIOps scale")
    _data_table(doc, ["Horizon", "Priority actions", "Exit evidence"], a.plan, [2.6, 10.1, 5.0])
    doc.add_heading("Decisions required", level=2)
    _numbered(doc, [
        "Which business KPI will justify further investment: FTE capacity, cost per ticket, MTTR, SLA-risk reduction, or a combination?",
        "Which two or three incident categories will be used for an Edwin AI proof of value?",
        "Which alerts must remain for contractual reporting, and which are expected to drive operational action?",
        "Who owns each health-check recommendation, with committed completion dates?",
        "What technical readiness evidence is required before AIOps activation (collector health, topology, metadata, RBAC, ITSM access)?",
        "What is the production decision gate after a proof of value, and who approves it?",
    ])
    _callout(doc, "Recommended positioning",
             "Convert the telemetry already being collected into fewer actionable situations, faster investigation, "
             "measurable SLA improvement and lower cost-to-serve, after the foundational findings above are closed.")

    doc.add_heading("Sources, assumptions and validation points", level=1)
    _subtitle(doc, "Evidence used for this health check")
    _source(doc, "[1]", f"LogicMonitor REST API v3 - {facts.portal_host}",
            f"Read-only GET requests on {when.strftime('%d %B %Y')}. Endpoints: " + ", ".join(facts.endpoints) +
            ". Metrics are point-in-time.")
    contract = {k: v for k, v in facts.contract.items() if v}
    if contract:
        labels = {"devices": "devices", "logs_gb": "GB logs", "uptime_checks": "uptime checks"}
        _source(doc, "[2]", "Contract entitlements supplied by the operator",
                "Entered on the command line: " + ", ".join(f"{n_(v)} {labels.get(k, k)}" for k, v in contract.items()) + ".")
    doc.add_heading("Validation points before customer use", level=2)
    _bullets(doc, [
        "A configuration count does not prove active adoption. Dashboard views, report recipients, service usage and query execution are not exposed by the API.",
        "Zero cloud, SaaS or Kubernetes resources in this portal does not prove those workloads are absent; they may be monitored elsewhere or excluded from scope.",
        "Portal metrics are a current operational snapshot. Contract, billing and renewal decisions should use formal usage reports.",
        "Clone and unused-module figures are name and AppliesTo heuristics; confirm before retiring any module.",
    ])

    doc.add_heading("Appendix A - Portal evidence", level=1)
    _subtitle(doc, "API data supporting the key usage and risk observations")
    if a.usage_rows:
        _data_table(doc, ["Usage counter (/metrics/usage)", "Value"], a.usage_rows, [11.7, 6.0])
        _caption(doc, "Figure A1. Resource usage counters reported by the portal.")
    if a.alert_severity or a.top_alert_sources:
        sev_rows = [(k, n_(v)) for k, v in sorted(a.alert_severity.items())]
        _data_table(doc, ["Open alerts by severity", "Count"], sev_rows, [11.7, 6.0])
        _data_table(doc, ["Top open-alert sources (LogicModule)", "Open alerts"],
                    [(name, n_(count)) for name, count in a.top_alert_sources], [11.7, 6.0])
        _caption(doc, "Figure A2. Open alerts by severity and top alert sources.")
    if a.collector_rows:
        _data_table(doc, ["Collector", "State", "Size", "Build", "Devices", "Instances", "Failover", "Group"],
                    a.collector_rows, [3.4, 1.3, 2.1, 1.6, 1.7, 2.0, 1.8, 3.8])
        suffix = " (top 100 by instance load)" if len(a.facts.get("collectors") or []) > 100 else ""
        _caption(doc, f"Figure A3. Collector estate{suffix}.")
    if a.module_rows:
        _data_table(doc, ["LogicModule type", "Installed", "Likely clones", "Not applied"], a.module_rows,
                    [5.7, 4.0, 4.0, 4.0])
        _caption(doc, "Figure A4. LogicModule inventory.")
    if a.audit_rows:
        _data_table(doc, ["Audit-log category (last 24 hours)", "Entries"], [(k, n_(v)) for k, v in a.audit_rows],
                    [11.7, 6.0])
        _caption(doc, "Figure A5. Audit-log activity by category.")

    doc.add_heading("Appendix B - Check coverage", level=1)
    _subtitle(doc, "What this report could and could not compute from the read-only API")
    coverage_rows = [(c.area, c.coverage, c.coverage_note or "Computed from API data.") for c in a.checks]
    coverage_rows.append(("Edwin AI usage", "Not available from API", "Edwin AI usage is not exposed by the REST API."))
    for key, reason in sorted(facts.gaps.items()):
        coverage_rows.append((f"Data: {key}", "Not collected", reason[:1].upper() + reason[1:] + "."))
    _data_table(doc, ["Check", "Coverage", "Note"], coverage_rows, [5.0, 3.7, 9.0])

    _normalise(doc)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / report_filename(facts.portal_name, when)
    doc.save(str(path))
    return path


def n_(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{number:,.0f}" if number.is_integer() else f"{number:,.1f}"
