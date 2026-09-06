"""Shared matplotlib chart rendering -- PNG output used identically by the
HTML, PDF and DOCX renderers (spec §3/§4: same look across all 3 formats,
so charts are rendered once as PNGs and simply embedded everywhere).
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")  # headless -- no GUI backend, safe for server/CLI use

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

# 8-color qualitative palette, repeating for >8 categories. Chosen to read
# distinctly in both print (PDF) and screen (HTML) contexts.
PALETTE = [
    "#1f3864",  # dark navy
    "#c00000",  # red
    "#2e7d32",  # green
    "#e07b00",  # orange
    "#6a3d9a",  # purple
    "#17a2b8",  # teal
    "#b8860b",  # ochre/mustard
    "#4682b4",  # steel blue
]

DPI = 150
FIGSIZE_WIDE = (8.5, None)  # width fixed, height computed by caller from row count


def _color_cycle(n: int) -> list[str]:
    return [PALETTE[i % len(PALETTE)] for i in range(n)]


def _format_thousands(x, _pos=None) -> str:
    return format(int(x), ",").replace(",", " ")


def horizontal_bar_chart(
    out_path: str,
    labels: list[str],
    values: list[float],
    value_format: str = "int",  # "int" | "rub"
    bar_height_in: float = 0.32,
    min_height_in: float = 1.6,
    title: str | None = None,
    single_color: str | None = None,
) -> None:
    """Horizontal bar chart, largest value at top (matches spec's recurring
    'sorted descending, value label at bar end' style used in sections
    3/4/4.1/6/7.1)."""
    n = len(labels)
    height = max(min_height_in, bar_height_in * n + 0.8)
    fig, ax = plt.subplots(figsize=(FIGSIZE_WIDE[0], height), dpi=DPI)

    y_pos = range(n)
    colors = [single_color] * n if single_color else _color_cycle(n)
    ax.barh(y_pos, values, color=colors, height=0.68)
    ax.set_yticks(list(y_pos))
    ax.set_yticklabels(labels, fontsize=9)
    ax.invert_yaxis()  # largest value at top

    max_val = max(values) if values else 0
    for i, v in enumerate(values):
        label = _format_thousands(v) + (" ₽" if value_format == "rub" else "")
        ax.text(v + max_val * 0.01, i, label, va="center", fontsize=8)

    ax.set_xlim(0, max_val * 1.18 if max_val else 1)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(_format_thousands))
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.tick_params(left=False)
    if title:
        ax.set_title(title, fontsize=11, loc="left")
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def pie_chart(
    out_path: str,
    labels: list[str],
    values: list[float],
    title: str | None = None,
) -> None:
    """Section 2 cost-structure pie: Услуги / Выезды / Запчасти shares."""
    fig, ax = plt.subplots(figsize=(6, 5), dpi=DPI)
    colors = _color_cycle(len(labels))
    total = sum(values) or 1
    wedges, _texts, autotexts = ax.pie(
        values,
        colors=colors,
        autopct=lambda pct: f"{pct:.1f}%",
        startangle=90,
        pctdistance=0.75,
    )
    for t in autotexts:
        t.set_color("white")
        t.set_fontsize(9)
    ax.legend(
        wedges,
        [f"{lbl} ({_format_thousands(v)} ₽)" for lbl, v in zip(labels, values)],
        loc="upper center",
        bbox_to_anchor=(0.5, -0.02),
        ncol=1,
        fontsize=9,
        frameon=False,
    )
    if title:
        ax.set_title(title, fontsize=11)
    ax.axis("equal")
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def stacked_vertical_bar_chart(
    out_path: str,
    x_labels: list[str],
    series: dict[str, list[float]],
    series_colors: dict[str, str] | None = None,
    title: str | None = None,
) -> None:
    """Vertical stacked bar chart -- used by the experimental section 10.1
    layout (order-status by month). Kept generic/reusable so wiring in a
    real data source later needs no chart-layer changes."""
    fig, ax = plt.subplots(figsize=(7, 4.5), dpi=DPI)
    bottoms = [0.0] * len(x_labels)
    names = list(series.keys())
    colors = series_colors or {name: PALETTE[i % len(PALETTE)] for i, name in enumerate(names)}
    for name in names:
        vals = series[name]
        ax.bar(x_labels, vals, bottom=bottoms, label=name, color=colors.get(name))
        bottoms = [b + v for b, v in zip(bottoms, vals)]
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=len(names), fontsize=9, frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    if title:
        ax.set_title(title, fontsize=11)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def threshold_colored_bar_chart(
    out_path: str,
    labels: list[str],
    values_pct: list[float],
    thresholds: tuple[float, float] = (90, 70),
    colors: tuple[str, str, str] = ("#2e7d32", "#e0a800", "#c00000"),
    title: str | None = None,
) -> None:
    """Horizontal bar chart colored by a value threshold -- used by the
    experimental section 10.2 layout (fulfilment % by geography): >=90%
    green, 70-89% yellow, <70% red."""
    hi, lo = thresholds
    bar_colors = [colors[0] if v >= hi else colors[1] if v >= lo else colors[2] for v in values_pct]
    n = len(labels)
    fig, ax = plt.subplots(figsize=(FIGSIZE_WIDE[0], max(1.6, 0.32 * n + 0.8)), dpi=DPI)
    y_pos = range(n)
    ax.barh(y_pos, values_pct, color=bar_colors, height=0.68)
    ax.set_yticks(list(y_pos))
    ax.set_yticklabels(labels, fontsize=9)
    ax.invert_yaxis()
    for i, v in enumerate(values_pct):
        ax.text(v + 1, i, f"{v:.0f}%", va="center", fontsize=8)
    ax.set_xlim(0, 105)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.tick_params(left=False)
    if title:
        ax.set_title(title, fontsize=11, loc="left")
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)
