"""Eye diagram utilities: folded traces and 2-D density histogram."""

from __future__ import annotations

import numpy as np


def eye_density(eye_traces: np.ndarray, v_bins: int = 256,
                v_range: tuple[float, float] | None = None) -> tuple[np.ndarray, np.ndarray]:
    """2-D histogram (time x voltage) from folded eye traces.

    Args:
        eye_traces: (n_traces, span) array from ``fold_eye``.

    Returns:
        (counts[v_bins, span], v_edges)
    """
    n_traces, span = eye_traces.shape
    if v_range is None:
        vmax = np.abs(eye_traces).max() * 1.05
        v_range = (-vmax, vmax)
    edges = np.linspace(v_range[0], v_range[1], v_bins + 1)
    counts = np.zeros((v_bins, span), dtype=np.int64)
    for col in range(span):
        counts[:, col] = np.histogram(eye_traces[:, col], bins=edges)[0]
    return counts, edges


def plot_eye(ax, eye_traces: np.ndarray, ui_ps: float, mode: str = "density",
             title: str = "") -> None:
    """Render an eye onto a matplotlib axes (line-fold or density heatmap)."""
    n_traces, span = eye_traces.shape
    t_ui = (np.arange(span) - span / 2) / (span / 2)  # in UI, centered
    if mode == "lines":
        for tr in eye_traces[: min(400, n_traces)]:
            ax.plot(t_ui, tr, color="C0", alpha=0.08, lw=0.5)
    else:
        counts, edges = eye_density(eye_traces)
        with np.errstate(divide="ignore"):
            img = np.log10(counts + 1)
        ax.imshow(img, aspect="auto", origin="lower", cmap="inferno",
                  extent=(t_ui[0], t_ui[-1], edges[0], edges[-1]))
    ax.set_xlabel("Time [UI]")
    ax.set_ylabel("V")
    if title:
        ax.set_title(title)
