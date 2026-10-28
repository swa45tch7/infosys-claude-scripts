#!/usr/bin/env python3
"""Appendix charts rendered with matplotlib from the collected portal data.

Each function returns PNG bytes, or None when there is nothing to draw. If
matplotlib is not installed, charts_available() is False and the report shows
a short note instead of the figure.
"""

from __future__ import annotations

import io
from typing import Any

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.ticker import FuncFormatter
except ImportError:  # pragma: no cover - exercised only without matplotlib
    plt = None

NAVY = "#071C4F"
BLUE = "#006FF2"
LIGHT_BLUE = "#9CC3FF"
MUTED = "#586273"
GRID = "#D7DEE8"
AMBER = "#E3A008"
RED = "#B3261E"
GREEN = "#2E7D32"
FIG_SIZE = (9.05, 2.5)
DPI = 200


def charts_available() -> bool:
    return plt is not None


def _style() -> None:
    installed = {f.name for f in font_manager.fontManager.ttflist}
    family = next((f for f in ("Arial", "Liberation Sans", "DejaVu Sans") if f in installed), "sans-serif")
    plt.rcParams.update(
        {
            "font.family": family,
            "font.size": 8,
            "axes.edgecolor": GRID,
            "axes.labelcolor": MUTED,
            "axes.titlesize": 9,
            "axes.titleweight": "bold",
            "axes.titlecolor": NAVY,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )


def _render(fig: Any) -> bytes:
    buffer = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buffer, format="png", dpi=DPI, facecolor="white")
    plt.close(fig)
    return buffer.getvalue()


def resource_chart(metrics: dict[str, Any], contracted: int | None = None) -> bytes | None:
    if not charts_available() or metrics.get("resources") is None:
        return None
    _style()
    fig, (left, right) = plt.subplots(1, 2, figsize=FIG_SIZE, gridspec_kw={"width_ratios": [3, 2]})
    types = dict(metrics.get("resource_types") or {})
    if metrics.get("kubernetes"):
        types["Kubernetes"] = metrics["kubernetes"]
    if metrics.get("services"):
        types["Services"] = metrics["services"]
    labels = list(types.keys()) or ["Resources"]
    values = list(types.values()) or [0]
    bars = left.barh(labels, values, color=NAVY, height=0.55)
    left.bar_label(bars, labels=[f"{v:,}" for v in values], padding=3, color=NAVY, fontsize=7)
    left.set_xlim(0, max(max(values) * 1.12, (contracted or 0) * 1.25, 1))
    left.invert_yaxis()
    services = f" + {metrics['services']:,} services" if metrics.get("services") else ""
    left.set_title(f"Monitored resources by type ({metrics['resources']:,} resources{services})", loc="left")
    left.grid(axis="x", color=GRID, linewidth=0.6)
    left.set_axisbelow(True)
    if contracted:
        left.axvline(contracted, color=RED, linestyle="--", linewidth=1)
        left.text(contracted, len(labels) - 0.4, f" contracted {contracted:,}", color=RED, fontsize=7)
    health = [
        ("Normal", metrics.get("resource_status", {}).get("Normal", 0), GREEN),
        ("Dead", metrics.get("dead", 0), RED),
        ("Root group only", metrics.get("ungrouped", 0), AMBER),
        ("Alerting disabled", metrics.get("alerting_disabled", 0), MUTED),
        ("In SDT", metrics.get("in_sdt", 0), LIGHT_BLUE),
    ]
    names = [h[0] for h in health]
    counts = [h[1] for h in health]
    bars = right.barh(names, counts, color=[h[2] for h in health], height=0.55)
    right.bar_label(bars, labels=[f"{v:,}" for v in counts], padding=3, color=NAVY, fontsize=7)
    right.set_xlim(0, max(max(counts) * 1.12, 1))
    right.invert_yaxis()
    right.set_title("Resource hygiene", loc="left")
    right.grid(axis="x", color=GRID, linewidth=0.6)
    right.set_axisbelow(True)
    return _render(fig)


def alert_chart(metrics: dict[str, Any]) -> bytes | None:
    hourly = metrics.get("alerts_hourly")
    if not charts_available() or not hourly:
        return None
    _style()
    fig, ax = plt.subplots(figsize=FIG_SIZE)
    hours = list(range(24))
    bottom = [0] * 24
    for severity, colour in (("warn", AMBER), ("error", "#E8710A"), ("critical", RED)):
        values = hourly.get(severity) or [0] * 24
        ax.bar(hours, values, bottom=bottom, color=colour, width=0.8, label=severity.capitalize())
        bottom = [b + v for b, v in zip(bottom, values)]
    ax.set_ylim(0, max(max(bottom) * 1.25, 1))
    ax.set_xticks(hours)
    ax.set_xticklabels([f"-{24 - h}h" for h in hours], fontsize=6)
    ax.set_ylabel("Alerts per hour")
    ax.set_title(
        f"Alerts raised in the 24 hours before collection ({metrics.get('alerts_24h', 0):,} total)", loc="left"
    )
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, ncol=3, fontsize=7, loc="upper right")
    return _render(fig)


def website_chart(metrics: dict[str, Any]) -> bytes | None:
    if not charts_available() or not metrics.get("websites"):
        return None
    _style()
    fig, (left, right) = plt.subplots(1, 2, figsize=FIG_SIZE)
    types = metrics.get("websites_by_type") or {}
    labels = [k.replace("check", " check").capitalize() for k in types] or ["Checks"]
    values = list(types.values()) or [0]
    bars = left.bar(labels, values, color=[NAVY, BLUE, LIGHT_BLUE][: len(values)], width=0.5)
    left.bar_label(bars, labels=[f"{v:,}" for v in values], padding=2, color=NAVY, fontsize=7)
    left.set_ylim(0, max(max(values) * 1.2, 1))
    left.set_title("Website checks by type", loc="left")
    left.grid(axis="y", color=GRID, linewidth=0.6)
    left.set_axisbelow(True)
    states = [
        ("External", metrics.get("websites_external", 0), BLUE),
        ("Internal", metrics.get("websites_internal", 0), NAVY),
        ("Alerting", metrics.get("websites_alerting", 0), RED),
        ("Stopped", metrics.get("websites_stopped", 0), MUTED),
    ]
    bars = right.bar([s[0] for s in states], [s[1] for s in states], color=[s[2] for s in states], width=0.5)
    right.bar_label(bars, labels=[f"{s[1]:,}" for s in states], padding=2, color=NAVY, fontsize=7)
    right.set_ylim(0, max(max(s[1] for s in states) * 1.2, 1))
    right.set_title("Checkpoint location and state", loc="left")
    right.grid(axis="y", color=GRID, linewidth=0.6)
    right.set_axisbelow(True)
    return _render(fig)


def collector_chart(rows: list[dict[str, Any]], limit: int = 40) -> bytes | None:
    if not charts_available() or not rows:
        return None
    _style()
    shown = rows[:limit]
    fig, ax = plt.subplots(figsize=(9.05, 3.55))
    colours = [RED if r["down"] else AMBER if not r["failover"] and r["hosts"] else NAVY for r in shown]
    positions = range(len(shown))
    ax.bar(positions, [r["instances"] for r in shown], color=colours, width=0.7)
    peak = max([r["instances"] for r in shown] + [1])
    for position, row in zip(positions, shown):
        if row["down"]:
            ax.text(position, row["instances"] + peak * 0.02, "down", color=RED, fontsize=6, ha="center")
    ax.set_ylim(0, peak * 1.2)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))
    ax.set_xticks(list(positions))
    ax.set_xticklabels([r["name"][:22] for r in shown], rotation=60, ha="right", fontsize=5.5)
    ax.set_ylabel("Monitored instances")
    title = "Instances per collector"
    if len(rows) > limit:
        title += f" (busiest {limit} of {len(rows)})"
    ax.set_title(title, loc="left")
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    handles = [
        plt.Rectangle((0, 0), 1, 1, color=NAVY),
        plt.Rectangle((0, 0), 1, 1, color=AMBER),
        plt.Rectangle((0, 0), 1, 1, color=RED),
    ]
    ax.legend(handles, ["Up with failover", "No failover", "Down"], frameon=False, fontsize=7, ncol=3, loc="upper right")
    return _render(fig)
