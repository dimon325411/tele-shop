"""PNG charts for Telegram (matplotlib, headless)."""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

INK, MUTED, ACCENT, BAND, GRID = "#1f2328", "#6e7781", "#2563eb", "#bfd3fb", "#e5e7eb"


def _fmt_pln(v: float, _pos=None) -> str:
    return f"{v:,.0f}".replace(",", " ")


def _style(ax, title: str, subtitle: str) -> None:
    ax.set_title(title, loc="left", fontsize=13, color=INK, fontweight="bold", pad=22)
    ax.text(0, 1.02, subtitle, transform=ax.transAxes, fontsize=9, color=MUTED)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(_fmt_pln))


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def history_chart(rows: list[dict], title: str, subtitle: str, currency: str = "PLN") -> bytes:
    """Median line with the 25–75 % band over days. ``rows``: dicts with day, median, p25, p75, count."""
    days = [r["day"] if isinstance(r["day"], date) else date.fromisoformat(str(r["day"])) for r in rows]
    med = [float(r["median"]) for r in rows]
    lo = [float(r["p25"]) for r in rows]
    hi = [float(r["p75"]) for r in rows]
    fig, ax = plt.subplots(figsize=(8, 4.2))
    ax.fill_between(days, lo, hi, color=BAND, alpha=0.6, linewidth=0, label="50% ofert (25–75 percentyl)")
    ax.plot(days, med, color=ACCENT, linewidth=2.2, marker="o" if len(days) < 20 else None, markersize=4,
            label="mediana")
    ax.annotate(f"{_fmt_pln(med[-1])} {currency}", (days[-1], med[-1]), textcoords="offset points",
                xytext=(6, 6), fontsize=9, color=ACCENT, fontweight="bold")
    _style(ax, title, subtitle)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    ax.set_ylabel(currency, color=MUTED, fontsize=9)
    ax.legend(frameon=False, fontsize=8, loc="upper right", labelcolor=MUTED)
    fig.autofmt_xdate(rotation=0, ha="center")
    return _png(fig)


def distribution_chart(prices: list[Decimal], title: str, subtitle: str, currency: str = "PLN") -> bytes:
    """Histogram of current prices with the median marked."""
    values = sorted(float(p) for p in prices)
    med = values[len(values) // 2] if len(values) % 2 else (values[len(values) // 2 - 1] + values[len(values) // 2]) / 2
    fig, ax = plt.subplots(figsize=(8, 4.2))
    bins = min(20, max(5, len(values) // 3))
    ax.hist(values, bins=bins, color=BAND, edgecolor="white", linewidth=1)
    ax.axvline(med, color=ACCENT, linewidth=2)
    ax.text(med, ax.get_ylim()[1] * 0.95, f" mediana {_fmt_pln(med)} {currency}", color=ACCENT, fontsize=9,
            fontweight="bold", va="top")
    _style(ax, title, subtitle)
    ax.xaxis.set_major_formatter(plt.FuncFormatter(_fmt_pln))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _p: f"{v:.0f}"))
    ax.set_xlabel(currency, color=MUTED, fontsize=9)
    ax.set_ylabel("liczba ofert", color=MUTED, fontsize=9)
    return _png(fig)
