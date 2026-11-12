"""Vector icon set rendered from inline SVG and tinted to the active theme.

Replaces the emoji used before, which render differently (or as empty boxes)
depending on the operating system and font set.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_BODIES: dict[str, str] = {
    "shield": '<path d="M12 3l8 3v6c0 4.5-3.2 8.2-8 9-4.8-.8-8-4.5-8-9V6z"/>',
    "lock": '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    "vault": '<rect x="3" y="4" width="18" height="16" rx="3"/><circle cx="12" cy="12" r="3.5"/><path d="M12 8.5V10M12 14v1.5M8.5 12H10M14 12h1.5"/>',
    "dice": '<rect x="4" y="4" width="16" height="16" rx="3"/><circle cx="9" cy="9" r="1.1" fill="COLOR"/><circle cx="15" cy="9" r="1.1" fill="COLOR"/><circle cx="9" cy="15" r="1.1" fill="COLOR"/><circle cx="15" cy="15" r="1.1" fill="COLOR"/>',
    "sliders": '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
    "activity": '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
    "eye": '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    "eye-off": '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/><path d="M4 4l16 16"/>',
    "copy": '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15H4a1 1 0 0 1-1-1V5a2 2 0 0 1 2-2h9a1 1 0 0 1 1 1v1"/>',
    "external": '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    "star": '<path d="M12 3l2.8 5.9 6.2.8-4.6 4.3 1.2 6.3L12 17.2 6.4 20.3l1.2-6.3L3 9.7l6.2-.8z"/>',
    "star-fill": '<path fill="COLOR" d="M12 3l2.8 5.9 6.2.8-4.6 4.3 1.2 6.3L12 17.2 6.4 20.3l1.2-6.3L3 9.7l6.2-.8z"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "search": '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
    "trash": '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/>',
    "edit": '<path d="M4 20l1-4L16 5a2.1 2.1 0 0 1 3 3L8 19z"/><path d="M14 7l3 3"/>',
    "check": '<path d="M5 12l5 5 9-10"/>',
    "refresh": '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/>',
    "library": '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "tag": '<path d="M3 12V4h8l10 10-8 8z"/><circle cx="7.5" cy="8.5" r="1"/>',
    "download": '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
    "upload": '<path d="M12 20V9M7 14l5-5 5 5M5 4h14"/>',
    "key": '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M16 7l3 3"/>',
    "x": '<path d="M6 6l12 12M18 6L6 18"/>',
    "warning": '<path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18v.5"/>',
    "undo": '<path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "chevron-down": '<path d="M6 9l6 6 6-6"/>',
    "file": '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8v.5"/>',
}

_cache: dict[tuple, QPixmap] = {}


def _svg(name: str, color: str) -> bytes:
    body = _BODIES[name].replace("COLOR", color)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        f"{body}</svg>"
    ).encode("utf-8")


def pixmap(name: str, color: str, size: int = 18, dpr: float = 2.0) -> QPixmap:
    key = (name, color, size, dpr)
    hit = _cache.get(key)
    if hit is not None:
        return hit
    renderer = QSvgRenderer(QByteArray(_svg(name, color)))
    px = int(size * dpr)
    img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, px, px))
    painter.end()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(dpr)
    _cache[key] = pm
    return pm


def icon(name: str, color: str, size: int = 18) -> QIcon:
    return QIcon(pixmap(name, color, size))


def export_png(name: str, color: str, size: int, path: Path) -> str:
    """Write an icon to disk for use from QSS `image: url(...)` rules."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pixmap(name, color, size, dpr=2.0).save(str(path), "PNG")
    return path.as_posix()
