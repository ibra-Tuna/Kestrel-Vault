"""Themes, accent colours and the stylesheet builder."""
from __future__ import annotations

from pathlib import Path
from string import Template

from PySide6.QtGui import QColor          # FIX: QColor was never imported

from . import icons

PAD = 24   # outer page padding (8px grid)
GAP = 12   # gap between sibling controls

THEMES: dict[str, dict[str, str]] = {
    "Dark":          {"bg": "#0d1016", "rail": "#0a0c11", "fg": "#e8eaed", "panel": "#131720", "elevated": "#181d27", "field": "#0d1017", "border": "#242a36", "sub": "#8e96a4"},
    "Light":         {"bg": "#f4f6f9", "rail": "#ebeef3", "fg": "#1a1d23", "panel": "#ffffff", "elevated": "#ffffff", "field": "#f7f9fc", "border": "#dce1e8", "sub": "#525a66"},
    "Midnight":      {"bg": "#0a0e14", "fg": "#c9d1d9", "panel": "#131820", "border": "#21262d", "sub": "#8b949e"},
    "Nord":          {"bg": "#242933", "fg": "#eceff4", "panel": "#2e3440", "border": "#434c5e", "sub": "#9aa3b4"},
    "Solarized":     {"bg": "#001f28", "fg": "#93a1a1", "panel": "#002b36", "border": "#0d3a44", "sub": "#839496"},
    "Rosé Pine":     {"bg": "#14121f", "fg": "#e0def4", "panel": "#1f1d2e", "border": "#2e2b3f", "sub": "#908caa"},
    "Dracula":       {"bg": "#21222c", "fg": "#f8f8f2", "panel": "#282a36", "border": "#44475a", "sub": "#a4a8c2"},
    "Gruvbox":       {"bg": "#1d2021", "fg": "#ebdbb2", "panel": "#282828", "border": "#3c3836", "sub": "#a89984"},
    "One Dark":      {"bg": "#1e2127", "fg": "#abb2bf", "panel": "#282c34", "border": "#3e4451", "sub": "#8a92a1"},
    "Catppuccin":    {"bg": "#181825", "fg": "#cdd6f4", "panel": "#1e1e2e", "border": "#313244", "sub": "#a6adc8"},
    "Tokyo Night":   {"bg": "#16161e", "fg": "#c0caf5", "panel": "#1a1b26", "border": "#2a2e44", "sub": "#8a9ad0"},
    "High Contrast": {"bg": "#000000", "fg": "#ffffff", "panel": "#111111", "border": "#ffff00", "sub": "#e0e000"},
}

ACCENTS: list[tuple[str, str]] = [
    ("Blue", "#4f8ef7"), ("Violet", "#7c5cff"), ("Indigo", "#6366f1"),
    ("Rose", "#f43f5e"), ("Red", "#ff453a"), ("Orange", "#ff9f0a"),
    ("Amber", "#f59e0b"), ("Green", "#34c759"), ("Teal", "#14b8a6"),
    ("Cyan", "#06b6d4"),
]

_TEMPLATE_PATH = Path(__file__).with_name("style.qss.tpl")


def blend(hex_a: str, hex_b: str, ratio_b: float) -> str:
    """Linearly blend two colours (`ratio_b` in 0..1)."""
    ca, cb = QColor(hex_a), QColor(hex_b)
    r = round(ca.red()   * (1 - ratio_b) + cb.red()   * ratio_b)
    g = round(ca.green() * (1 - ratio_b) + cb.green() * ratio_b)
    b = round(ca.blue()  * (1 - ratio_b) + cb.blue()  * ratio_b)
    return QColor(r, g, b).name()


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
    rail = t.get("rail") or (darken(t["bg"], 6) if dark else darken(t["bg"], 8))
    elevated = t.get("elevated") or (lighten(t["panel"], 6) if dark else t["panel"])
    field = t.get("field") or (darken(t["panel"], 6) if dark else darken(t["panel"], 4))
    nav_active_bg = blend(rail, accent, 0.28 if dark else 0.16)
    nav_active_fg = "#ffffff" if dark else darken(accent, 28)
    row_selected_bg = blend(t["panel"], accent, 0.28 if dark else 0.16)
    row_selected_fg = "#ffffff" if dark else t["fg"]
    danger = "#ff453a"
    success = "#34c759"
    warning = "#ff9f0a"
    tok.update(
        accent=accent,
        on_accent=on_color(accent),
        accent_hover=lighten(accent, 14) if dark else darken(accent, 8),
        accent_press=darken(accent, 14),
        rail=rail,
        field=field,
        nav_active_bg=nav_active_bg,
        nav_active_fg=nav_active_fg,
        row_selected_bg=row_selected_bg,
        row_selected_fg=row_selected_fg,
        panel_hover=lighten(t["panel"], 10) if dark else darken(t["panel"], 6),
        border_hover=lighten(t["border"], 16) if dark else darken(t["border"], 12),
        card=elevated,
        elevated=elevated,
        danger=danger, danger_hover="#e0352b",
        danger_tint=blend(t["panel"], danger, 0.14),
        danger_border=blend(t["panel"], danger, 0.55),
        success=success,
        success_tint=blend(t["panel"], success, 0.14),
        success_border=blend(t["panel"], success, 0.40),
        warning=warning,
        warning_tint=blend(t["panel"], warning, 0.14),
        warning_border=blend(t["panel"], warning, 0.40),
        neutral_toast="#3b4252",
        is_dark="1" if dark else "0",
    )

    # FIX: ensure the icon cache directory exists before writing PNGs into it
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    # QSS cannot draw check marks or chevrons itself, so render small PNGs.
    key = tok["on_accent"].lstrip("#") + t["sub"].lstrip("#")
    tok["check_icon"]   = icons.export_png("check",        tok["on_accent"], 14, cache_dir / f"check_{key}.png")
    tok["chevron_icon"] = icons.export_png("chevron-down", t["sub"],         14, cache_dir / f"chevron_{key}.png")
    return tok


def build_stylesheet(tok: dict[str, str]) -> str:
    return Template(_TEMPLATE_PATH.read_text(encoding="utf-8")).safe_substitute(tok)