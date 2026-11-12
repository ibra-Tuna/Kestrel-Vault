"""Themes, accent colours and the stylesheet builder."""
from __future__ import annotations

from pathlib import Path
from string import Template

from PySide6.QtGui import QColor

from . import icons

PAD = 20   # outer page padding
GAP = 12   # gap between sibling controls

THEMES: dict[str, dict[str, str]] = {
    "Dark":          {"bg": "#0f1115", "fg": "#e8ecf1", "panel": "#181b22", "border": "#2a2f3a", "sub": "#8a93a3"},
    "Light":         {"bg": "#f4f6f9", "fg": "#1a1d23", "panel": "#ffffff", "border": "#e0e4ea", "sub": "#5b6270"},
    "Midnight":      {"bg": "#0a0e14", "fg": "#c9d1d9", "panel": "#131820", "border": "#21262d", "sub": "#8590a0"},
    "Nord":          {"bg": "#242933", "fg": "#eceff4", "panel": "#2e3440", "border": "#434c5e", "sub": "#9099aa"},
    "Solarized":     {"bg": "#001f28", "fg": "#93a1a1", "panel": "#002b36", "border": "#0d3a44", "sub": "#7b9097"},
    "Rosé Pine":     {"bg": "#14121f", "fg": "#e0def4", "panel": "#1f1d2e", "border": "#2e2b3f", "sub": "#908caa"},
    "Dracula":       {"bg": "#21222c", "fg": "#f8f8f2", "panel": "#282a36", "border": "#44475a", "sub": "#9a9eb8"},
    "Gruvbox":       {"bg": "#1d2021", "fg": "#ebdbb2", "panel": "#282828", "border": "#3c3836", "sub": "#a89984"},
    "One Dark":      {"bg": "#1e2127", "fg": "#abb2bf", "panel": "#282c34", "border": "#3e4451", "sub": "#7f8796"},
    "Catppuccin":    {"bg": "#181825", "fg": "#cdd6f4", "panel": "#1e1e2e", "border": "#313244", "sub": "#a6adc8"},
    "Tokyo Night":   {"bg": "#16161e", "fg": "#c0caf5", "panel": "#1a1b26", "border": "#2a2e44", "sub": "#8a9ad0"},
    "High Contrast": {"bg": "#000000", "fg": "#ffffff", "panel": "#111111", "border": "#ffff00", "sub": "#e0e000"},
}

ACCENTS: list[tuple[str, str]] = [
    ("Blue", "#3b82f6"), ("Indigo", "#6366f1"), ("Purple", "#a855f7"),
    ("Rose", "#f43f5e"), ("Red", "#ef4444"), ("Orange", "#f97316"),
    ("Amber", "#f59e0b"), ("Green", "#22c55e"), ("Teal", "#14b8a6"),
    ("Cyan", "#06b6d4"),
]

_TEMPLATE_PATH = Path(__file__).with_name("style.qss.tpl")


def lighten(hex_color: str, amount: int = 15) -> str:
    c = QColor(hex_color)
    h, s, l, a = c.getHsl()
    c.setHsl(h, s, min(255, l + amount), a)
    return c.name()


def darken(hex_color: str, amount: int = 15) -> str:
    c = QColor(hex_color)
    h, s, l, a = c.getHsl()
    c.setHsl(h, s, max(0, l - amount), a)
    return c.name()


def is_dark(hex_color: str) -> bool:
    return QColor(hex_color).lightness() < 128


def on_color(hex_color: str) -> str:
    """Readable text colour to place on top of `hex_color`."""
    return "#ffffff" if QColor(hex_color).lightness() < 150 else "#111111"


def tokens(theme_name: str, accent: str, cache_dir: Path) -> dict[str, str]:
    t = THEMES.get(theme_name, THEMES["Dark"])
    dark = is_dark(t["bg"])
    tok = dict(t)
    tok.update(
        accent=accent,
        on_accent=on_color(accent),
        accent_hover=lighten(accent, 14) if dark else darken(accent, 8),
        accent_press=darken(accent, 14),
        panel_hover=lighten(t["panel"], 14) if dark else darken(t["panel"], 6),
        border_hover=lighten(t["border"], 16) if dark else darken(t["border"], 12),
        card=lighten(t["panel"], 4) if dark else t["panel"],
        danger="#ef4444", danger_hover="#dc2626",
        success="#22c55e", warning="#f59e0b",
        is_dark="1" if dark else "0",
    )
    # QSS cannot draw check marks or chevrons itself, so render small PNGs.
    key = tok["on_accent"].lstrip("#") + t["sub"].lstrip("#")
    tok["check_icon"] = icons.export_png("check", tok["on_accent"], 14, cache_dir / f"check_{key}.png")
    tok["chevron_icon"] = icons.export_png("chevron-down", t["sub"], 14, cache_dir / f"chevron_{key}.png")
    return tok


def build_stylesheet(tok: dict[str, str]) -> str:
    return Template(_TEMPLATE_PATH.read_text(encoding="utf-8")).safe_substitute(tok)
